"""Rapid real-window input regression with a synthetic recorder (no microphone/ASR)."""
import argparse
import ctypes
import json
import os
import sys
import time
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ctypes.windll.shcore.SetProcessDpiAwareness(2)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["SPEAK_DATA_DIR"] = str(ROOT / "data/rapid-click-test/profile")

import tkinter as tk
import win32api
import win32con
import win32gui
from speak.storage import DATA, Settings
from speak.ui import App


class SyntheticRecorder:
    starts = 0
    def __init__(self, path, device=None, on_segment=None):
        self.path = path
        self.segments = []
        self.error, self.stream, self.level = None, None, 0
    def start(self):
        type(self).starts += 1
        with wave.open(str(self.path), "wb") as out:
            out.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        self.started = time.monotonic()
        self.stream = SimpleNamespace(active=True)
    def stop(self):
        self.stream = None


def run(baseline):
    DATA.mkdir(parents=True, exist_ok=True)
    if not baseline:
        (ROOT / "data/rapid-click-test/result.json").unlink(missing_ok=True)
    Settings(mode="local", hotkey="Ctrl+Alt+F10").save()
    root = tk.Tk()
    root.tk.call("tk", "scaling", 1.3333333333)
    app = App(root)
    app.controller.local.available = lambda: True
    app.controller.local.segment = lambda *a: (_ for _ in ()).throw(AssertionError("No ASR allowed"))
    errors = []
    root.report_callback_exception = lambda kind, value, trace: errors.append(kind.__name__)
    original_cursor = win32api.GetCursorPos()
    def pump(seconds=.15):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            root.update()
            time.sleep(.002)
    def tk_click(x=32):
        app.canvas.event_generate("<ButtonPress-1>", x=x, y=35)
        app.canvas.event_generate("<ButtonRelease-1>", x=x, y=35)
    def result_windows():
        return [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)
                and w.winfo_exists() and w.title() == "轻声 · 本次结果"]
    report = {"scope": "Real Tk/Win32 input with synthetic empty WAV; no microphone, model, cloud or paste"}
    try:
        pump()
        # Starting/stopping in a few milliseconds used to produce a permanent error
        # while leaving the preview saying it was still processing.
        tk_click()
        tk_click()
        pump(.2)
        report.update(after_fast_pair=app.controller.state, result_windows=len(result_windows()),
                      preview_open_after_pair=bool(app.preview_window and app.preview_window.winfo_exists()))
        if baseline:
            (ROOT / "data/rapid-click-test/baseline.json").write_text(json.dumps(report, indent=2), "utf-8")
            print(json.dumps(report))
            return
        assert app.controller.state == "idle", "Empty input should permit recording again"
        assert app.preview_window is None and not result_windows()
        # Ordinary errors must close stale previews and reuse a single result window.
        pump(.35)
        tk_click()
        task = app.controller.task
        task.error_code = "service"
        app.events.put((task.id, "error", "模拟服务暂时不可用"))
        pump(.25)
        assert app.controller.state == "error" and app.preview_window is None
        for _ in range(20):
            app.show_result()
        assert len(result_windows()) == 1
        for w in result_windows():
            w.destroy()
        app.controller.state = "idle"
        # Same screen point means a different control after resize. Bursts must
        # remain one expand gesture, never turn into Record or the context menu.
        menu_calls = []
        app.menu = lambda event: menu_calls.append(1)
        start_count = SyntheticRecorder.starts
        clicks = 0
        for side in ("left", "right"):
            for _ in range(10):
                app.collapse(side)
                pump(.4)
                l, t, r, b = win32gui.GetWindowRect(app.hooks.hwnd)
                point = (l + 11, t + 28)
                hit = win32gui.WindowFromPoint(point)
                assert win32gui.GetAncestor(hit, win32con.GA_ROOT) == app.hooks.hwnd
                win32api.SetCursorPos(point)
                for _ in range(6):
                    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)
                    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0)
                    pump(.01)
                    clicks += 1
                assert not app.collapsed and app.controller.state == "idle"
                assert SyntheticRecorder.starts == start_count and not menu_calls
        assert not errors, errors
        report.update(status="passed", empty_input_recovers=True, stale_preview_closed=True,
                      one_error_window=True, rapid_edge_clicks=clicks,
                      no_accidental_recording=True, no_accidental_menu=True, ui_callback_errors=errors)
        (ROOT / "data/rapid-click-test/result.json").write_text(json.dumps(report, indent=2), "utf-8")
        print(json.dumps(report))
    finally:
        app.close()
        win32api.SetCursorPos(original_cursor)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", action="store_true")
    args = parser.parse_args()
    with patch("speak.controller.Recorder", SyntheticRecorder), patch("speak.providers.FreePriceGuard.check", return_value=None):
        run(args.baseline)
