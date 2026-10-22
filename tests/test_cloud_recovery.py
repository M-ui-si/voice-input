"""Cloud fault recovery: bounded retries, cancellation, retained audio and editor identity."""
import queue
import threading
import wave
from types import SimpleNamespace

import pytest

from speak.controller import Controller, Task
from speak.providers import CloudProvider, RecognitionError, SENSEVOICE
from speak.storage import Settings


@pytest.fixture
def audio(tmp_path):
    path = tmp_path / "recording.wav"
    with wave.open(str(path), "wb") as out:
        out.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        out.writeframes(b"\x01\x00" * 1600)
    return path


def provider():
    return CloudProvider(SimpleNamespace(load=lambda: "private-key"),
                         SimpleNamespace(check=lambda model: None))


def response(status, closed):
    return SimpleNamespace(status_code=status, json=lambda: {"text": "不要发送 API。"},
                           close=lambda: closed.append(status))


@pytest.mark.parametrize("statuses", [(500, 200), (502, 503, 200), (504, 200)])
def test_transient_failure_reuploads_entire_audio_and_returns_once(audio, monkeypatch, statuses):
    uploads, closed, delays, progress = [], [], [], []
    pending = iter(statuses)
    def post(*args, **kwargs):
        uploads.append(kwargs["files"]["file"][1].read())
        assert kwargs["data"] == {"model": SENSEVOICE}
        return response(next(pending), closed)
    monkeypatch.setattr("speak.providers.requests.post", post)
    cancel = threading.Event()
    monkeypatch.setattr(cancel, "wait", lambda seconds: delays.append(seconds) or False)
    result = provider().transcribe(audio, [], cancel, progress=progress.append)
    assert result.text == "不要发送 API。"
    assert uploads == [audio.read_bytes()] * len(statuses)
    assert closed == list(statuses)
    assert len(progress) == len(statuses) - 1
    assert delays == [1, 2][:len(statuses) - 1]


def test_persistent_500_stops_at_three_and_keeps_audio(audio, monkeypatch):
    attempts, closed = [], []
    monkeypatch.setattr("speak.providers.requests.post", lambda *a, **kw:
                        attempts.append(1) or response(500, closed))
    cancel = threading.Event()
    monkeypatch.setattr(cancel, "wait", lambda seconds: False)
    with pytest.raises(RecognitionError, match="尝试 3 次") as exc:
        provider().transcribe(audio, [], cancel)
    assert exc.value.code == "service"
    assert len(attempts) == 3 and closed == [500] * 3 and audio.exists()
    assert "private-key" not in str(exc.value)


def test_cancel_during_backoff_sends_no_more_uploads(audio, monkeypatch):
    attempts, closed = [], []
    cancel = threading.Event()
    monkeypatch.setattr("speak.providers.requests.post", lambda *a, **kw:
                        attempts.append(1) or response(500, closed))
    def wait(seconds):
        cancel.set()
        return True
    monkeypatch.setattr(cancel, "wait", wait)
    with pytest.raises(RecognitionError) as exc:
        provider().transcribe(audio, [], cancel)
    assert exc.value.code == "cancelled"
    assert attempts == [1] and closed == [500]


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 429])
def test_non_transient_errors_are_not_retried(audio, monkeypatch, status):
    attempts, closed = [], []
    monkeypatch.setattr("speak.providers.requests.post", lambda *a, **kw:
                        attempts.append(1) or response(status, closed))
    with pytest.raises(RecognitionError):
        provider().transcribe(audio, [], threading.Event())
    assert attempts == [1] and closed == [status]


def test_free_price_failure_before_retry_blocks_next_upload(audio, monkeypatch):
    calls, attempts, closed = [], [], []
    def check(model):
        calls.append(model)
        if len(calls) >= 3:  # Initial check, first upload, then retry.
            raise RecognitionError("price", "免费状态无法确认")
    cloud = provider()
    cloud.guard.check = check
    monkeypatch.setattr("speak.providers.requests.post", lambda *a, **kw:
                        attempts.append(1) or response(500, closed))
    cancel = threading.Event()
    monkeypatch.setattr(cancel, "wait", lambda seconds: False)
    with pytest.raises(RecognitionError) as exc:
        cloud.transcribe(audio, [], cancel)
    assert exc.value.code == "price" and attempts == [1]


@pytest.mark.parametrize("invalid", ["empty", "corrupt"])
def test_invalid_audio_never_reaches_service(tmp_path, monkeypatch, invalid):
    path = tmp_path / "bad.wav"
    if invalid == "empty":
        with wave.open(str(path), "wb") as out:
            out.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
    else:
        path.write_bytes(b"not a recording")
    monkeypatch.setattr("speak.providers.requests.post", lambda *a, **kw: pytest.fail("must not upload"))
    with pytest.raises(RecognitionError) as exc:
        provider().transcribe(path, [], threading.Event())
    assert exc.value.code == ("empty_audio" if invalid == "empty" else "audio")


def failed_controller(audio):
    c = object.__new__(Controller)
    c.settings, c.state = Settings(), "error"
    c.events = queue.Queue()
    c.local = SimpleNamespace(stop=lambda: None)
    c.task = Task("failed", audio, c.settings, target="original-editor", target_window=(1, 2))
    return c


def test_retry_preserves_original_editor_after_error_dialog(audio, monkeypatch):
    c = failed_controller(audio)
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)
    c.retry("cloud")
    assert c.task.id != "failed"
    assert c.task.target == "original-editor" and c.task.target_window == (1, 2)
    assert c.task.path == audio and c.state == "processing"


def test_shutdown_keeps_failed_recording_even_if_worker_finally_runs_later(audio):
    c = failed_controller(audio)
    task = c.task
    c.shutdown()
    assert audio.exists() and task.cancel.is_set() and task.preserve_audio
    c._run(task)
    assert audio.exists()


def test_explicit_discard_still_removes_failed_recording(audio):
    c = failed_controller(audio)
    c.cancel()
    assert not audio.exists()
