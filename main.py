from __future__ import annotations

import multiprocessing
import os
import sys
import hashlib
from pathlib import Path


def instance_name(data_dir, username):
    profile = str(Path(data_dir).resolve()).casefold().encode("utf-8")
    return "Local\\SpeakDesktopUser_" + username + "_" + hashlib.sha256(profile).hexdigest()[:16]


def main():
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    import argparse
    import tkinter as tk
    import win32api
    import win32event
    from speak.storage import DATA
    parser = argparse.ArgumentParser()
    parser.add_argument("--settings", action="store_true")
    parser.add_argument("--prepare-local", action="store_true")
    parser.add_argument("--exit-after", type=float, default=0)
    parser.add_argument("--screenshot")
    parser.add_argument("--diagnostics")
    args = parser.parse_args()
    if args.diagnostics:
        from speak.diagnostics import diagnose
        diagnose(args.diagnostics)
        return
    DATA.mkdir(parents=True, exist_ok=True)
    mutex = win32event.CreateMutex(None, False, instance_name(DATA, os.environ.get("USERNAME", "default")))
    if win32api.GetLastError() == 183:
        (DATA / "show.request").touch()
        return
    from speak.ui import App
    root = tk.Tk()
    # Match Tk geometry to physical pixels on this desktop; fonts remain legible.
    root.tk.call("tk", "scaling", 1.3333333333)
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.close)
    def callback_error(kind, value, trace):
        import traceback
        # Stack locations and exception class only; never serialize credential or transcript values.
        (DATA / "ui-error.log").write_text(kind.__name__ + "\n" + "".join(traceback.format_tb(trace)), "utf-8")
        app.status = "界面操作失败 · 可重启软件"
    root.report_callback_exception = callback_error
    if args.settings or args.prepare_local or not (DATA / "settings.json").exists():
        root.after(200, app.open_settings)
    if args.prepare_local:
        root.after(500, app.prepare_local)
    if args.screenshot:
        def capture():
            from PIL import ImageGrab
            window = app.settings_window or root
            ImageGrab.grab(bbox=(window.winfo_rootx(), window.winfo_rooty(), window.winfo_rootx() + window.winfo_width(), window.winfo_rooty() + window.winfo_height())).save(args.screenshot)
        root.after(1600, capture)
    if args.exit_after:
        root.after(int(args.exit_after * 1000), app.close)
    root.mainloop()
    win32api.CloseHandle(mutex)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
