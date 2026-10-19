from __future__ import annotations

import queue
import threading
import time
import wave
from pathlib import Path

RATE = 16000
LIVE_CHUNK_SECONDS = 8
LIVE_OVERLAP_SECONDS = 2


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

    def start(self):
        import sounddevice as sd
        def callback(indata, frames, time_info, status):
            if status:
                self.error = "麦克风缓冲区溢出，录音可能不完整，请重录。"
            try:
                self.queue.put_nowait(bytes(indata))
            except queue.Full:
                self.error = "磁盘写入过慢，录音已暂停。"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", device=self.device, blocksize=1600, callback=callback)
        self.thread = threading.Thread(target=self._write, daemon=True)
        self.thread.start()
        try:
            self.stream.start()
            self.started = time.monotonic()
        except Exception:
            self.stream.close()
            self.closed.set()
            self.thread.join(3)
            raise

    def _write(self):
        import array
        segment = bytearray()
        segment_new = 0
        try:
            with wave.open(str(self.path), "wb") as out:
                out.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
                while not self.closed.is_set() or not self.queue.empty():
                    try:
                        block = self.queue.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    out.writeframes(block)
                    self.frames += len(block) // 2
                    samples = array.array("h", block)
                    self.level = max((abs(v) for v in samples), default=0) / 32768
                    if self.on_segment:
                        segment.extend(block)
                        segment_new += len(block)
                        if segment_new >= RATE * 2 * LIVE_CHUNK_SECONDS:
                            self._segment(segment)
                            segment = segment[-RATE * 2 * LIVE_OVERLAP_SECONDS:]
                            segment_new = 0
                if self.on_segment and segment_new:
                    self._segment(segment)
        except Exception:
            self.error = "录音文件写入失败，请检查磁盘空间。"

    def _segment(self, data):
        path = self.path.with_name(f"{self.path.stem}-seg{len(self.segments)}.wav")
        with wave.open(str(path), "wb") as out:
            out.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
            out.writeframes(data)
        self.segments.append(path)
        self.on_segment(path)

    def stop(self):
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
