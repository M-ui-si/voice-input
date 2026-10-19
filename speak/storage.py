from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
if getattr(sys, "frozen", False) and (ROOT / "portable-root.json").exists():
    try:
        ROOT = (ROOT / json.loads((ROOT / "portable-root.json").read_text("utf-8-sig"))["path"]).resolve()
    except (ValueError, KeyError, OSError, TypeError):
        pass
DATA = Path(os.environ.get("SPEAK_DATA_DIR", str(ROOT / "data")))
DEFAULT_TERMS = ["Codex", "Claude", "GitHub", "API", "MCP", "Python", "IDA", "Ghidra", "GDB", "ELF", "PE", "ROP", "ASLR", "DEP", "UAF", "pwntools", "libc", "Shellcode", "Canary", "堆溢出", "栈溢出", "格式化字符串"]


@dataclass
class Settings:
    mode: str = "cloud"
    hotkey: str = "Ctrl+Alt+Space"
    microphone: int | None = None
    terms: list[str] = field(default_factory=lambda: DEFAULT_TERMS.copy())
    replacements: dict[str, str] = field(default_factory=dict)
    x: int = 80
    y: int = 140
    autostart: bool = False
    cloud_model: str = "FunAudioLLM/SenseVoiceSmall"

    @classmethod
    def load(cls, directory=DATA):
        try:
            raw = json.loads((directory / "settings.json").read_text("utf-8"))
            if not isinstance(raw, dict):
                return cls()
            valid = {
                "mode": lambda v: v in ("cloud", "local"),
                "hotkey": lambda v: isinstance(v, str) and bool(v.strip()),
                "microphone": lambda v: v is None or type(v) is int and v >= 0,
                "terms": lambda v: isinstance(v, list) and all(isinstance(t, str) for t in v),
                "replacements": lambda v: isinstance(v, dict) and all(isinstance(k, str) and isinstance(t, str) for k, t in v.items()),
                "x": lambda v: type(v) is int,
                "y": lambda v: type(v) is int,
                "autostart": lambda v: type(v) is bool,
                "cloud_model": lambda v: v == "FunAudioLLM/SenseVoiceSmall",
            }
            raw = {k: v for k, v in raw.items() if k in valid and valid[k](v)}
            return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, directory=DATA):
        directory.mkdir(parents=True, exist_ok=True)
        temp = directory / "settings.tmp"
        temp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), "utf-8")
        temp.replace(directory / "settings.json")


class SecretStore:
    """DPAPI binds secrets to the current Windows user; never returns them to logs."""
    def __init__(self, directory=DATA):
        self.path = directory / "credential.dpapi"

    def save(self, secret: str):
        import win32crypt
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encrypted = win32crypt.CryptProtectData(secret.strip().encode(), "Speak API key", None, None, None, 0)
        self.path.write_bytes(encrypted)

    def load(self):
        if not self.path.exists():
            return ""
        import win32crypt
        try:
            return win32crypt.CryptUnprotectData(self.path.read_bytes(), None, None, None, 0)[1].decode()
        except Exception:
            return ""

    def delete(self):
        self.path.unlink(missing_ok=True)


class History:
    def __init__(self, directory=DATA):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.audio_dir = self.directory / "audio"
        self.audio_dir.mkdir(exist_ok=True)
        self.path = self.directory / "history.sqlite3"
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS results (task_id TEXT PRIMARY KEY, created REAL, text TEXT, provider TEXT, model TEXT, elapsed REAL)")
        self.clean()

    def connect(self):
        db = sqlite3.connect(self.path)
        db.execute("PRAGMA secure_delete=ON")
        return db

    def add(self, task_id, result):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO results VALUES (?, ?, ?, ?, ?, ?)", (task_id, time.time(), result.text, result.provider, result.model, result.elapsed))

    def list(self):
        with self.connect() as db:
            return db.execute("SELECT task_id, created, text, provider, model, elapsed FROM results ORDER BY created DESC LIMIT 300").fetchall()

    def clean(self, active_paths=()):
        with self.connect() as db:
            db.execute("DELETE FROM results WHERE created < ?", (time.time() - 7 * 86400,))
        for path in self.audio_dir.glob("*.wav"):
            if path not in active_paths and path.stat().st_mtime < time.time() - 86400:
                path.unlink(missing_ok=True)

    def clear(self):
        with self.connect() as db:
            db.execute("DELETE FROM results")
            db.commit()
            db.execute("VACUUM")
        for path in self.audio_dir.glob("*.wav"):
            path.unlink(missing_ok=True)


def apply_replacements(text: str, replacements: dict[str, str]) -> str:
    # One pass avoids cascaded replacements. Rules are explicitly authored by user.
    import re
    # Single-character global substitutions are almost always accidental and can
    # corrupt every occurrence in a transcript (for example, n=notion).
    keys = sorted((k for k in replacements if len(k.strip()) >= 2), key=len, reverse=True)
    if not keys:
        return text.strip()
    def token_pattern(key):
        # Correct an English token without rewriting parts of larger words.
        left = r"(?<![A-Za-z0-9_])" if re.match(r"[A-Za-z0-9_]", key[0]) else ""
        right = r"(?![A-Za-z0-9_])" if re.match(r"[A-Za-z0-9_]", key[-1]) else ""
        return left + re.escape(key) + right
    pattern = "|".join(token_pattern(k) for k in keys)
    return re.sub(pattern, lambda m: replacements[m.group()], text).strip()
