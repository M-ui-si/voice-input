import threading
import time
from types import SimpleNamespace

import pytest

from speak.local import LocalProvider
from speak.ui import App


class SlowProcess:
    def __init__(self):
        self.alive = True
        self.closed = False
        self.calls = []
        self.done = threading.Event()

    def is_alive(self):
        return self.alive

    def terminate(self):
        self.calls.append("terminate")

    def kill(self):
        self.calls.append("kill")

    def join(self, timeout=None):
        self.calls.append("join")

    def close(self):
        assert not self.alive, "cannot close process while running"
        self.closed = True
        self.done.set()


def test_slow_model_exit_never_closes_a_running_handle_or_blocks_ui():
    provider = LocalProvider()
    process = SlowProcess()
    pipe_closed = []
    provider.process = process
    provider.pipe = SimpleNamespace(close=lambda: pipe_closed.append(True), send=lambda _: None)
    started = time.monotonic()
    provider.stop()
    assert time.monotonic()-started < .2
    assert provider.process is None and provider.pipe is None
    assert not process.closed
    process.alive = False
    assert provider.wait_for_cleanup(3)
    assert process.closed and pipe_closed == [True]


def test_close_finishes_even_when_shutdown_raises(monkeypatch):
    monkeypatch.setattr("speak.diagnostics.runtime_error", lambda *args: None)
    calls = []
    def fail():
        calls.append("shutdown")
        raise ValueError("cannot close a process while it is still running")
    app = SimpleNamespace(history_window=None, shutting_down=False, tick_id="timer",
        root=SimpleNamespace(after_cancel=lambda _: calls.append("timer"), destroy=lambda: calls.append("destroy")),
        delivery=SimpleNamespace(cancel=lambda: calls.append("delivery")),
        prepare_cancel=SimpleNamespace(set=lambda: calls.append("prepare")),
        controller=SimpleNamespace(shutdown=fail), close_preview=lambda: calls.append("preview"),
        hooks=SimpleNamespace(close=lambda: calls.append("hooks")))
    App.close(app)
    assert app.shutting_down and "preview" in calls and "hooks" in calls and calls[-1] == "destroy"


def test_idle_release_never_waits_for_inference_lock():
    provider = LocalProvider()
    provider.process = SlowProcess()
    provider.process.alive = False
    provider.last_used = time.monotonic()-400
    held, release = threading.Event(), threading.Event()
    def inference():
        with provider.lock:
            held.set(); release.wait(3)
    thread = threading.Thread(target=inference)
    thread.start(); assert held.wait(1)
    try:
        started = time.monotonic()
        provider.release_if_idle()
        assert time.monotonic()-started < .2
    finally:
        release.set(); thread.join(3)


def test_tick_failure_still_schedules_next_tick():
    errors, scheduled = [], []
    def fail():
        raise ValueError("controlled tick failure")
    app = SimpleNamespace(shutting_down=False, _tick_once=fail, tick=lambda: None,
        root=SimpleNamespace(report_callback_exception=lambda *a: errors.append(a[0]),
                             after=lambda delay, callback: scheduled.append(callback) or "next"))
    App.tick(app)
    assert errors == [ValueError] and scheduled == [app.tick] and app.tick_id == "next"


def test_late_old_worker_cannot_stop_new_worker():
    from speak.providers import RecognitionError
    provider = LocalProvider()
    old, current = SlowProcess(), SlowProcess()
    provider.process = current
    provider.pipe = object()
    cancelled = threading.Event(); cancelled.set()
    with pytest.raises(RecognitionError) as exc:
        provider._wait(cancelled, 1, old, object())
    assert exc.value.code == "cancelled"
    assert provider.process is current and not current.calls


def test_cancel_while_spawning_never_publishes_the_late_worker(monkeypatch):
    provider = LocalProvider()
    process = SlowProcess()
    process.alive = False
    def start():
        provider.stop()
    process.start = start
    pipe = SimpleNamespace(close=lambda: None)
    child = SimpleNamespace(close=lambda: None)
    context = SimpleNamespace(Pipe=lambda: (pipe, child), Process=lambda **_: process)
    monkeypatch.setattr("speak.local.mp.get_context", lambda _: context)
    monkeypatch.setattr(provider, "available", lambda: True)
    from speak.providers import RecognitionError
    with pytest.raises(RecognitionError) as exc:
        provider.start(threading.Event(), lambda _: None)
    assert exc.value.code == "cancelled" and provider.process is None
    assert provider.wait_for_cleanup(3) and process.closed
