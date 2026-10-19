"""Measure the packaged GUI at idle and check dependency loading without recording audio."""
import json
import os
import subprocess
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
exe = ROOT / "dist-review/Speak/Speak.exe"
out = ROOT / "data/package-preview-test"
out.mkdir(parents=True, exist_ok=True)
for name in ("settings.png", "diagnostics.json", "gui.json", "profile/ui-error.log"):
    (out / name).unlink(missing_ok=True)
env = dict(os.environ, SPEAK_DATA_DIR=str(out / "profile"))
diagnostics = out / "diagnostics.json"
subprocess.run([str(exe), "--diagnostics", str(diagnostics)], env=env, cwd=ROOT, timeout=60, check=True)
proc = subprocess.Popen([str(exe), "--settings", "--exit-after", "12", "--screenshot", str(out / "settings.png")], env=env, cwd=ROOT)
samples = []
children = set()
try:
    for _ in range(10):
        time.sleep(1)
        if proc.poll() is not None:
            break
        process = psutil.Process(proc.pid)
        mem = process.memory_info()
        samples.append({"private_mb": round(mem.private / 1024**2, 1), "working_set_mb": round(mem.rss / 1024**2, 1)})
        children.update(p.name() for p in process.children(recursive=True))
    code = proc.wait(timeout=50)
finally:
    if proc.poll() is None:
        proc.terminate()
        proc.wait(timeout=10)
report = {"exe": str(exe), "exit_code": code, "samples": samples,
          "child_process_names": sorted(children), "screenshot_exists": (out / "settings.png").exists(),
          "ui_callback_error": (out / "profile/ui-error.log").exists(),
          "scope": "Packaged settings UI at idle, no microphone recording or ASR call"}
(out / "gui.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
if code != 0 or not samples or not report["screenshot_exists"] or report["ui_callback_error"]:
    raise RuntimeError("Packaged GUI probe failed; inspect its report")
