"""Real desktop preview/button checks with synthetic tasks; never opens a microphone."""
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
os.environ["SPEAK_DATA_DIR"] = str(ROOT / "data/preview-test/profile")

import tkinter as tk
import win32api
import win32con
import win32gui
from speak.controller import Task
from speak.storage import DATA, Settings
from speak.ui import App
from speak import windows


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    Settings(mode="local").save()
    root = tk.Tk()
    root.tk.call("tk", "scaling", 1.3333333333)
    cursor = win32api.GetCursorPos()
    with patch("speak.providers.FreePriceGuard.check", return_value=None):
        app = App(root)
    counts = {"start": 0, "stop": 0}
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
    errors = []
    root.report_callback_exception = lambda kind, value, trace: errors.append(kind.__name__)
    target = tk.Toplevel(root)
    target.title("Speak preview test focus target")
    target.geometry("400x140+200+200")
    entry = tk.Entry(target)
    entry.pack(fill="x", padx=20, pady=30)
    def pump(seconds=.15):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            root.update()
            time.sleep(.01)
    def click(x, y):
        win32api.SetCursorPos((int(x), int(y)))
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0)
        pump()
    monitor = win32api.MonitorFromWindow(app.hooks.hwnd, win32con.MONITOR_DEFAULTTONEAREST)
    left, top, right, bottom = win32api.GetMonitorInfo(monitor)["Work"]
    report = {"scope": "Actual Tk/Win32 windows and physical clicks; synthetic tasks; no microphone, ASR, or paste"}
    try:
        pump()
        entry.focus_force()
        click(entry.winfo_rootx() + 20, entry.winfo_rooty() + 10)
        focus = win32gui.GetForegroundWindow()
        for index, (x, y) in enumerate([(right - 286, top + 90), (right - 286, bottom - 78),
                                        (left, top + 90), (left + 100, top + 100)]):
            app.controller.state = "idle"
            windows.position_floating(root, x, y)
            pump()
            anchor = win32gui.GetWindowRect(app.hooks.hwnd)
            click(anchor[0] + 32, anchor[1] + 35)
            assert app.controller.state == "recording", "Floating button cannot start"
            preview = win32gui.GetWindowRect(app._preview_hwnd)
            assert (preview[2] <= anchor[0] or preview[0] >= anchor[2] or
                    preview[3] <= anchor[1] or preview[1] >= anchor[3]), (anchor, preview)
            hit = win32gui.WindowFromPoint((anchor[0] + 32, anchor[1] + 35))
            assert win32gui.GetAncestor(hit, win32con.GA_ROOT) == app.hooks.hwnd
            assert win32gui.GetForegroundWindow() == focus, "Preview stole focus"
            if index == 0:
                # Verify the real preview Stop button, not just its Python callback.
                button = app.preview_stop
                click(button.winfo_rootx() + button.winfo_width() // 2,
                      button.winfo_rooty() + button.winfo_height() // 2)
            else:
                click(anchor[0] + 32, anchor[1] + 35)
            assert app.controller.state == "processing", "Stop click did not reach its handler"
            App.stop_preview_recording(app)
            assert counts == {"start": index + 1, "stop": index + 1}
            assert not ctypes.windll.user32.IsHungAppWindow(app.hooks.hwnd)
            app.close_preview()
        assert not errors, errors
        assert win32gui.GetForegroundWindow() == focus
        report.update(status="passed", screen_edge_cases=4, floating_start_stop=True,
                      preview_stop_button=True, no_overlap=True, focus_preserved=True,
                      double_stop_does_not_restart=True, ui_callback_errors=errors)
        (ROOT / "data/preview-test/result.json").write_text(json.dumps(report, indent=2), "utf-8")
        print(json.dumps(report))
    finally:
        app.close()
        win32api.SetCursorPos(cursor)


if __name__ == "__main__":
    main()
