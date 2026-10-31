"""Exercise actual edge tabs and hotkey routing with isolated synthetic tasks."""
import ctypes
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ctypes.windll.shcore.SetProcessDpiAwareness(2)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["SPEAK_DATA_DIR"] = str(ROOT / "data/dock-test/profile")

import tkinter as tk
import win32api
import win32con
import win32gui
from PIL import ImageGrab
from speak.controller import Task
from speak.storage import DATA, Settings
from speak.ui import App
from speak import windows


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    result_path = ROOT / "data/dock-test/result.json"
    result_path.unlink(missing_ok=True)
    Settings(mode="local", hotkey="Ctrl+Alt+F11").save()
    root = tk.Tk()
    root.tk.call("tk", "scaling", 1.3333333333)
    cursor = win32api.GetCursorPos()
    with patch("speak.providers.FreePriceGuard.check", return_value=None):
        app = App(root)
    counts = {"start": 0, "stop": 0, "dialog": 0}
    def start(target, target_window=None):
        counts["start"] += 1
        task = Task(str(counts["start"]), DATA / "not-recorded.wav", app.settings, target)
        task.target_window = target_window
        task.recorder = SimpleNamespace(started=time.monotonic(), level=0, error=None, stream=None)
        app.controller.task, app.controller.state = task, "recording"
    def stop():
        counts["stop"] += 1
        app.controller.state = "processing"
    app.controller.start, app.controller.stop_recording = start, stop
    app.controller.shutdown = lambda: None
    app.show_result = lambda: counts.update(dialog=counts["dialog"] + 1)
    errors = []
    root.report_callback_exception = lambda kind, value, trace: errors.append(kind.__name__)
    target = tk.Toplevel(root)
    target.title("Speak docking probe focus target")
    target.geometry("400x140+300+200")
    entry = tk.Entry(target)
    entry.pack(fill="x", padx=20, pady=30)
    def pump(seconds=.15):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            root.update()
            time.sleep(.01)
    def click(x, y):
        # This probe checks separate deliberate gestures; rapid bursts have their
        # own regression probe and must not be made slower there.
        pump(max(0, app.pointer_ready_at - time.monotonic(), app.toggle_ready_at - time.monotonic()) + .02)
        win32api.SetCursorPos((int(x), int(y)))
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0)
        pump()
    def click_tab():
        l, t, r, b = win32gui.GetWindowRect(app.hooks.hwnd)
        click((l + r) // 2, (t + b) // 2)
    def hotkey():
        assert win32gui.GetForegroundWindow() == focus
        keys = (win32con.VK_CONTROL, win32con.VK_MENU, win32con.VK_F11)
        assert not any(win32api.GetAsyncKeyState(k) & 0x8000 for k in keys), "User is holding a test key"
        try:
            for key in keys:
                win32api.keybd_event(key, 0, 0, 0)
        finally:
            for key in reversed(keys):
                win32api.keybd_event(key, 0, win32con.KEYEVENTF_KEYUP, 0)
        pump()
    report = {"scope": "Real desktop edge tabs and physical registered hotkey; synthetic tasks; no microphone, ASR or paste"}
    try:
        pump()
        click(entry.winfo_rootx() + 20, entry.winfo_rooty() + 10)
        focus, hwnd = win32gui.GetForegroundWindow(), app.hooks.hwnd
        assert app.hooks.current == "Ctrl+Alt+F11"
        for side in ("left", "right"):
            app.collapse(side)
            pump()
            l, t, r, b = win32gui.GetWindowRect(hwnd)
            work = app._work_area()
            assert (r - l, b - t) == (22, 56)
            assert l == work[0] if side == "left" else r == work[2]
            assert Settings.load().collapsed and Settings.load().dock_side == side
            ImageGrab.grab(bbox=(l, t, r, b)).save(ROOT / f"data/dock-test/{side}-tab.png")
            click_tab()
            assert not app.collapsed and counts["start"] == 0
            assert win32gui.GetForegroundWindow() == focus and app.hooks.hwnd == hwnd
        ImageGrab.grab(bbox=win32gui.GetWindowRect(hwnd)).save(ROOT / "data/dock-test/expanded.png")
        # Use the actual collapse control instead of its Python method.
        anchor = win32gui.GetWindowRect(hwnd)
        click(anchor[0] + 232, anchor[1] + 30)
        assert app.collapsed
        hotkey()
        task = app.controller.task
        assert app.collapsed and counts["start"] == 1 and app.preview_window is None
        app.update_preview("这是隔离测试的预览文字。", "本地预览")
        assert app.preview_window is None
        click_tab()
        assert app.controller.task is task and not task.cancel.is_set()
        assert app.preview_widget.get("1.0", "end-1c") == "这是隔离测试的预览文字。"
        assert win32gui.GetForegroundWindow() == focus
        # A preview update may not re-show the panel while collapsed.
        app.collapse("right")
        pump()
        assert not win32gui.IsWindowVisible(app._preview_hwnd)
        app.update_preview("更新后的隔离测试文字。", "本地预览")
        assert not win32gui.IsWindowVisible(app._preview_hwnd)
        win32gui.PostMessage(hwnd, win32con.WM_HOTKEY, app.hooks.HOTKEY_ID, 0)
        pump()
        assert app.collapsed and counts["stop"] == 1
        click_tab()
        assert app.preview_widget.get("1.0", "end-1c") == "更新后的隔离测试文字。"
        assert str(app.preview_stop["state"]) == "disabled"
        app.collapse("left")
        app.events.put((task.id, "error", "模拟识别失败"))
        pump()
        assert counts["dialog"] == 0 and app.last_error == "模拟识别失败"
        assert app.collapsed and app.preview_window is None
        assert win32gui.GetForegroundWindow() == focus and not errors
        # A vertical drag moves the handle without expanding or changing the task.
        pump(max(0, app.pointer_ready_at - time.monotonic()) + .02)
        anchor = win32gui.GetWindowRect(hwnd)
        work = app._work_area()
        delta = 65 if anchor[1] + 121 < work[3] else -65
        win32api.SetCursorPos((anchor[0] + 11, anchor[1] + 28))
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)
        pump(.03)
        win32api.SetCursorPos((anchor[0] + 11, anchor[1] + 28 + delta))
        pump(.08)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0)
        pump()
        assert app.collapsed and app.controller.task is task
        assert win32gui.GetWindowRect(hwnd)[1] == anchor[1] + delta
        saved = Settings.load()
        app.close()
        root = tk.Tk()
        root.tk.call("tk", "scaling", 1.3333333333)
        with patch("speak.providers.FreePriceGuard.check", return_value=None):
            app = App(root)
        pump()
        assert app.collapsed and app.settings.dock_side == saved.dock_side
        restored = win32gui.GetWindowRect(app.hooks.hwnd)
        assert restored[1] == saved.y and (restored[2] - restored[0], restored[3] - restored[1]) == (22, 56)
        report.update(status="passed", left_right_edges=True, real_collapse_expand_clicks=True,
                      no_accidental_recording=True, hidden_preview_stays_hidden=True,
                      preview_restored=True, hotkey_start_stop_while_collapsed=True,
                      active_task_preserved=True, focus_preserved=True, saved_collapsed_state=True,
                      no_error_popup_while_collapsed=True, vertical_drag=True, restart_restores_handle=True,
                      physical_global_hotkey=True, ui_callback_errors=errors)
        result_path.write_text(json.dumps(report, indent=2), "utf-8")
        print(json.dumps(report))
    finally:
        app.close()
        win32api.SetCursorPos(cursor)


if __name__ == "__main__":
    main()
