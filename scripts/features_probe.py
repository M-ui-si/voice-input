"""Isolated desktop and microphone checks for correction learning and paused streaming.

GUI mode uses generated PCM and fake ASR; microphone mode never uploads/decodes audio.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/features-test"
sys.path.insert(0, str(ROOT))


def microphone():
    import time
    import wave
    from speak.recording import Recorder
    out = OUT / "microphone"
    out.mkdir(parents=True, exist_ok=True)
    segments = []
    recorder = Recorder(out / "capture.wav", on_segment=segments.append)
    report = {"scope": "Real microphone; no ASR or upload; audio deleted"}
    try:
        recorder.start(); time.sleep(.5); recorder.pause()
        paused_frames = recorder.frames
        assert paused_frames > 0 and not recorder.stream.active
        time.sleep(.6)
        assert recorder.frames == paused_frames
        recorder.continue_recording(); time.sleep(.5); recorder.pause()
        assert recorder.frames > paused_frames
        recorder.stop()
        with wave.open(str(recorder.path), "rb") as wav:
            assert wav.getnframes() == recorder.frames
        assert len(segments) == 2 and segments[1].break_before and segments[1].overlap_frames == 0
        report.update(status="passed", frames=recorder.frames, paused_frames=paused_frames, segments=len(segments))
    finally:
        recorder.stop()
        for path in [recorder.path, *recorder.segments]:
            path.unlink(missing_ok=True)
        (out / "report.json").write_text(json.dumps(report, indent=2), "utf-8")
    print(json.dumps(report), flush=True)


def gui():
    import ctypes
    import threading
    import time
    from unittest.mock import patch
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
    import tkinter as tk
    from tkinter import ttk
    import win32api
    import win32con
    import win32gui
    import sounddevice
    from PIL import ImageGrab
    from speak.providers import RecognitionError, Result
    from speak.recording import RATE
    from speak.storage import DATA, Settings
    from speak.ui import App
    DATA.mkdir(parents=True, exist_ok=True)
    Settings(mode="cloud", hotkey="Ctrl+Alt+F8", x=1100, y=350).save()
    capture = {}
    class Stream:
        def __init__(self, **kw):
            capture["callback"] = kw["callback"]; self.active = False
        def start(self): self.active = True
        def stop(self): self.active = False
        def close(self): self.active = False
    with patch.object(sounddevice, "RawInputStream", Stream), patch("speak.providers.FreePriceGuard.check", return_value=None):
        root = tk.Tk(); root.tk.call("tk", "scaling", 1.3333333333)
        app = App(root)
        app.controller.history.clear()
        app.controller.secrets.load = lambda: "synthetic-probe-key"
        errors, sent = [], []
        root.report_callback_exception = lambda kind, value, trace: errors.append(kind.__name__ + ": " + str(value))
        original_cursor = win32api.GetCursorPos()
        probe_id = f"features-{os.getpid()}"
        helper = subprocess.Popen([sys.executable, str(ROOT / "scripts/native_input_probe.py"), "--helper"],
                                  env=dict(os.environ, SPEAK_PROBE_ID=probe_id))
        def pump(seconds=.15):
            end = time.monotonic()+seconds
            while time.monotonic() < end:
                root.update(); time.sleep(.005)
        def wait(predicate, seconds=4):
            end = time.monotonic()+seconds
            while not predicate():
                assert time.monotonic() < end, "Timed out waiting for feature state"
                pump(.02)
        def click(widget):
            pump(.05)
            x, y = widget.winfo_rootx()+widget.winfo_width()//2, widget.winfo_rooty()+widget.winfo_height()//2
            assert win32gui.GetAncestor(win32gui.WindowFromPoint((x, y)), win32con.GA_ROOT) == win32gui.GetAncestor(widget.winfo_id(), win32con.GA_ROOT)
            win32api.SetCursorPos((x, y))
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0)
            pump()
        def children(widget):
            for child in widget.winfo_children():
                yield child
                yield from children(child)
        def button(widget, label):
            return next(w for w in children(widget) if isinstance(w, ttk.Button) and w.cget("text") == label)
        def screenshot(widget, name):
            pump()
            ImageGrab.grab(bbox=(widget.winfo_rootx(), widget.winfo_rooty(), widget.winfo_rootx()+widget.winfo_width(), widget.winfo_rooty()+widget.winfo_height())).save(DATA / name)
        def feed(seconds):
            capture["callback"](b"\x01\x00"*RATE*seconds, 0, None, None)
            pump()
        try:
            wait(lambda: win32gui.FindWindow(None, "Speak automated native input test " + probe_id) != 0)
            editor = win32gui.FindWindow(None, "Speak automated native input test " + probe_id)
            wait(lambda: win32gui.FindWindowEx(editor, 0, "EDIT", None) != 0)
            edit = win32gui.FindWindowEx(editor, 0, "EDIT", None)
            def focus_edit():
                try:
                    win32gui.SetForegroundWindow(editor)
                except Exception:
                    pass  # Windows may deny activation; the physical click below activates it.
                l, t, _, _ = win32gui.GetWindowRect(edit)
                assert win32gui.GetAncestor(win32gui.WindowFromPoint((l+30, t+20)), win32con.GA_ROOT) == editor
                win32api.SetCursorPos((l+30, t+20))
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0)
                pump(.4)
            def text_value():
                size = win32gui.SendMessage(edit, win32con.WM_GETTEXTLENGTH)
                buffer = win32gui.PyMakeBuffer((size+1)*2)
                win32gui.SendMessage(edit, win32con.WM_GETTEXT, size+1, buffer)
                return bytes(buffer).decode("utf-16le").rstrip("\x00")
            focus_edit()
            outputs = iter(["第一段不要发送消息，使用扣德克斯。", "使用扣德克斯。暂停前补充。", "继续检查 API。"])
            app.controller.cloud.transcribe = lambda *a, **kw: Result(next(outputs), "cloud", "probe", .01)
            app.toggle(); feed(9)
            wait(lambda: app.controller.task.processed_segments == 1)
            pump()
            assert text_value() == "" and "第一段" in app.preview_text
            target = app.controller.task.target
            assert target is not None
            click(app.preview_pause)
            assert app.controller.state == "paused"
            frames = app.controller.task.recorder.frames
            feed(2)
            assert app.controller.task.recorder.frames == frames
            assert win32gui.GetForegroundWindow() == target.hwnd, "Preview stole target focus"
            screenshot(app.preview_window, "paused-preview.png")
            app.collapse(); pump()
            assert app.controller.state == "paused" and not win32gui.IsWindowVisible(app._preview_hwnd)
            screenshot(root, "paused-dock.png")
            app.expand(); pump()
            assert app.controller.state == "paused" and win32gui.IsWindowVisible(app._preview_hwnd)
            assert win32gui.GetForegroundWindow() == target.hwnd
            click(app.preview_pause); assert app.controller.state == "recording"
            feed(2); click(app.preview_stop)
            expected = "第一段不要发送消息，使用扣德克斯。暂停前补充。继续检查 API。"
            wait(lambda: text_value() == expected)
            assert app.controller.state == "idle" and not sent
            assert len(app.controller.history.list()) == 1
            app.open_settings(); pump()  # Keep an older rule editor open while learning a rule.
            app.open_history(); pump()
            history = app.history_window
            history.lift(); history.focus_force(); pump()
            history_text = next(w for w in children(history) if isinstance(w, tk.Text))
            history_text.delete("1.0", "end"); history_text.insert("1.0", expected.replace("扣德克斯", "Codex"))
            screenshot(history, "history-before-save.png")
            click(button(history, "保存修改"))
            assert app.controller.history.list()[0][2] == expected
            assert "Codex" in app.controller.history.list()[0][6]
            assert app.settings.replacements == {}
            screenshot(history, "history-edit.png")
            click(button(history, "记住纠错"))
            dialog = next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel) and w.title() == "轻声 · 确认全局纠错")
            checks = [w for w in children(dialog) if isinstance(w, ttk.Checkbutton)]
            assert checks and not any(w.instate(["selected"]) for w in checks)
            screenshot(dialog, "correction-confirm.png")
            click(checks[0]); click(button(dialog, "确认记住"))
            assert app.settings.replacements == {"扣德克斯": "Codex"}
            history_text.insert("end", "未保存的测试修改")
            with patch("speak.ui.messagebox.askyesnocancel", return_value=None):
                app.close()
            assert history.winfo_exists() and not app.shutting_down
            with patch("speak.ui.messagebox.askyesnocancel", return_value=False):
                history.tk.call(history.protocol("WM_DELETE_WINDOW"))
            assert app.controller.history.list()[0][6] == expected.replace("扣德克斯", "Codex")
            app.settings_window.lift(); pump()
            click(button(app.settings_window, "保存设置"))
            assert Settings.load().replacements == {"扣德克斯": "Codex"}
            screenshot(app.settings_window, "settings.png")
            app.settings_window.destroy()
            # A failed cloud segment must not stop capture or open the result dialog early.
            focus_edit()
            def fail(*a, **kw):
                raise RecognitionError("network", "Synthetic network failure")
            app.controller.cloud.transcribe = fail
            app.toggle(); feed(9)
            wait(lambda: app.controller.task.cloud_error is not None)
            pump()
            assert app.controller.state == "recording" and not (app.result_window and app.result_window.winfo_exists())
            feed(1); assert app.controller.task.recorder.frames == 10*RATE
            app.stop_preview_recording(); wait(lambda: app.controller.state == "error")
            assert app.controller.task.path.exists() and len(app.controller.history.list()) == 1
            assert text_value() == expected and not sent
            app.cancel()
            assert not list(app.controller.history.audio_dir.glob("*.wav"))
            assert not errors, errors
            report = {"status": "passed", "scope": "Physical desktop clicks, synthetic PCM and ASR; no microphone/cloud upload/message send",
                      "pause_focus_preserved": True, "single_final_insertion": True, "history_original_preserved": True,
                      "rules_require_selection": True, "stale_settings_preserve_rules": True,
                      "paused_dock_keeps_task": True, "unsaved_close_cancel_and_discard": True,
                      "cloud_failure_keeps_recording": True, "callback_errors": errors}
            (DATA / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
            print(json.dumps(report), flush=True)
        finally:
            with patch("speak.ui.messagebox.askyesnocancel", return_value=False):
                app.close()
            if helper.poll() is None:
                hwnd = win32gui.FindWindow(None, "Speak automated native input test " + probe_id)
                if hwnd:
                    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                try:
                    helper.wait(3)
                except subprocess.TimeoutExpired:
                    helper.terminate(); helper.wait(3)
            win32api.SetCursorPos(original_cursor)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["gui", "microphone"])
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    if args.worker:
        gui() if args.mode == "gui" else microphone()
    else:
        env = dict(os.environ, SPEAK_DATA_DIR=str(OUT / args.mode))
        subprocess.run([sys.executable, "-X", "utf8", __file__, args.mode, "--worker"], env=env, cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
