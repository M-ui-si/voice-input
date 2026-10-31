"""Real local-model inference with a Tk heartbeat and bounded cancellation.

Uses only the previously generated public synthetic phrase; never a microphone,
cloud endpoint, user recording, or application input field.
"""
from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path
import queue
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/hang-2026-10-31"
os.environ["SPEAK_DATA_DIR"] = str(OUT / "probe-profile")
sys.path.insert(0, str(ROOT))


def main():
    import tkinter as tk
    from speak.local import LocalProvider
    from speak.providers import RecognitionError
    audio = ROOT / "data/cloud-recovery-test/synthetic-phrase.wav"
    assert audio.is_file(), "Generated test phrase is required"
    OUT.mkdir(parents=True, exist_ok=True)
    provider = LocalProvider()
    root = tk.Tk(); root.title("轻声 · 本地识别隔离验证")
    label = tk.Label(root, text="固定合成语音测试；不录音、不上传", padx=20, pady=20)
    label.pack()
    cancel, done = threading.Event(), threading.Event()
    events = queue.Queue()
    ticks, errors, progress = [], [], []
    report = {"scope": "Real local model, fixed generated audio, actual Tk heartbeat; no microphone/cloud/insertion"}
    started = time.monotonic()
    root.report_callback_exception = lambda kind, value, trace: errors.append(kind.__name__)
    def recognize():
        try:
            result = provider.segment(audio, [], "", cancel,
                lambda text: events.put((time.monotonic()-started, text)))
            report.update(status="passed" if result.text.strip() else "empty", device=provider.device,
                          output=result.text, recognition_seconds=result.elapsed)
        except RecognitionError as exc:
            report.update(status="cancelled_timeout" if exc.code == "cancelled" else "failed",
                          error_code=exc.code)
        except Exception as exc:
            report.update(status="failed", error_type=type(exc).__name__)
        finally:
            done.set()
    worker = threading.Thread(target=recognize, daemon=True)
    worker.start()
    def tick():
        ticks.append(time.monotonic())
        while not events.empty():
            stamp, text = events.get_nowait()
            progress.append({"seconds": round(stamp, 2), "stage": text})
            label.configure(text=text)
            print(f"{stamp:.1f}s: {text}", flush=True)
        provider.release_if_idle()
        if done.is_set():
            root.destroy(); return
        if time.monotonic()-started > 75:
            cancel.set(); provider.stop()
        if time.monotonic()-started > 80:
            report.update(status="worker_unresponsive")
            root.destroy(); return
        root.after(25, tick)
    def close():
        cancel.set(); provider.stop()
    root.protocol("WM_DELETE_WINDOW", close)
    root.after(25, tick)
    root.mainloop()
    cancel.set(); provider.stop(); worker.join(2)
    cleanup_started = time.monotonic()
    cleanup_finished = provider.wait_for_cleanup(30)
    report.update(elapsed_seconds=round(cleanup_started-started, 2), ticks=len(ticks),
                  max_tick_gap_seconds=round(max((b-a for a,b in zip(ticks,ticks[1:])), default=0), 3),
                  callback_errors=errors, cleanup_finished=cleanup_finished,
                  cleanup_seconds=round(time.monotonic()-cleanup_started, 2), progress=progress)
    (OUT / "real-local-probe.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if report["status"] != "passed" or errors or not report["cleanup_finished"]:
        raise SystemExit(1)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
