import json
import os
import threading
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from speak.providers import CloudProvider, FreePriceGuard, RecognitionError, Result, SENSEVOICE, QWEN, price_is_free
from speak.storage import History, SecretStore, Settings, apply_replacements
from speak.recording import Recorder, RATE, split_wav
from speak.prepare import extract_checked


def price_html(model=SENSEVOICE, price="免费"):
    return f'<section><div class="row"><div><a href="https://cloud.siliconflow.cn/models?target={model}" title="{model}">SenseVoice</a></div><div><span>{price}</span></div></div></section>'


@pytest.mark.parametrize("html,expected", [
    (price_html(), True),
    (price_html(price="¥ 0.10"), False),
    (price_html(price="免费额度"), False),
    (price_html(price="免费") + price_html(price="¥ 0.10"), False),
    (price_html(model=QWEN), False),
    ("<div>免费</div>" + price_html(price="¥ 1"), False),
    (price_html(price="免费") .replace("<span>免费</span>", "<span>免费</span><span>限时试用</span>"), False),
    ("<html>服务暂不可用</html>", False),
])
def test_official_price_never_confuses_trial_or_neighbouring_model(html, expected):
    assert price_is_free(html, SENSEVOICE) is expected


def test_price_checked_once_per_process_day_and_fails_closed(tmp_path, monkeypatch):
    calls = []
    def get(*a, **kw):
        calls.append(1)
        return SimpleNamespace(content=price_html().encode(), raise_for_status=lambda: None)
    guard = FreePriceGuard(tmp_path, SimpleNamespace(get=get))
    guard.check(); guard.check()
    assert len(calls) == 1
    monkeypatch.setattr("speak.providers.shanghai_day", lambda: "2099-01-02")
    guard.check()
    assert len(calls) == 2
    assert json.loads((tmp_path / "price-check.json").read_text("utf-8"))["free"]


def test_network_failure_prevents_cloud_upload(tmp_path, monkeypatch):
    def fail(*a, **kw): raise requests.Timeout()
    cloud = CloudProvider(SimpleNamespace(load=lambda: "fake"), FreePriceGuard(tmp_path, SimpleNamespace(get=fail)))
    uploaded = []
    monkeypatch.setattr(requests, "post", lambda *a, **kw: uploaded.append(1))
    with pytest.raises(RecognitionError, match="无法核实"):
        cloud.transcribe(tmp_path / "not-opened.wav", [], threading.Event())
    assert not uploaded


@pytest.mark.parametrize("status,code", [(401,"key"),(403,"permission"),(402,"quota"),(429,"limit"),(503,"service")])
def test_cloud_failures_keep_recording_without_exposing_response(tmp_path, monkeypatch, status, code):
    audio = tmp_path / "recording.wav"; audio.write_bytes(b"recording")
    response = SimpleNamespace(status_code=status, text="secret account data")
    monkeypatch.setattr(requests, "post", lambda *a, **kw: response)
    cloud = CloudProvider(SimpleNamespace(load=lambda: "private-key"), SimpleNamespace(check=lambda model: None))
    with pytest.raises(RecognitionError) as exc:
        cloud.transcribe(audio, [], threading.Event())
    assert exc.value.code == code
    assert "private-key" not in str(exc.value) and "secret" not in str(exc.value)
    assert audio.exists()


def test_unvalidated_qwen_cannot_be_enabled_by_config(tmp_path):
    cloud = CloudProvider(SimpleNamespace(load=lambda: ""))
    with pytest.raises(RecognitionError) as exc:
        cloud.transcribe(tmp_path / "none", [], threading.Event(), QWEN)
    assert exc.value.code == "unvalidated"


def test_cancelled_cloud_response_cannot_be_used(tmp_path, monkeypatch):
    audio = tmp_path / "recording.wav"; audio.write_bytes(b"recording")
    cancelled = threading.Event()
    def post(*a, **kw):
        cancelled.set()
        return SimpleNamespace(status_code=200, json=lambda: {"text":"late response"})
    monkeypatch.setattr(requests, "post", post)
    cloud = CloudProvider(SimpleNamespace(load=lambda: "fake"), SimpleNamespace(check=lambda model: None))
    with pytest.raises(RecognitionError) as exc:
        cloud.transcribe(audio, [], cancelled)
    assert exc.value.code == "cancelled"


