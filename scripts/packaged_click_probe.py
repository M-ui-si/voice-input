"""Stress the packaged edge handle with an isolated cloud profile without a key."""
import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ctypes.windll.shcore.SetProcessDpiAwareness(2)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import win32api
import win32con
import win32gui
import win32process
from speak.storage import Settings


def main():
    out = ROOT / "data/rapid-click-test"
    out.mkdir(parents=True, exist_ok=True)
    report_path = out / "packaged.json"
    report_path.unlink(missing_ok=True)
    cursor = win32api.GetCursorPos()
    clicks = 0
    try:
        for side in ("left", "right"):
            profile = out / ("packaged-" + side)
            profile.mkdir(exist_ok=True)
            assert not (profile / "credential.dpapi").exists(), "Must not use a credential"
            (profile / "ui-error.log").unlink(missing_ok=True)
            Settings(mode="cloud", collapsed=True, dock_side=side, x=80, y=300,
                     hotkey="Ctrl+Alt+F9").save(profile)
            env = dict(os.environ, SPEAK_DATA_DIR=str(profile))
            proc = subprocess.Popen([str(ROOT / "dist-review/Speak/Speak.exe"), "--exit-after", "20"],
                                    cwd=ROOT, env=env)
            try:
                hwnd = None
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    handles = []
                    win32gui.EnumWindows(lambda h, _: handles.append(h) if
                        win32process.GetWindowThreadProcessId(h)[1] == proc.pid and
                        win32gui.GetWindowText(h) == "轻声" and win32gui.IsWindowVisible(h) else None, None)
                    if handles:
                        hwnd = handles[0]
                        l, t, r, b = win32gui.GetWindowRect(hwnd)
                        if (r - l, b - t) == (22, 56):
                            break
                    time.sleep(.05)
                assert hwnd and (r - l, b - t) == (22, 56), "Packaged handle not ready"
                def click(point):
                    hit = win32gui.WindowFromPoint(point)
                    assert win32gui.GetAncestor(hit, win32con.GA_ROOT) == hwnd, "Input intercepted"
                    win32api.SetCursorPos(point)
                    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)
                    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0)
                for _ in range(5):
                    time.sleep(.45)
                    l, t, r, b = win32gui.GetWindowRect(hwnd)
                    assert (r - l, b - t) == (22, 56)
                    point = (l + 11, t + 28)
                    for _ in range(6):
                        click(point)
                        clicks += 1
                        time.sleep(.025)
                    time.sleep(.1)
                    l, t, r, b = win32gui.GetWindowRect(hwnd)
                    assert (r - l, b - t) == (286, 78), "Burst did not leave a usable expanded strip"
                    visible = []
                    win32gui.EnumWindows(lambda h, _: visible.append(h) if
                        win32process.GetWindowThreadProcessId(h)[1] == proc.pid and
                        win32gui.IsWindowVisible(h) else None, None)
                    assert visible == [hwnd], "Unexpected menu, settings, or error dialog"
                    assert not ctypes.windll.user32.IsHungAppWindow(hwnd)
                    time.sleep(.3)
                    click((l + 232, t + 30))
                    time.sleep(.12)
                assert not (profile / "ui-error.log").exists()
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                assert proc.wait(timeout=5) == 0
            finally:
                if proc.poll() is None:
                    # This is only the subprocess created above with our private profile.
                    proc.terminate()
                    proc.wait(timeout=5)
        report = {"status": "passed", "rapid_edge_clicks": clicks, "sides": ["left", "right"],
                  "no_extra_windows": True, "ui_responsive": True,
                  "scope": "Packaged edge clicks only; private cloud profile has no API key; no microphone or ASR"}
        report_path.write_text(json.dumps(report, indent=2), "utf-8")
        print(json.dumps(report))
    finally:
        win32api.SetCursorPos(cursor)


if __name__ == "__main__":
    main()
