import queue
import threading
import time
from types import SimpleNamespace

from speak.controller import Controller, Task
from speak.local import LocalProvider
from speak.storage import Settings
from speak import windows


def test_late_event_from_cancelled_or_retried_task_is_ignored(tmp_path, monkeypatch):
    c = object.__new__(Controller)
    first = Task("first", tmp_path/"one.wav", Settings())
    c.task = first
    assert c.accept_event("first")
    first.cancel.set()
    assert not c.accept_event("first")
    c.task = Task("second", tmp_path/"two.wav", Settings())
    assert not c.accept_event("first") and c.accept_event("second")


def test_local_is_not_started_at_construction():
    p = LocalProvider()
    assert p.process is None and p.pipe is None


def test_idle_release_waits_for_inflight_recognition(monkeypatch):
    p = LocalProvider(); p.process = object(); p.last_used = time.monotonic()-400
    calls=[]; monkeypatch.setattr(p, "stop", lambda: calls.append(1))
    p.busy=True; p.release_if_idle(); assert not calls
    p.busy=False; p.release_if_idle(); assert calls == [1]


def test_target_change_never_writes_clipboard(monkeypatch):
    monkeypatch.setattr(windows, "target_matches", lambda target: False)
    def forbidden(*args): raise AssertionError("clipboard must not be touched")
    monkeypatch.setattr(windows.win32clipboard, "OpenClipboard", forbidden)
    assert not windows.paste(object(), "不要发送", lambda *args: None)


def test_hotkey_parser_rejects_unmodified_or_unknown_keys():
    import pytest
    assert windows.parse_hotkey("Ctrl+Alt+Space") == (0x4003, 32)
    with pytest.raises(ValueError): windows.parse_hotkey("Space")
    with pytest.raises(ValueError): windows.parse_hotkey("Ctrl+Enter")


def test_recovered_external_audio_survives_task_cleanup(tmp_path, monkeypatch):
    from speak.storage import History
    c = object.__new__(Controller)
    c.settings = Settings()
    c.state = "idle"
    c.history = History(tmp_path / "private")
    source = tmp_path / "my-original.wav"
    source.write_bytes(b"original recording")
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)
    c.resume(source, "cloud")
    assert c.task.path != source
    assert c.task.path.read_bytes() == source.read_bytes()
    c.cancel()
    assert source.read_bytes() == b"original recording"
    assert not c.task.path.exists()


def test_microphone_disconnect_still_finalizes_writer(tmp_path):
    from speak.recording import Recorder
    recorder = Recorder(tmp_path / "recording.wav")
    closed = []
    def fail():
        raise RuntimeError("disconnected")
    recorder.stream = SimpleNamespace(stop=fail, close=lambda: closed.append(True))
    recorder.thread = threading.Thread(target=recorder._write)
    recorder.thread.start()
    recorder.stop()
    assert recorder.error and closed
    assert recorder.closed.is_set() and not recorder.thread.is_alive()


def test_floating_position_supports_negative_monitor_coordinates(monkeypatch):
    calls = []
    monkeypatch.setattr(windows.win32api, "MonitorFromPoint", lambda *args: 1)
    monkeypatch.setattr(windows.win32api, "GetMonitorInfo", lambda monitor: {"Work": (-1920, 0, 0, 1040)})
    monkeypatch.setattr(windows.win32gui, "GetAncestor", lambda *args: 10)
    monkeypatch.setattr(windows.win32gui, "SetWindowPos", lambda *args: calls.append(args))
    root = SimpleNamespace(winfo_id=lambda: 10)
    windows.position_floating(root, -1500, 100)
    assert calls[-1][2:6] == (-1500, 100, 286, 78)
    windows.position_floating(root, -9999, 9999)
    assert calls[-1][2:6] == (-1920, 962, 286, 78)