def test_history_retention_and_clear(tmp_path):
    history = History(tmp_path)
    history.add("new", Result("不要修改 API。", "cloud", SENSEVOICE, 1))
    history.add("old", Result("old", "local", "test", 2))
    with history.connect() as db:
        db.execute("UPDATE results SET created=? WHERE task_id='old'", (time.time()-8*86400,))
    old_audio = history.audio_dir / "old.wav"; old_audio.write_bytes(b"old")
    os.utime(old_audio, (time.time()-90000, time.time()-90000))
    fresh = history.audio_dir / "new.wav"; fresh.write_bytes(b"new")
    history.clean()
    assert [r[0] for r in history.list()] == ["new"]
    assert not old_audio.exists() and fresh.exists()
    history.clear()
    assert not history.list() and not list(history.audio_dir.iterdir())


def test_dpapi_roundtrip_and_no_plaintext(tmp_path):
    secret = SecretStore(tmp_path); secret.save("test-secret-only")
    assert secret.load() == "test-secret-only"
    assert b"test-secret-only" not in secret.path.read_bytes()
    secret.delete(); assert not secret.load()


def test_settings_and_conservative_rules(tmp_path):
    settings = Settings(); settings.save(tmp_path)
    assert Settings.load(tmp_path) == settings
    assert apply_replacements("不要改 API，不是 ROP。", {}) == "不要改 API，不是 ROP。"
    assert apply_replacements("AA BB", {"AA":"BB", "BB":"CC"}) == "BB CC"
    assert apply_replacements("Python notion n", {"n":"notion"}) == "Python notion n"
    assert apply_replacements("API CAPITable API2 API", {"API":"接口"}) == "接口 CAPITable API2 接口"


def test_bounded_recording_segments_and_final_overlap(tmp_path):
    segments = []
    recorder = Recorder(tmp_path / "all.wav", on_segment=segments.append)
    recorder.thread = threading.Thread(target=recorder._write)
    recorder.thread.start()
    # 65 seconds without accessing the real microphone.
    for second in range(65): recorder.queue.put((second + 1).to_bytes(2, "little") * RATE)
    recorder.closed.set(); recorder.thread.join(5)
    assert not recorder.thread.is_alive() and recorder.error is None
    with wave.open(str(recorder.path)) as wav: assert wav.getnframes() == RATE*65
    lengths = []
    for path in segments:
        with wave.open(str(path)) as wav: lengths.append(wav.getnframes() / RATE)
    assert lengths == [8] + [10] * 7 + [3]
    # Reconstruct original PCM after removing the exact overlap. No audio is lost.
    reconstructed = bytearray()
    for index, path in enumerate(segments):
        with wave.open(str(path)) as wav:
            data = wav.readframes(wav.getnframes())
        reconstructed.extend(data if index == 0 else data[RATE * 2 * 2:])
    with wave.open(str(recorder.path)) as wav:
        assert reconstructed == wav.readframes(wav.getnframes())


@pytest.mark.parametrize("seconds,expected", [(60,[60]),(65,[60,9]),(120,[60,60,8]),(300,[60,60,60,60,60,20])])
def test_cloud_to_local_retry_does_not_truncate_long_audio(tmp_path, seconds, expected):
    path = tmp_path / "source.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        for _ in range(seconds): wav.writeframes(b"\0\0"*RATE)
    lengths=[]
    for segment in split_wav(path, tmp_path):
        with wave.open(str(segment)) as wav: lengths.append(wav.getnframes()/RATE)
    assert lengths == expected


def test_archive_traversal_rejected(tmp_path):
    import zipfile
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w") as z: z.writestr("../outside", "bad")
    with pytest.raises(ValueError): extract_checked(path, tmp_path / "safe")
    assert not (tmp_path / "outside").exists()
