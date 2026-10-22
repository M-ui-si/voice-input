import threading
from types import SimpleNamespace

import pytest

from speak import windows
from speak.delivery import TextDelivery


def task(target=None, task_id="one", window=(10, 20)):
    return SimpleNamespace(id=task_id, target=target, target_window=window, cancel=threading.Event())


def target(hwnd=10, pid=20, runtime_id=(1,)):
    return windows.Target(hwnd, pid, runtime_id, (None, None, None))


def test_transient_paste_failure_retries_then_inserts_exactly_once(monkeypatch):
    now = [0.0]
    delivery = TextDelivery(lambda *a: None, clock=lambda: now[0])
    t = task(target())
    calls = []
    def paste(destination, text, schedule):
        calls.append(text)
        return len(calls) == 2
    monkeypatch.setattr(windows, "paste", paste)
    delivery.queue(t, "中文 API")
    assert delivery.attempt(t) == "waiting"
    assert delivery.attempt(t) == "waiting"  # Throttled, not attempted twice immediately.
    now[0] += .3
    assert delivery.attempt(t) == "inserted"
    assert delivery.attempt(t) is None
    assert calls == ["中文 API", "中文 API"]


def test_missing_initial_accessibility_can_recover_only_in_original_window(monkeypatch):
    now = [0.0]
    d = TextDelivery(lambda *a: None, clock=lambda: now[0])
    t = task()
    focused = [target(hwnd=99)]
    monkeypatch.setattr(windows, "capture_target", lambda: focused[0])
    calls = []
    monkeypatch.setattr(windows, "paste", lambda *a: calls.append(a) or True)
    d.queue(t, "填入")
    assert d.attempt(t) == "waiting" and not calls
    focused[0] = target()
    now[0] += .3
    assert d.attempt(t) == "inserted" and len(calls) == 1


@pytest.mark.parametrize("reason", ["cancelled", "new_task", "expired"])
def test_cancel_new_task_and_expiry_never_insert_late(monkeypatch, reason):
    now = [0.0]
    d = TextDelivery(lambda *a: None, clock=lambda: now[0])
    t = task(target())
    d.queue(t, "旧结果")
    if reason == "cancelled": t.cancel.set()
    elif reason == "new_task": t = task(target(), task_id="two")
    else: now[0] = 31
    monkeypatch.setattr(windows, "paste", lambda *a: pytest.fail("must not paste old text"))
    assert d.attempt(t) in ("cancelled", "expired")
    assert d.pending is None


def test_temporary_focus_loss_does_not_permanently_disable_delivery(monkeypatch):
    now = [0.0]
    d = TextDelivery(lambda *a: None, clock=lambda: now[0])
    original = target()
    t = task(original)
    focused = [target(runtime_id=(2,))]
    monkeypatch.setattr(windows, "capture_target", lambda: focused[0])
    calls = []
    def paste(destination, text, schedule):
        if not windows.target_matches(destination): return False
        calls.append(text)
        return True
    monkeypatch.setattr(windows, "paste", paste)
    d.queue(t, "不要发送")
    assert d.attempt(t) == "waiting" and not calls
    focused[0] = original
    now[0] += .3
    assert d.attempt(t) == "inserted" and calls == ["不要发送"]


def test_modifiers_held_do_not_even_touch_clipboard(monkeypatch):
    monkeypatch.setattr(windows, "target_matches", lambda _: True)
    monkeypatch.setattr(windows.win32api, "GetAsyncKeyState", lambda _: 0x8000)
    monkeypatch.setattr(windows, "snapshot_clipboard", lambda: pytest.fail("must not touch clipboard"))
    assert not windows.paste(target(), "文字", lambda *a: None)


def test_writable_custom_rich_editor_and_focused_child_are_supported(monkeypatch):
    import sys
    interfaces = SimpleNamespace(IUIAutomationValuePattern=object(), IUIAutomationTextPattern=object())
    monkeypatch.setitem(sys.modules, "comtypes.gen.UIAutomationClient", interfaces)
    def no_pattern(*a): raise ValueError("not available")
    editor = SimpleNamespace(CurrentControlType=50025, CurrentIsPassword=False,
                             CurrentIsEnabled=True, GetCurrentPattern=no_pattern,
                             GetCurrentPropertyValue=lambda pid: "textbox" if pid == 30101 else "multiline=true")
    child = SimpleNamespace(CurrentControlType=50020, CurrentIsPassword=False,
                            CurrentIsEnabled=True, GetCurrentPattern=no_pattern,
                            GetCurrentPropertyValue=lambda pid: "")
    walker = SimpleNamespace(GetParentElement=lambda e: editor if e is child else None)
    monkeypatch.setattr(windows, "automation", lambda: SimpleNamespace(ControlViewWalker=walker))
    assert windows.editable_element(editor) is editor
    assert windows.editable_element(child) is editor
    editor.GetCurrentPropertyValue = lambda pid: "textbox" if pid == 30101 else "readonly=true"
    assert windows.editable_element(editor) is None
    editor.CurrentIsPassword = True
    assert windows.editable_element(editor) is None


def test_button_never_becomes_an_editor_via_parent_walking(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "comtypes.gen.UIAutomationClient", SimpleNamespace(
        IUIAutomationValuePattern=object(), IUIAutomationTextPattern=object()))
    button = SimpleNamespace(CurrentControlType=50000, CurrentIsPassword=False, CurrentIsEnabled=True)
    assert windows.editable_element(button) is None
