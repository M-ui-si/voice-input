from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

from .storage import DATA

SENSEVOICE = "FunAudioLLM/SenseVoiceSmall"
QWEN = "Qwen/Qwen3-ASR-1.7B"
PRICING_URL = "https://siliconflow.cn/pricing"
TRANSCRIBE_URL = "https://api.siliconflow.cn/v1/audio/transcriptions"


class RecognitionError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


@dataclass
class Result:
    text: str
    provider: str
    model: str
    elapsed: float


def shanghai_day():
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def price_is_free(html: str, model: str) -> bool:
    """Fail closed: require an exact official model link and its own free-only price row."""
    from bs4 import BeautifulSoup
    import re
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.find_all("a", title=model)
    if not anchors:
        return False
    decisions = []
    for anchor in anchors:
        row = anchor
        decision = False
        for _ in range(7):
            row = row.parent
            if not row or row.name in ("body", "html"):
                break
            model_links = [a for a in row.find_all("a", title=True) if "models?target=" in a.get("href", "")]
            if len(model_links) != 1:
                break
            words = list(row.stripped_strings)
            text = " ".join(words)
            if "免费" in words:
                decision = not bool(re.search(r"[¥￥$]|收费|限时|试用|赠送|额度", text))
                break
        decisions.append(decision)
    return bool(decisions) and all(decisions)


class FreePriceGuard:
    def __init__(self, directory=DATA, session=None):
        self.directory = Path(directory)
        self.session = session or requests.Session()
        self.verified = {}  # In-process only: recheck every launch and each new Shanghai day.
        self.lock = threading.Lock()

    def check(self, model=SENSEVOICE, force=False):
        if model not in (SENSEVOICE, QWEN):
            raise RecognitionError("price", "该模型未列入免费模型名单。")
        with self.lock:
            if not force and self.verified.get(model) == shanghai_day():
                return
            try:
                response = self.session.get(PRICING_URL, timeout=(8, 20))
                response.raise_for_status()
                html = response.content.decode("utf-8")
            except (requests.RequestException, UnicodeError):
                raise RecognitionError("price", "暂时无法核实官方免费价格。可重试，或选择本地识别。") from None
            if not price_is_free(html, model):
                self.verified.pop(model, None)
                raise RecognitionError("price", "官方页面未能确认该模型免费，已暂停云端调用。")
            self.directory.mkdir(parents=True, exist_ok=True)
            (self.directory / "price-check.json").write_text(json.dumps({"model": model, "checked_at": datetime.now(timezone.utc).isoformat(), "source": PRICING_URL, "sha256": hashlib.sha256(response.content).hexdigest(), "free": True}, ensure_ascii=False, indent=2), "utf-8")
            self.verified[model] = shanghai_day()


class CloudProvider:
    def __init__(self, secrets, guard=None):
        self.secrets = secrets
        self.guard = guard or FreePriceGuard()

    def transcribe(self, path, terms, cancel, model=SENSEVOICE):
        if model != SENSEVOICE:
            raise RecognitionError("unvalidated", "Qwen 云端尚未完成接口及准确率验证，暂不启用。")
        key = self.secrets.load()
        if not key:
            raise RecognitionError("key", "请先在设置中保存硅基流动 API Key。")
        self.guard.check(model)
        if cancel.is_set():
            raise RecognitionError("cancelled", "已取消")
        started = time.monotonic()
        try:
            with open(path, "rb") as audio:
                response = requests.post(TRANSCRIBE_URL, headers={"Authorization": f"Bearer {key}"}, files={"file": ("recording.wav", audio, "audio/wav")}, data={"model": model}, timeout=(10, 90))
        except requests.RequestException:
            raise RecognitionError("network", "云端连接失败或超时，录音已保留，可重试或选择本地识别。") from None
        if cancel.is_set():
            raise RecognitionError("cancelled", "已取消")
        if response.status_code != 200:
            code, message = {401: ("key", "API Key 无效，请在设置中更新。"), 403: ("permission", "账号无法使用服务，请检查实名认证和访问权限。"), 429: ("limit", "云端限流或额度不足，可稍后重试或改用本地。"), 402: ("quota", "免费服务不可用，已停止请求，请选择本地识别。")}.get(response.status_code, ("service", f"云端服务暂不可用（HTTP {response.status_code}），录音已保留。"))
            raise RecognitionError(code, message)
        try:
            text = response.json()["text"]
            if not isinstance(text, str):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise RecognitionError("response", "云端返回格式异常，录音已保留。") from None
        # API has no documented hotword parameter: deliberately do not invent one.
        return Result(text.strip(), "cloud", model, time.monotonic() - started)
