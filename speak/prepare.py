"""Download pinned upstream runtime and Qwen model, verifying SHA-256 before extraction."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from .storage import ROOT

RELEASE = ("https://github.com/HaujetZhao/CapsWriter-Offline/releases/download/v2.6/CapsWriter-Offline-20260914.zip", "40a46100e2b221ed487d85aa923a38f8f722090bf1fa86975b701baceafbb6f4")
MODEL = ("https://github.com/HaujetZhao/CapsWriter-Offline/releases/download/models/Qwen3-ASR-1.7B-q5_k.zip", "f40040fe62a5ef0c09f8699fdbcb30f18bb8ae2bcd515ed4954e1f62b8b0e88f")
MODEL_DIR = ROOT / "models" / "Qwen3-ASR-1.7B"
MODEL_FILES = ("qwen3_asr_encoder_frontend.onnx", "qwen3_asr_encoder_backend.onnx", "qwen3_asr_llm.gguf")


def fetch(url, digest, directory, progress=print, cancel=None):
    cancel = cancel or threading.Event()
    failed = threading.Event()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / url.rsplit("/", 1)[1]
    if path.exists() and checksum(path) == digest:
        return path
    part = path.with_suffix(".part")
    with requests.get(url, headers={"Range": "bytes=0-0"}, stream=True, timeout=(15, 40)) as response:
        response.raise_for_status()
        if response.status_code != 206 or not response.headers.get("Content-Range", "").startswith("bytes 0-0/"):
            raise ValueError("下载源不支持安全续传，请稍后重试或导入本地模型。")
        total = int(response.headers["Content-Range"].split("/")[-1])
        download_url = response.url
    chunks = directory / (path.name + ".chunks")
    chunks.mkdir(exist_ok=True)
    size = 4 * 1024 * 1024
    def download_chunk(index):
        start, end = index * size, min(total, (index + 1) * size) - 1
        chunk_path = chunks / str(index)
        for attempt in range(3):
            if cancel.is_set() or failed.is_set():
                raise InterruptedError("下载已停止，进度已保留")
            have = chunk_path.stat().st_size if chunk_path.exists() else 0
            if have == end - start + 1:
                return chunk_path
            if have > end - start + 1:
                chunk_path.unlink(); have = 0
            try:
                started = time.monotonic()
                with requests.get(download_url, headers={"Range": f"bytes={start + have}-{end}"}, stream=True, timeout=(15, 40)) as response:
                    response.raise_for_status()
                    if response.status_code != 206 or response.headers.get("Content-Range") != f"bytes {start + have}-{end}/{total}":
                        raise ValueError("服务器返回了错误的文件片段")
                    with chunk_path.open("ab") as out:
                        for block in response.iter_content(65536):
                            if cancel.is_set() or failed.is_set():
                                raise InterruptedError("下载已停止，进度已保留")
                            out.write(block)
                            if time.monotonic() - started > 120:
                                raise requests.Timeout("片段下载超时")
            except requests.RequestException:
                if attempt == 2: raise
                cancel.wait(1 + attempt)
        if chunk_path.stat().st_size != end - start + 1:
            raise ValueError("下载片段不完整")
        return chunk_path
    count = (total + size - 1) // size
    workers = max(1, min(32, int(os.environ.get("SPEAK_DOWNLOAD_WORKERS", "8"))))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs = [pool.submit(download_chunk, i) for i in range(count)]
        last = 0
        try:
            for completed, job in enumerate(as_completed(jobs), 1):
                job.result()
                if time.monotonic() - last > 3:
                    progress(f"下载 {path.name}：{completed}/{count} 段")
                    last = time.monotonic()
        except BaseException:
            failed.set()
            for job in jobs:
                job.cancel()
            raise
    with part.open("wb") as out:
        for index in range(count):
            with (chunks / str(index)).open("rb") as source: shutil.copyfileobj(source, out)
    progress(f"校验 {path.name}")
    if checksum(part) != digest:
        part.unlink(missing_ok=True)
        # Do not reuse the same corrupted cached chunks on every retry.
        for index in range(count):
            (chunks / str(index)).unlink(missing_ok=True)
        raise ValueError("下载文件校验不符，已删除，请重试。")
    part.replace(path)
    # Only known numeric chunk files under this download's private directory.
    for index in range(count): (chunks / str(index)).unlink(missing_ok=True)
    chunks.rmdir()
    return path


def checksum(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def extract_checked(archive, destination):
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        for member in z.infolist():
            target = (destination / member.filename).resolve()
            if not target.is_relative_to(destination):
                raise ValueError("压缩包包含越界路径")
        z.extractall(destination)


def prepare(progress=print, cancel=None):
    import msvcrt
    downloads = ROOT / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    with (downloads / "prepare.lock").open("a+b") as lock:
        lock.seek(0)
        if not lock.read(1):
            lock.write(b"0"); lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise ValueError("另一个窗口正在准备模型，请等待其结束。") from None
        try:
            _prepare(progress, cancel)
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def _prepare(progress, cancel):
    downloads = ROOT / "downloads"
    runtime_dir = ROOT / "runtime"
    if not (runtime_dir / "prepared.json").exists():
        archive = fetch(*RELEASE, downloads, progress, cancel)
        extract_checked(archive, runtime_dir)
        (runtime_dir / "prepared.json").write_text(json.dumps({"url": RELEASE[0], "sha256": RELEASE[1]}), "utf-8")
    # Match the DLL ABI shipped with the pinned upstream release.
    binaries = list(runtime_dir.rglob("llama.dll"))
    preferred = [p for p in binaries if "qwen_asr_gguf" in str(p)]
    binary = preferred[0] if preferred else (binaries[0] if binaries else None)
    if binary is None:
        raise ValueError("上游运行库中缺少 llama.dll")
    import sys
    source_root = Path(getattr(sys, "_MEIPASS", ROOT)) / "upstream"
    for relative in ("core/server/engines/llama/bin", "core/server/engines/qwen_asr_gguf/inference/bin"):
        destination = source_root / relative
        destination.mkdir(parents=True, exist_ok=True)
        for dll in binary.parent.iterdir():
            if dll.is_file() and dll.suffix.lower() == ".dll": shutil.copy2(dll, destination / dll.name)
    if not (MODEL_DIR / "prepared.json").exists() or not all((MODEL_DIR / name).is_file() for name in MODEL_FILES):
        archive = fetch(*MODEL, downloads, progress, cancel)
        staging = ROOT / "models" / "extracted"
        extract_checked(archive, staging)
        candidates = list(staging.rglob("qwen3_asr_llm.gguf"))
        if len(candidates) != 1:
            raise ValueError("模型目录结构不符，请手动导入模型目录。")
        source = candidates[0].parent
        if not all((source / name).exists() for name in MODEL_FILES):
            raise ValueError("模型文件不完整")
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        for path in source.iterdir():
            if path.is_file():
                shutil.move(str(path), str(MODEL_DIR / path.name))
        (MODEL_DIR / "prepared.json").write_text(json.dumps({"url": MODEL[0], "sha256": MODEL[1]}), "utf-8")
    progress("本地模型准备完成，尚未加载到内存。")


if __name__ == "__main__":
    prepare(lambda s: print(s, flush=True))
