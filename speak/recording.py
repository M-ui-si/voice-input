from __future__ import annotations

import queue
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path

RATE = 16000
LIVE_CHUNK_SECONDS = 8
LIVE_OVERLAP_SECONDS = 2


@dataclass(frozen=True)
class AudioSegment:
    path: Path
    index: int
    start_frame: int
    end_frame: int
    overlap_frames: int = 0
    break_before: bool = False

    def __str__(self):
        return str(self.path)


class SegmentWriter:
    """Sample-accurate 8-second new-audio windows, shared by live capture and recovery."""
    def __init__(self, path, callback):
        self.path, self.callback = Path(path), callback
        self.buffer = bytearray()
        self.new_bytes = 0
        self.frames = 0
        self.files = []
        self.break_before = False

    def feed(self, block):
        while block:
            take = min(len(block), RATE * 2 * LIVE_CHUNK_SECONDS - self.new_bytes)
            self.buffer.extend(block[:take])
            self.new_bytes += take
            self.frames += take // 2
            block = block[take:]
            if self.new_bytes == RATE * 2 * LIVE_CHUNK_SECONDS:
                self.emit()
                self.buffer = self.buffer[-RATE * 2 * LIVE_OVERLAP_SECONDS:]
                self.new_bytes = 0

    def emit(self):
        if not self.new_bytes:
            return
        path = self.path.with_name(f"{self.path.stem}-seg{len(self.files)}.wav")
        with wave.open(str(path), "wb") as out:
            out.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
            out.writeframes(self.buffer)
        segment = AudioSegment(path, len(self.files), self.frames - len(self.buffer)//2,
                               self.frames, (len(self.buffer)-self.new_bytes)//2, self.break_before)
        self.files.append(path)
        self.break_before = False
        self.callback(segment)

    def flush(self, boundary=False):
        self.emit()
        self.buffer.clear()
        self.new_bytes = 0
        self.break_before = boundary


class Recorder:
    """PortAudio callback never touches disk; bounded queue is drained by one writer."""
    def __init__(self, path: Path, device=None, on_segment=None):
        self.path, self.device, self.on_segment = path, device, on_segment
        self.queue = queue.Queue(maxsize=100)
        self.error = None
        self.level = 0.0
        self.frames = 0
        self.started = 0
        self.stream = None
        self.thread = None
        self.closed = threading.Event()
        self.segments = []
        self.paused = False
        self.accepting = False
        self.capture_lock = threading.Lock()
        self.control_lock = threading.RLock()
        self.segment_writer = SegmentWriter(path, on_segment) if on_segment else None

    def start(self):
        import sounddevice as sd
        def callback(indata, frames, time_info, status):
            with self.capture_lock:
                if not self.accepting:
                    return
                if status:
                    self.error = "麦克风缓冲区溢出，录音可能不完整，请重录。"
                try:
                    self.queue.put_nowait(bytes(indata))
                except queue.Full:
                    self.error = "磁盘写入过慢，录音可能不完整，请结束后重试。"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", device=self.device, blocksize=1600, callback=callback)
        self.thread = threading.Thread(target=self._write, daemon=True)
        self.thread.start()
        try:
            self.accepting = True
            self.stream.start()
            self.started = time.monotonic()
        except Exception:
            self.accepting = False
            self.stream.close()
            self.closed.set()
            self.thread.join(3)
            raise

    def _write(self):
        import array
        try:
            with wave.open(str(self.path), "wb") as out:
                out.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
                while not self.closed.is_set() or not self.queue.empty():
                    try:
                        block = self.queue.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    if isinstance(block, threading.Event):
                        if self.segment_writer:
                            self.segment_writer.flush(boundary=True)
                        out.writeframes(b"")  # Update the WAV header before acknowledging pause.
                        block.set()
                        continue
                    out.writeframes(block)
                    self.frames += len(block) // 2
                    samples = array.array("h", block)
                    self.level = max((abs(v) for v in samples), default=0) / 32768
                    if self.segment_writer:
                        self.segment_writer.feed(block)
                        self.segments = self.segment_writer.files
                if self.segment_writer:
                    self.segment_writer.flush()
                    self.segments = self.segment_writer.files
        except Exception:
            self.error = "录音文件写入失败，请检查磁盘空间。"

    @property
    def duration(self):
        return self.frames / RATE

    def pause(self):
        with self.control_lock:
            if self.paused or self.closed.is_set():
                return
            with self.capture_lock:
                self.accepting = False
            if self.stream:
                self.stream.stop()
            barrier = threading.Event()
            self.queue.put(barrier, timeout=5)
            if not barrier.wait(5):
                raise RuntimeError("recording writer did not pause")
            self.paused, self.level = True, 0.0

    def continue_recording(self):
        with self.control_lock:
            if not self.paused or self.closed.is_set():
                return
            with self.capture_lock:
                self.accepting = True
            try:
                self.stream.start()
            except Exception:
                self.accepting = False
                raise
            self.paused = False

    def stop(self):
        with self.control_lock:
            return self._stop()

    def _stop(self):
        with self.capture_lock:
            self.accepting = False
        if self.stream:
            try:
                self.stream.stop()
            except Exception:
                self.error = self.error or "麦克风停止失败，录音可能不完整。"
            finally:
                try:
                    self.stream.close()
                except Exception:
                    self.error = self.error or "麦克风已断开，录音可能不完整。"
                self.stream = None
        self.closed.set()
        if self.thread:
            self.thread.join(5)
            if self.thread.is_alive():
                self.error = "录音文件仍在写入，请稍后重试。"
        self.level = 0.0
        return self.path


def split_live_wav(path, directory):
    """Recovery uses exactly the live window sizes; source is never changed."""
    pending = []
    writer = SegmentWriter(Path(directory) / f"{Path(path).stem}-retry.wav", pending.append)
    with wave.open(str(path), "rb") as source:
        if (source.getframerate(), source.getnchannels(), source.getsampwidth()) != (RATE, 1, 2):
            raise ValueError("需要 16kHz 单声道 PCM16 WAV")
        while block := source.readframes(RATE):
            writer.feed(block)
            while pending:
                yield pending.pop(0)
        writer.flush()
        yield from pending


def split_wav(path, directory):
    """Bound memory to a 60-second segment; used for retrying a failed cloud recording."""
    with wave.open(str(path), "rb") as source:
        if source.getframerate() != RATE or source.getnchannels() != 1 or source.getsampwidth() != 2:
            raise ValueError("需要 16kHz 单声道 PCM16 WAV")
        n, start, index = source.getnframes(), 0, 0
        while start < n:
            source.setpos(start)
            data = source.readframes(RATE * 60)
            dest = Path(directory) / f"{Path(path).stem}-retry{index}.wav"
            with wave.open(str(dest), "wb") as out:
                out.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
                out.writeframes(data)
            yield dest
            if start + RATE * 60 >= n:
                break
            start += RATE * 56
            index += 1
