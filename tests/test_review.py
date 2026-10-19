"""Regressions for task ownership, current-caret insertion, and clipboard privacy."""
import json
import queue
import threading
import wave
import sys
import hashlib
from dataclasses import replace
from types import SimpleNamespace

import pytest

from speak import windows
from speak.controller import Controller, Task
from speak.local import LocalProvider
from speak.providers import RecognitionError, Result
from speak.storage import History, Settings


def test_single_instance_is_scoped_to_data_profile(tmp_path):
    from main import instance_name
    assert instance_name(tmp_path / "real", "user") == instance_name(tmp_path / "real", "user")
    assert instance_name(tmp_path / "real", "user") != instance_name(tmp_path / "isolated-probe", "user")


def test_empty_writable_value_is_supported_and_read_only_is_rejected(monkeypatch):
    interfaces = SimpleNamespace(IUIAutomationValuePattern=object(), IUIAutomationTextPattern=object())
    monkeypatch.setitem(sys.modules, "comtypes.gen.UIAutomationClient", interfaces)
    value = SimpleNamespace(CurrentIsReadOnly=False, CurrentValue=None)
    element = SimpleNamespace(GetCurrentPattern=lambda _: SimpleNamespace(QueryInterface=lambda _: value))
    assert windows.selection_fingerprint(element)[0] == hashlib.sha256(b"").hexdigest()
    value.CurrentIsReadOnly=True
    assert windows.selection_fingerprint(element) is None


def test_same_editor_accepts_new_caret_but_rejects_document_or_focus_change(monkeypatch):
    original = windows.Target(1, 10, (2, 3), ("document", 4, 0))
    moved = replace(original, fingerprint=("document", 1, 2))
    monkeypatch.setattr(windows, "capture_target", lambda: moved)
    assert windows.target_matches(original)
    for other in [replace(moved, hwnd=2), replace(moved, pid=11),
                  replace(moved, runtime_id=(2, 4)),
                  replace(moved, fingerprint=("edited", 1, 2)), None]:
        monkeypatch.setattr(windows, "capture_target", lambda: other)
        assert not windows.target_matches(original)


def test_terminal_result_or_error_can_only_be_consumed_once(tmp_path):
    c = object.__new__(Controller)
    c.task = Task("first", tmp_path / "one.wav", Settings())
    assert c.accept_event("first")
    assert c.accept_event("first", terminal=True)
    assert not c.accept_event("first", terminal=True)
    assert not c.accept_event("first")
    c.task = Task("second", tmp_path / "two.wav", Settings())
    assert c.accept_event("second", terminal=True)
    assert not c.accept_event("first")


def test_retry_success_cleans_chunks_owned_by_failed_task(tmp_path, monkeypatch):
    c = object.__new__(Controller)
    c.events = queue.Queue()
    c.settings, c.state = Settings(mode="local"), "error"
    c.history = History(tmp_path)
    source = c.history.audio_dir / "source.wav"
    with wave.open(str(source), "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\x01\x00" * 16000)
    old_chunks = [c.history.audio_dir / f"source-seg{i}.wav" for i in range(3)]
    for path in old_chunks:
        path.write_bytes(b"failed-task chunk")
    old = Task("old", source, c.settings)
    old.recorder = SimpleNamespace(segments=old_chunks)
    old.files = old_chunks[:1]
    c.task = old
    c.local = SimpleNamespace(segment=lambda *args: Result("忠实转写", "local", "mock", 0.1))
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)
    c.retry("local")
    assert c.task.id != old.id
    c._run(c.task, retry=True)
    assert not list(c.history.audio_dir.glob("*.wav"))
    assert c.history.list()[0][2] == "忠实转写"


def test_cancelled_local_request_does_not_check_or_load_models(monkeypatch):
    p = LocalProvider()
    cancel = threading.Event()
    cancel.set()
    monkeypatch.setattr(p, "available", lambda: pytest.fail("must not inspect or load models"))
    with pytest.raises(RecognitionError) as exc:
        p.start(cancel, lambda _: None)
    assert exc.value.code == "cancelled"


@pytest.mark.parametrize("raw", [[], None, 42, {"terms": None, "replacements": [],
                                             "mode": "unknown", "x": "wrong", "autostart": "false"}])
def test_invalid_settings_do_not_crash_or_enable_unknown_modes(tmp_path, raw):
    (tmp_path / "settings.json").write_text(json.dumps(raw), encoding="utf-8")
    assert Settings.load(tmp_path) == Settings()


def test_consecutive_pastes_restore_original_clipboard_not_prior_transcript(monkeypatch):
    state = {"text": "user-original", "sequence": 1}
    pending = []
    monkeypatch.setattr(windows, "_pending_clipboard", None)
    monkeypatch.setattr(windows, "target_matches", lambda _: True)
    monkeypatch.setattr(windows, "snapshot_clipboard", lambda: (state["text"], state["sequence"]))
    clip = windows.win32clipboard
    monkeypatch.setattr(clip, "OpenClipboard", lambda: None)
    monkeypatch.setattr(clip, "CloseClipboard", lambda: None)
    monkeypatch.setattr(clip, "EmptyClipboard", lambda: None)
    monkeypatch.setattr(clip, "GetClipboardSequenceNumber", lambda: state["sequence"])
    def set_text(text, *args):
        state["text"] = text
        state["sequence"] += 1
    monkeypatch.setattr(clip, "SetClipboardText", set_text)
    def restore(snapshot, expected):
        if state["sequence"] == expected:
            set_text(snapshot)
    monkeypatch.setattr(windows, "restore_clipboard", restore)
    monkeypatch.setattr(windows.win32api, "GetAsyncKeyState", lambda _: 0)
    monkeypatch.setattr(windows.win32api, "keybd_event", lambda *args: None)
    schedule = lambda delay, fn: pending.append(fn)
    assert windows.paste(object(), "first transcript", schedule)
    assert windows.paste(object(), "second transcript", schedule)
    pending[0]()
    assert state["text"] == "second transcript"
    pending[1]()
    assert state["text"] == "user-original"
    # A fresh user copy while a paste is pending must remain untouched.
    assert windows.paste(object(), "third transcript", schedule)
    set_text("user-copied-new")
    pending[-1]()
    assert state["text"] == "user-copied-new"
    # Exiting before the delayed callback also restores the original clipboard.
    assert windows.paste(object(), "final transcript", schedule)
    windows.restore_pending_clipboard()
    assert state["text"] == "user-copied-new"
    pending[-1]()
    assert state["text"] == "user-copied-new"


def test_corrupted_download_cache_can_be_retried(tmp_path, monkeypatch):
    import hashlib
    from speak import prepare
    payload = [b"corrupt"]
    good = b"healthy"
    class Response:
        status_code = 206
        url = "https://example.invalid/model.zip"
        def __init__(self, range_header):
            self.headers = {"Content-Range": range_header + "/7"}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_content(self, size): return iter(payload)
    def get(url, headers, **kwargs):
        return Response(headers["Range"].replace("=", " "))
    monkeypatch.setattr(prepare.requests, "get", get)
    digest = hashlib.sha256(good).hexdigest()
    with pytest.raises(ValueError, match="校验不符"):
        prepare.fetch(Response.url, digest, tmp_path, lambda _: None)
    assert not (tmp_path / "model.zip.chunks" / "0").exists()
    payload[:] = [good]
    assert prepare.fetch(Response.url, digest, tmp_path, lambda _: None).read_bytes() == good
