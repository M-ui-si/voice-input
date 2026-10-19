"""Preserve installed distribution metadata and supplied license notices in the local bundle."""
import importlib.metadata
import json
import os
import shutil
from pathlib import Path

root = Path(__file__).resolve().parents[1]
destination = Path(os.environ.get("SPEAK_LICENSES_DIR", root / "dist-review/Speak/licenses/python"))
destination.mkdir(parents=True, exist_ok=True)
inventory = []
for dist in importlib.metadata.distributions():
    name = dist.metadata["Name"]
    target = destination / f"{name}-{dist.version}"
    copied = []
    for entry in dist.files or []:
        if any(word in entry.name.lower() for word in ("license", "copying", "notice")):
            source = Path(dist.locate_file(entry))
            if not source.is_file():
                continue
            target.mkdir(parents=True, exist_ok=True)
            # Preserve subpaths to avoid overwriting different bundled license files.
            relative = Path(*[part for part in entry.parts if part not in ("..", ".")])
            output = target / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, output)
            copied.append(str(output.relative_to(destination)))
    inventory.append({"package": name, "version": dist.version,
                      "license": dist.metadata.get("License-Expression") or dist.metadata.get("License"),
                      "notices": copied})
(destination / "inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2), "utf-8")
