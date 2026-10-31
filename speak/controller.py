from __future__ import annotations

import copy
import queue
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .local import LocalProvider
from .providers import CloudProvider, RecognitionError, Result
from .recording import AudioSegment, Recorder, split_wav, split_live_wav
from .merging import merge_cloud_text
from .storage import History, SecretStore, apply_replacements


@dataclass
class Task:
    id: str
    path: Path
    settings: object
    target: object = None
    cancel: threading.Event = field(default_factory=threading.Event)
    stopped: threading.Event = field(default_factory=threading.Event)
    segments: queue.Queue = field(default_factory=queue.Queue)
    target_window: tuple | None = None
    recorder: Recorder | None = None
    files: list = field(default_factory=list)
    terminal_accepted: bool = False
    preserve_audio: bool = False
    error_code: str | None = None
    cloud_error: RecognitionError | None = None
    processed_segments: int = 0
    review_boundaries: list[int] = field(default_factory=list)


class Controller:
    def __init__(self, settings, events):
        self.settings = settings
        self.events = events
        self.history = History()
        self.secrets = SecretStore()
        self.cloud = CloudProvider(self.secrets)
        self.local = LocalProvider()
        self.task = None
        self.state = "idle"
        self.last_text = ""

    def emit(self, task, kind, value):
        self.events.put((task.id if task else None, kind, value))

    def start(self, target, target_window=None):
        if self.state in ("recording", "paused", "processing"):
            return
        config = copy.deepcopy(self.settings)
        if config.mode == "cloud" and not self.secrets.load():
            raise RecognitionError("key", "先打开设置，保存你的硅基流动 API Key。")
        if config.mode == "local" and not self.local.available():
            raise RecognitionError("model", "请先在设置中准备本地模型。")
        task_id = uuid.uuid4().hex
        task = Task(task_id, self.history.audio_dir / f"{task_id}.wav", config, target)
        task.target_window = target_window
        segmented = config.mode == "local" or config.cloud_segmented
        task.recorder = Recorder(task.path, config.microphone, task.segments.put if segmented else None)
        self.history.clean()
        try:
            task.recorder.start()
        except Exception:
            task.path.unlink(missing_ok=True)
            raise RecognitionError("microphone", "无法打开麦克风。请检查 Windows 麦克风权限，并在设置中选择设备。") from None
        self.task, self.state = task, "recording"
        threading.Thread(target=self._run, args=(task,), daemon=True).start()

    def stop_recording(self):
        if self.state not in ("recording", "paused"):
            return
        task = self.task
        self.state = "processing"
        task.recorder.stop()
        task.stopped.set()
        task.segments.put(None)

    def pause_recording(self):
        if self.state != "recording":
            return
        task = self.task
        try:
            task.recorder.pause()
            self.state = "paused"
        except Exception:
            task.recorder.error = "暂停麦克风失败，已有录音已保留。"
            self.stop_recording()

    def continue_recording(self):
        if self.state != "paused":
            return
        task = self.task
        try:
            task.recorder.continue_recording()
            self.state = "recording"
        except Exception:
            task.recorder.error = "无法重新打开麦克风，已有录音已保留，可重试识别。"
            self.stop_recording()

    @staticmethod
    def _live_segments(task):
        while not task.cancel.is_set():
            try:
                segment = task.segments.get(timeout=0.2)
            except queue.Empty:
                continue
            if segment is None:
                break
            yield segment

    def _run(self, task, retry=False):
        progress = lambda value: self.emit(task, "progress", value)
        try:
            segmented = task.settings.mode == "local" or task.settings.cloud_segmented
            if segmented:
                text, elapsed = "", 0.0
                if retry:
                    segments = (split_wav(task.path, self.history.audio_dir) if task.settings.mode == "local"
                                else split_live_wav(task.path, self.history.audio_dir))
                else:
                    segments = self._live_segments(task)
                for item in segments:
                    segment = item if isinstance(item, AudioSegment) else None
                    path = segment.path if segment else item
                    task.files.append(path)
                    if task.cancel.is_set():
                        return
                    if task.settings.mode == "local":
                        boundary = segment is not None and segment.break_before
                        result = self.local.segment(path, task.settings.terms, "" if boundary else text, task.cancel, progress)
                        text = merge_cloud_text(text, result.text)[0] if boundary else result.text
                    else:
                        try:
                            progress(f"云端识别第 {task.processed_segments + 1} 段…")
                            result = self.cloud.transcribe(path, task.settings.terms, task.cancel,
                                                           task.settings.cloud_model, progress=progress)
                        except RecognitionError as exc:
                            if task.cancel.is_set():
                                return
                            task.cloud_error = exc
                            self.emit(task, "cloud_paused", str(exc))
                            # Keep capturing the complete recording; do not return partial success.
                            while not task.stopped.wait(.1):
                                if task.cancel.is_set():
                                    return
                            raise exc
                        if task.cancel.is_set():
                            return
                        text, review = merge_cloud_text(text, result.text, segment.overlap_frames if segment else 0)
                        if review:
                            task.review_boundaries.append(segment.index + 1 if segment else task.processed_segments + 1)
                    elapsed += result.elapsed
                    task.processed_segments += 1
                    self.emit(task, "preview", apply_replacements(text, task.settings.replacements))
                    label = "云端" if task.settings.mode == "cloud" else "本地"
                    progress(f"{label}已处理 {task.processed_segments} 段 · 待处理 {task.segments.qsize() if not retry else '恢复中'}"
                             + (" · 分段边界请检查" if task.review_boundaries else ""))
                    path.unlink(missing_ok=True)
                result = Result(text, task.settings.mode,
                                "Qwen3-ASR-1.7B-q5_k" if task.settings.mode == "local" else task.settings.cloud_model,
                                elapsed, tuple(task.review_boundaries))
            else:
                while not task.stopped.wait(0.1):
                    if task.cancel.is_set():
                        return
                if task.recorder and task.recorder.error:
                    raise RecognitionError("recording", task.recorder.error)
                progress("上传并识别中…")
                result = self.cloud.transcribe(task.path, task.settings.terms, task.cancel, task.settings.cloud_model, progress=progress)
            if task.cancel.is_set():
                return
            if task.recorder and task.recorder.error:
                raise RecognitionError("recording", task.recorder.error)
            result.text = apply_replacements(result.text, task.settings.replacements)
            if not result.text:
                raise RecognitionError("empty", "没有识别到文字。录音已保留，请检查麦克风后重试。")
            self.history.add(task.id, result)
            self._delete_audio(task)
            self.emit(task, "result", result)
        except RecognitionError as exc:
            if not task.cancel.is_set():
                task.error_code = exc.code
                self.emit(task, "error", RecognitionError(exc.code, str(exc)))
        except Exception as exc:
            if not task.cancel.is_set():
                task.error_code = "internal"
                self.emit(task, "error", f"处理失败（{type(exc).__name__}），录音已保留，可重试。")
        finally:
            if task.cancel.is_set() and not task.preserve_audio:
                self._delete_audio(task)

    @staticmethod
    def _delete_audio(task):
        for path in [task.path, *task.files, *(task.recorder.segments if task.recorder else [])]:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def accept_event(self, task_id, terminal=False):
        task = self.task
        if task is None or task.id != task_id or task.cancel.is_set() or task.terminal_accepted:
            return False
        if terminal:
            task.terminal_accepted = True
        return True

    def retry(self, mode):
        old = self.task
        if self.state != "error" or not old or not old.path.exists():
            return
        config = copy.deepcopy(self.settings)
        config.mode = mode
        task = Task(uuid.uuid4().hex, old.path, config, old.target)
        task.target_window = old.target_window
        # A successful retry or cancellation must also remove chunks created by
        # the failed recording, not only the new retry chunks.
        task.files = list(dict.fromkeys([*old.files, *(old.recorder.segments if old.recorder else [])]))
        task.stopped.set()
        self.task, self.state = task, "processing"
        threading.Thread(target=self._run, args=(task, True), daemon=True).start()

    def resume(self, path, mode):
        if self.state in ("recording", "paused", "processing"):
            return
        config = copy.deepcopy(self.settings)
        config.mode = mode
        # Recovery owns only a private copy, even when the picker selects an external file.
        task_id = uuid.uuid4().hex
        owned_path = self.history.audio_dir / f"{task_id}.wav"
        try:
            shutil.copyfile(path, owned_path)
        except OSError:
            owned_path.unlink(missing_ok=True)
            raise RecognitionError("recording", "无法读取录音，请检查文件是否存在及磁盘空间。") from None
        task = Task(task_id, owned_path, config, None)
        task.stopped.set()
        self.task, self.state = task, "processing"
        threading.Thread(target=self._run, args=(task, True), daemon=True).start()

    def cancel(self):
        task = self.task
        if task:
            task.cancel.set()
            if task.recorder:
                task.recorder.stop()
            task.stopped.set()
            task.segments.put(None)
            if not task.preserve_audio:
                self._delete_audio(task)
        self.state = "idle"

    def shutdown(self):
        if self.task and (self.state == "error" or self.task.cloud_error is not None
                          or self.task.error_code in ("empty", "empty_audio")):
            # Closing an error window/app must honor "recording retained".
            # Explicit discard still uses cancel() with the default deletion behavior.
            self.task.preserve_audio = True
        try:
            self.cancel()
        finally:
            self.local.stop()
