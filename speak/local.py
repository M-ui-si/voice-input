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
        self.state_lock = threading.RLock()
        self.generation = 0
        self.cleanup_threads = set()
        self.busy = False

    def available(self):
        model_ok = all((MODEL_DIR / name).is_file() for name in MODEL_FILES)
        dll_ok = (source_root() / "core/server/engines/qwen_asr_gguf/inference/bin/llama.dll").is_file()
        return model_ok and dll_ok

    def _wait(self, cancel, timeout, process=None, pipe=None, progress=None, stage="本地识别"):
        with self.state_lock:
            process = process or self.process
            pipe = pipe or self.pipe
        started = time.monotonic()
        next_update = started + 5
        while time.monotonic() - started < timeout:
            if cancel.is_set():
                self.stop(expected_process=process)
                raise RecognitionError("cancelled", "已取消")
            with self.state_lock:
                if self.process is not process:
                    raise RecognitionError("cancelled", "本地识别已停止。")
            try:
                if not process or not process.is_alive():
                    raise RecognitionError("local", "本地识别进程已退出，请重试或查看本地准备状态。")
                if pipe.poll(0.1):
                    reply = pipe.recv()
                    if cancel.is_set():
                        self.stop(expected_process=process)
                        raise RecognitionError("cancelled", "已取消")
                    return reply
            except (OSError, EOFError, ValueError):
                if cancel.is_set():
                    raise RecognitionError("cancelled", "已取消") from None
                raise RecognitionError("local", "本地识别连接已关闭，录音已保留，可重试。") from None
            if progress and time.monotonic() >= next_update:
                progress(f"{stage} · 已等待 {int(time.monotonic()-started)} 秒 · 右键可取消")
                next_update = time.monotonic() + 5
        self.stop(expected_process=process)
        raise RecognitionError("timeout", "本地模型加载或识别超时，录音已保留。")

    def start(self, cancel, progress):
        if cancel.is_set():
            raise RecognitionError("cancelled", "已取消")
        with self.state_lock:
            generation = self.generation
            if self.process and self.process.is_alive():
                return self.process, self.pipe
            cleanup_pending = bool(self.cleanup_threads)
        if cleanup_pending:
            progress("正在释放上次本地模型 · 右键可取消")
            deadline = time.monotonic()+30
            while not self.wait_for_cleanup(.1):
                if cancel.is_set():
                    raise RecognitionError("cancelled", "已取消")
                if time.monotonic() >= deadline:
                    raise RecognitionError("local", "上次模型仍在释放，录音已保留，请稍后重试。")
        if not self.available():
            raise RecognitionError("model", "本地模型或运行库未准备好，请在设置中点击“准备本地模型”。")
        for use_gpu in (True, False):
            progress("正在加载本地模型…" if use_gpu else "显卡加载失败，正在尝试 CPU…")
            context = mp.get_context("spawn")
            pipe, child = context.Pipe()
            process = context.Process(target=local_worker, args=(child, use_gpu), daemon=True)
            try:
                process.start()
            except Exception:
                pipe.close(); child.close()
                raise RecognitionError("local", "无法启动本地识别进程，请重试。") from None
            finally:
                child.close()
            with self.state_lock:
                retired = cancel.is_set() or self.generation != generation
                if not retired:
                    self.process, self.pipe = process, pipe
            if retired:
                self._retire(process, pipe)
                raise RecognitionError("cancelled", "本地识别已停止。")
            try:
                reply = self._wait(cancel, 120, process, pipe, progress, "加载本地模型")
                if reply.get("ready"):
                    with self.state_lock:
                        if self.process is not process or cancel.is_set():
                            raise RecognitionError("cancelled", "本地识别已停止。")
                        self.device = reply["device"]
                    return process, pipe
            except RecognitionError as exc:
                if cancel.is_set() or exc.code == "cancelled":
                    raise
            self.stop(expected_process=process)
            with self.state_lock:
                generation = self.generation
        raise RecognitionError("local", "本地模型无法加载，请检查模型和运行库；可切回云端。")

    def segment(self, path, terms, previous, cancel, progress):
        with self.lock:
            self.busy = True
            try:
                process, pipe = self.start(cancel, progress)
                progress(f"本地识别中（{self.device}）…")
                started = time.monotonic()
                try:
                    pipe.send({"path": str(path), "terms": terms, "previous": previous})
                except (OSError, ValueError):
                    raise RecognitionError("local", "本地识别连接已关闭，录音已保留，可重试。") from None
                reply = self._wait(cancel, 180, process, pipe, progress, f"本地识别（{self.device}）")
                if "error" in reply:
                    self.stop(expected_process=process)
                    raise RecognitionError("local", f"本地识别失败（{reply['error']}），录音已保留。")
                return Result(reply["text"], "local", "Qwen3-ASR-1.7B-q5_k", time.monotonic() - started)
            finally:
                self.busy = False
                self.last_used = time.monotonic()

    def stop(self, expected_process=None):
        # Detach once. Never wait for inference/driver teardown on the Tk thread.
        with self.state_lock:
            if expected_process is not None and self.process is not expected_process:
                return
            self.generation += 1
            process, self.process = self.process, None
            pipe, self.pipe = self.pipe, None
            self.device = ""
            graceful = not self.busy
        if process or pipe:
            self._retire(process, pipe, graceful)

    def _retire(self, process, pipe, graceful=False):
        def cleanup():
            try:
                if graceful and pipe and process:
                    try:
                        pipe.send(None)
                        process.join(3)
                    except (OSError, ValueError):
                        pass
                if pipe:
                    pipe.close()
                if process:
                    if process.is_alive():
                        process.terminate()
                    process.join(3)
                    if process.is_alive():
                        process.kill()
                    # Some Windows GPU drivers take longer to finish teardown.
                    # Keep ownership here; close() is only legal after confirmed exit.
                    while process.is_alive():
                        process.join(.2)
                        time.sleep(.01)
                    process.close()
            except (OSError, ValueError, AssertionError) as exc:
                from .diagnostics import runtime_error
                runtime_error("local_cleanup", exc)
            finally:
                with self.state_lock:
                    self.cleanup_threads.discard(threading.current_thread())
        thread = threading.Thread(target=cleanup, name="local-cleanup", daemon=True)
        with self.state_lock:
            self.cleanup_threads.add(thread)
        thread.start()

    def wait_for_cleanup(self, timeout):
        """For probes/shutdown verification only, never call from a Tk callback."""
        deadline = time.monotonic()+timeout
        with self.state_lock:
            threads = list(self.cleanup_threads)
        for thread in threads:
            thread.join(max(0, deadline-time.monotonic()))
        return all(not thread.is_alive() for thread in threads)

    def release_if_idle(self):
        if not self.busy and self.process and time.monotonic() - self.last_used > 300:
            if self.lock.acquire(blocking=False):
                try:
                    if not self.busy:
                        self.stop()
                finally:
                    self.lock.release()
