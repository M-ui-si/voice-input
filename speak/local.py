from __future__ import annotations

import multiprocessing as mp
import os
import sys
import threading
import time
import wave
from pathlib import Path

from .providers import RecognitionError, Result
from .storage import ROOT
from .prepare import MODEL_DIR, MODEL_FILES


def source_root():
    return Path(getattr(sys, "_MEIPASS", ROOT)) / "upstream"


def local_worker(connection, use_gpu):
    """Private pipe rather than an exposed server. Process exit reliably frees GPU allocations."""
    sink = open(os.devnull, "w")
    sys.stdout = sys.stderr = sink
    try:
        sys.path.insert(0, str(source_root()))
        import config_server
        config_server.ServerConfig.log_level = "ERROR"
        from core.server.engines.qwen_asr_gguf.asr_engine import QwenASREngine
        from core.server.engines.qwen_asr_gguf.inference.schema import ASREngineConfig
        from core.server.merger.text_merger import merge_by_text
        import numpy as np
        os.chdir(ROOT)
        cfg = ASREngineConfig(model_dir=str(MODEL_DIR), encoder_frontend_fn=MODEL_FILES[0], encoder_backend_fn=MODEL_FILES[1], llm_fn=MODEL_FILES[2], onnx_provider="DML" if use_gpu else "CPU", llm_use_gpu=use_gpu, chunk_size=60, n_ctx=4096, dml_pad_to=10, verbose=False)
        engine = QwenASREngine(cfg)
        connection.send({"ready": True, "device": "GPU" if use_gpu else "CPU"})
        while True:
            message = connection.recv()
            if message is None:
                break
            try:
                with wave.open(message["path"], "rb") as audio:
                    if audio.getframerate() != 16000 or audio.getnchannels() != 1 or audio.getsampwidth() != 2 or audio.getnframes() > 16000 * 60:
                        raise ValueError("audio_format")
                    samples = np.frombuffer(audio.readframes(audio.getnframes()), np.int16).astype(np.float32) / 32768
                text = ""
                if samples.size and float(np.max(np.abs(samples))) > 0.001:
                    stream = engine.create_stream()
                    stream.accept_waveform(16000, samples)
                    # Passing the entire personal glossary as decoder context made the
                    # model emit the glossary itself. Keep transcription faithful until
                    # term-by-term context behavior can be verified against real speech.
                    engine.decode_stream(stream, context="", language="auto", temperature=0.0)
                    text = stream.result.text.strip()
                merged = merge_by_text(message.get("previous", ""), text)
                connection.send({"text": merged})
            except Exception as exc:
                connection.send({"error": type(exc).__name__})
    except Exception as exc:
        try:
            connection.send({"error": type(exc).__name__})
        except Exception:
            pass
    finally:
        connection.close()


class LocalProvider:
    def __init__(self):
        self.process = None
        self.pipe = None
        self.device = ""
        self.last_used = 0
        self.lock = threading.RLock()
        self.busy = False

    def available(self):
        model_ok = all((MODEL_DIR / name).is_file() for name in MODEL_FILES)
        dll_ok = (source_root() / "core/server/engines/qwen_asr_gguf/inference/bin/llama.dll").is_file()
        return model_ok and dll_ok

    def _wait(self, cancel, timeout):
        started = time.monotonic()
        while time.monotonic() - started < timeout:
            if cancel.is_set():
                self.stop()
                raise RecognitionError("cancelled", "已取消")
            if not self.process or not self.process.is_alive():
                raise RecognitionError("local", "本地识别进程已退出，请重试或查看本地准备状态。")
            if self.pipe.poll(0.1):
                try:
                    return self.pipe.recv()
                except EOFError:
                    break
        self.stop()
        raise RecognitionError("timeout", "本地模型加载或识别超时，录音已保留。")

    def start(self, cancel, progress):
        if cancel.is_set():
            raise RecognitionError("cancelled", "已取消")
        if self.process and self.process.is_alive():
            return
        if not self.available():
            raise RecognitionError("model", "本地模型或运行库未准备好，请在设置中点击“准备本地模型”。")
        for use_gpu in (True, False):
            progress("正在加载本地模型…" if use_gpu else "显卡加载失败，正在尝试 CPU…")
            context = mp.get_context("spawn")
            self.pipe, child = context.Pipe()
            self.process = context.Process(target=local_worker, args=(child, use_gpu), daemon=True)
            self.process.start()
            child.close()
            try:
                reply = self._wait(cancel, 120)
                if reply.get("ready"):
                    self.device = reply["device"]
                    return
            except RecognitionError:
                if cancel.is_set():
                    raise
            self.stop()
        raise RecognitionError("local", "本地模型无法加载，请检查模型和运行库；可切回云端。")

    def segment(self, path, terms, previous, cancel, progress):
        with self.lock:
            self.busy = True
            try:
                self.start(cancel, progress)
                progress(f"本地识别中（{self.device}）…")
                started = time.monotonic()
                self.pipe.send({"path": str(path), "terms": terms, "previous": previous})
                reply = self._wait(cancel, 180)
                if "error" in reply:
                    self.stop()
                    raise RecognitionError("local", f"本地识别失败（{reply['error']}），录音已保留。")
                return Result(reply["text"], "local", "Qwen3-ASR-1.7B-q5_k", time.monotonic() - started)
            finally:
                self.busy = False
                self.last_used = time.monotonic()

    def stop(self):
        process, self.process = self.process, None
        pipe, self.pipe = self.pipe, None
        if process:
            if process.is_alive():
                process.terminate()
            process.join(3)
            if process.is_alive():
                process.kill()
                process.join(2)
            process.close()
        if pipe:
            pipe.close()
        self.device = ""

    def release_if_idle(self):
        if not self.busy and self.process and time.monotonic() - self.last_used > 300:
            with self.lock:
                if not self.busy:
                    self.stop()
