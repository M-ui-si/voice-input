"""Generate public test speech and compare whole vs real-time segmented cloud ASR.

Never reads user recordings or transcript history. Uses the saved key only for ASR.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import wave

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/streaming-cloud-test"


def generate(durations):
    import win32com.client
    OUT.mkdir(parents=True, exist_ok=True)
    voice = win32com.client.Dispatch("SAPI.SpVoice")
    voices = voice.GetVoices()
    for i in range(voices.Count):
        token = voices.Item(i)
        if "804" in token.GetAttribute("Language").split(";"):
            voice.Voice = token
            break
    else:
        raise RuntimeError("Chinese SAPI voice is required for this probe")
    clips, references, count = [], [], 0
    frames = 0
    while frames < max(durations)*16000:
        count += 1
        phrase = f"这是第{count}项检查。请保留数字{count}，不要自动发送消息。继续检查接口和服务，确认完成后再告诉我。"
        path = OUT / f"phrase-{count}.wav"
        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        stream.Format.Type = 18
        stream.Open(str(path), 3, False)
        try:
            voice.AudioOutputStream = stream
            voice.Speak(phrase)
        finally:
            stream.Close()
        with wave.open(str(path), "rb") as source:
            assert (source.getframerate(), source.getnchannels(), source.getsampwidth()) == (16000, 1, 2)
            clip = source.readframes(source.getnframes()) + b"\0\0"*3200
        clips.append(clip); references.append(phrase); frames += len(clip)//2
    samples = []
    for seconds in durations:
        blocks, refs, size = [], [], 0
        for clip, reference in zip(clips, references):
            if size+len(clip)//2 > seconds*16000:
                break
            blocks.append(clip); refs.append(reference); size += len(clip)//2
        path = OUT / f"synthetic-{seconds}s.wav"
        with wave.open(str(path), "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            for block in blocks:
                audio.writeframes(block)
            audio.writeframes(b"\0\0"*(seconds*16000-size))
        samples.append({"audio": str(path), "reference": "".join(refs), "terms": [],
                        "critical": ["不要", "发送"], "split": "acceptance", "source": "Windows SAPI generated speech"})
    manifest = OUT / "manifest.json"
    manifest.write_text(json.dumps(samples, ensure_ascii=False, indent=2), "utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--durations", default="60,180,300")
    parser.add_argument("--generate-only", action="store_true")
    args = parser.parse_args()
    durations = [int(value) for value in args.durations.split(",")]
    if not durations or min(durations) < 1 or max(durations) > 300:
        parser.error("durations must be within 1..300 seconds")
    manifest = generate(durations)
    print(f"Generated fixed synthetic samples: {durations}", flush=True)
    if not args.generate_only:
        subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "scripts/evaluate.py"), str(manifest),
                        "--mode", "cloud", "--cloud-strategy", "compare", "--output", str(OUT / "comparison.json")],
                       cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
