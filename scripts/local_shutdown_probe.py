"""Actual App heartbeat under a controlled inference-lock race and shutdown error."""
import json
import os
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/hang-2026-10-31/desktop-profile"
os.environ["SPEAK_DATA_DIR"] = str(OUT)
sys.path.insert(0, str(ROOT))


def main():
    import tkinter as tk
    from speak.storage import Settings
    from speak.ui import App
    OUT.mkdir(parents=True, exist_ok=True)
    Settings(mode="local", hotkey="Ctrl+Alt+F8").save(OUT)
    root = tk.Tk()
    with patch("speak.providers.FreePriceGuard.check", return_value=None):
        app = App(root)
    errors, ticks, destroyed = [], [], []
    root.report_callback_exception = lambda kind, value, trace: errors.append(kind.__name__)
    root.bind("<Destroy>", lambda e: destroyed.append(True) if e.widget is root else None)
    held, release = threading.Event(), threading.Event()
    provider = app.controller.local
    class Process:
        closed = False
        def is_alive(self): return False
        def join(self, timeout=None): pass
        def close(self): self.closed = True
    process = Process()
    provider.process = process
    provider.last_used = time.monotonic()-400
    def inference_race():
        with provider.lock:
            held.set(); release.wait(2)
    worker = threading.Thread(target=inference_race)
    worker.start(); assert held.wait(1)
    started = time.monotonic()
    def heartbeat():
        ticks.append(time.monotonic())
        root.after(10, heartbeat)
    root.after(10, heartbeat)
    root.after(500, release.set)
    def shutdown():
        assert process.closed, "Idle model was not released after the lock became free"
        def fail():
            raise ValueError("controlled still-running process failure")
        app.controller.shutdown = fail
        app.close()
    root.after(1000, shutdown)
    root.mainloop()
    release.set(); worker.join(2)
    assert not errors and destroyed
    gap = max((b-a for a,b in zip(ticks,ticks[1:])), default=0)
    assert gap < .2, f"Tk heartbeat blocked for {gap:.3f}s"
    report = {"status": "passed", "scope": "Actual App/Tk with controlled model-lock race and shutdown fault; no microphone or ASR",
              "ticks": len(ticks), "max_tick_gap_seconds": round(gap, 3), "window_destroyed": True,
              "callback_errors": errors, "elapsed_seconds": round(time.monotonic()-started, 2)}
    (OUT / "report.json").write_text(json.dumps(report, indent=2), "utf-8")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
