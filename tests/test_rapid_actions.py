import queue
from types import SimpleNamespace

import pytest

from speak import windows
from speak.controller import Controller, Task
from speak.providers import RecognitionError
from speak.storage import Settings
from speak.ui import App


def pointer_app(monkeypatch, collapsed=False):
    now = [10.0]
    monkeypatch.setattr("speak.ui.time.monotonic", lambda: now[0])
    monkeypatch.setattr(windows.win32gui, "GetWindowRect", lambda _: (0, 0, 286, 78))
    app = object.__new__(App)
    app.shutting_down = False
    app.pointer_ready_at = app.toggle_ready_at = 0
    app.layout_revision = 0
    app.toggling = False
    app.drag_origin = None
    app.collapsed = collapsed
    app.hooks = SimpleNamespace(hwnd=1)
    app.controller = SimpleNamespace(state="idle", task=None)
    calls = []
    app.toggle = lambda: calls.append("toggle")
    def expand():
        calls.append("expand")
        App._layout_changed(app)
        app.collapsed = False
    app.expand = expand
    return app, now, calls


def event(x=11):
    return SimpleNamespace(x=x, x_root=x, y_root=28)


def test_double_click_on_handle_does_not_record_and_controls_recover(monkeypatch):
    app, now, calls = pointer_app(monkeypatch, collapsed=True)
    for _ in range(6):
        App.press(app, event())
        App.release(app, event())
        now[0] += .02
    assert calls == ["expand"]
    now[0] += .4
    App.press(app, event())
    App.release(app, event())
    assert calls == ["expand", "toggle"]


def test_release_from_old_layout_never_activates_new_control(monkeypatch):
    app, _, calls = pointer_app(monkeypatch)
    App.press(app, event())
    App._layout_changed(app)
    App.release(app, event())
    assert not calls


@pytest.mark.parametrize("change", ["new_task", "processing_completed"])
def test_record_click_cannot_apply_to_a_task_that_changed_during_press(monkeypatch, change):
    app, _, calls = pointer_app(monkeypatch)
    app.controller.task = object()
    if change == "processing_completed":
        app.controller.state = "processing"
    App.press(app, event())
    if change == "new_task":
        app.controller.task = object()
    else:
        app.controller.state = "idle"
    App.release(app, event())
    assert not calls


def test_fast_toggle_burst_honors_stop_and_does_not_restart_processing(monkeypatch):
    app, now, _ = pointer_app(monkeypatch)
    calls = []
    def toggle():
        if app.controller.state == "processing":
            return
        calls.append(app.controller.state)
        if app.controller.state == "idle":
            app.controller.state = "recording"
            app.controller.task = SimpleNamespace(recorder=SimpleNamespace(started=now[0]))
        else:
            app.controller.state = "processing"
    app._toggle = toggle
    for _ in range(8):
        App.toggle(app)
        now[0] += .02
    assert calls == ["idle", "recording"]
    now[0] += .3
    app.controller.state = "idle"
    App.toggle(app)
    assert calls == ["idle", "recording", "idle"]
    App.toggle(app)
    assert calls == ["idle", "recording", "idle", "recording"]
    app.controller.state = "idle"
    App.toggle(app)
    assert len(calls) == 4
    now[0] += .3
    App.toggle(app)
    assert calls[-1] == "idle" and len(calls) == 5


def test_stop_queued_during_slow_device_open_is_honored(monkeypatch):
    app, now, _ = pointer_app(monkeypatch)
    calls = []
    def toggle():
        if app.controller.state == "idle":
            calls.append("start")
            now[0] += 2
            app.controller.state = "recording"
            app.controller.task = SimpleNamespace(recorder=SimpleNamespace(started=now[0]))
        else:
            calls.append("stop")
            app.controller.state = "processing"
    app._toggle = toggle
    App.toggle(app)
    App.toggle(app)
    assert calls == ["start", "stop"]


@pytest.mark.parametrize("elapsed", [0, .01, .2])
def test_immediate_stop_bypasses_all_restart_cooldowns(monkeypatch, elapsed):
    app, now, _ = pointer_app(monkeypatch)
    app.controller.state = "recording"
    app.controller.task = SimpleNamespace(recorder=SimpleNamespace(started=now[0] - elapsed))
    app.toggle_ready_at = now[0] + 100
    stops = []
    def stop():
        stops.append(1)
        app.controller.state = "processing"
    app._toggle = stop
    App.toggle(app)
    assert stops == [1] and app.controller.state == "processing"


def test_reentrant_toggle_does_not_repeat_and_failure_releases_the_guard(monkeypatch):
    app, now, _ = pointer_app(monkeypatch)
    calls = []
    def fail():
        calls.append(1)
        App.toggle(app)
        raise RuntimeError("test device failure")
    app._toggle = fail
    with pytest.raises(RuntimeError):
        App.toggle(app)
    assert calls == [1] and not app.toggling
    now[0] += .3
    with pytest.raises(RuntimeError):
        App.toggle(app)
    assert calls == [1, 1]


def test_error_category_is_part_of_event_not_mutable_task_metadata(tmp_path):
    c = object.__new__(Controller)
    c.events = queue.Queue()
    task = Task("empty", tmp_path / "recording.wav", Settings(mode="local"))
    task.stopped.set()
    task.segments.put(None)
    c._run(task)
    task.error_code = "another-category"
    task_id, kind, error = c.events.get_nowait()
    assert task_id == "empty" and kind == "error"
    assert isinstance(error, RecognitionError) and error.code == "empty"


@pytest.mark.parametrize("code", ["empty", "empty_audio"])
def test_recoverable_empty_result_keeps_recording_when_app_closes(tmp_path, code):
    c = object.__new__(Controller)
    path = tmp_path / "retained.wav"
    path.write_bytes(b"retained recording")
    c.task = Task("failed", path, Settings(), error_code=code)
    c.state = "idle"
    c.local = SimpleNamespace(stop=lambda: None)
    c.shutdown()
    assert path.exists() and c.task.preserve_audio
