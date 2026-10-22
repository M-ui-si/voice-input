"""Live ASR smoke test using an explicitly generated phrase, never user recordings."""
from __future__ import annotations

import argparse
import json
import multiprocessing
import sys
import threading
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from speak.local import LocalProvider
from speak.providers import CloudProvider, RecognitionError
from speak.storage import SecretStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("cloud", "local"), required=True)
    args = parser.parse_args()
    out = ROOT / "data/cloud-recovery-test"
    out.mkdir(parents=True, exist_ok=True)
    audio = out / "synthetic-phrase.wav"
    phrase = "这是轻声语音输入测试，请不要发送消息。"
    if not audio.exists():
        import win32com.client
        voice = win32com.client.Dispatch("SAPI.SpVoice")
        voices = voice.GetVoices()
        for index in range(voices.Count):
            token = voices.Item(index)
            if "804" in token.GetAttribute("Language").split(";"):
                voice.Voice = token
                break
        else:
            phrase = "This is a voice input test. Please do not send the message."
        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        stream.Format.Type = 18  # SAFT16kHz16BitMono.
        stream.Open(str(audio), 3, False)
        try:
            voice.AudioOutputStream = stream
            voice.Speak(phrase)
        finally:
            stream.Close()
        (out / "synthetic-reference.txt").write_text(phrase, "utf-8")
    with wave.open(str(audio), "rb") as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
        seconds = wav.getnframes() / wav.getframerate()
        assert seconds > 1
    report = {"mode": args.mode, "scope": "Generated speech only; no microphone or user audio; no message sent",
              "audio_seconds": seconds, "status": "started"}
    local = LocalProvider()
    started = time.monotonic()
    try:
        cancel = threading.Event()
        if args.mode == "cloud":
            cloud = CloudProvider(SecretStore())
            cloud.guard.check(force=True)
            print("Official free-price check passed; testing existing SenseVoice endpoint.", flush=True)
            result = cloud.transcribe(audio, [], cancel, progress=lambda text: print(text, flush=True))
        else:
            result = local.segment(audio, [], "", cancel, lambda text: print(text, flush=True))
        assert result.text.strip(), "No transcription returned"
        report.update(status="passed", result=result.text, model=result.model, device=local.device)
    except RecognitionError as exc:
        report.update(status="failed", error_code=exc.code, message=str(exc))
    finally:
        local.stop()
        report["total_seconds"] = round(time.monotonic() - started, 2)
        (out / f"{args.mode}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
