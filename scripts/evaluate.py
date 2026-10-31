"""Evaluate real user recordings; no claims based on synthetic audio.

Manifest: [{"audio":"absolute.wav","reference":"原文","terms":["API"],"critical":["不要"],"split":"acceptance"}]
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
import json
import re
import threading
import time
import wave
import queue
import statistics
from speak.providers import CloudProvider, RecognitionError
from speak.storage import SecretStore
from speak.local import LocalProvider
from speak.recording import RATE, SegmentWriter, split_wav
from speak.merging import merge_cloud_text
import tempfile


def tokens(text):
    return re.findall(r"[\u3400-\u9fff]|[a-zA-Z]+|\d+", text.lower())


def distance(a,b):
    row=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        nxt=[i]
        for j,y in enumerate(b,1): nxt.append(min(nxt[-1]+1,row[j]+1,row[j-1]+(x!=y)))
        row=nxt
    return row[-1]


def cloud_sample(cloud, audio, terms, cancel, strategy):
    """Compare the same WAV; segmented windows are released at actual capture speed."""
    with wave.open(str(audio), "rb") as wav:
        duration = wav.getnframes() / wav.getframerate()
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (RATE, 1, 2):
            raise ValueError("需要 16kHz 单声道 PCM16 WAV")
    before = cloud.request_count
    started = time.monotonic()
    if strategy == "whole":
        result = cloud.transcribe(audio, terms, cancel)
        wait = time.monotonic() - started
        return result, {"strategy": strategy, "recording_seconds": duration,
                        "first_preview_seconds": None, "stop_wait_seconds": wait,
                        "session_seconds": duration + wait, "request_count": cloud.request_count - before,
                        "review_boundaries": []}
    pending = queue.Queue()
    stopped = [None]
    producer_errors = []
    with tempfile.TemporaryDirectory(dir=Path(audio).resolve().parent) as tmp:
        def produce():
            try:
                writer = SegmentWriter(Path(tmp)/"evaluation.wav", pending.put)
                with wave.open(str(audio), "rb") as source:
                    captured = 0
                    while block := source.readframes(1600):
                        captured += len(block)//2
                        if cancel.wait(max(0, started + captured/RATE - time.monotonic())):
                            return
                        writer.feed(block)
                    writer.flush()
            except Exception as exc:
                producer_errors.append(exc)
            finally:
                stopped[0] = time.monotonic()
                pending.put(None)
        producer = threading.Thread(target=produce)
        producer.start()
        text, elapsed, first, review = "", 0.0, None, []
        try:
            while (segment := pending.get()) is not None:
                result = cloud.transcribe(segment.path, terms, cancel)
                text, ambiguous = merge_cloud_text(text, result.text, segment.overlap_frames)
                if ambiguous:
                    review.append(segment.index+1)
                elapsed += result.elapsed
                if first is None and text:
                    first = time.monotonic()-started
                segment.path.unlink(missing_ok=True)
            if producer_errors:
                raise producer_errors[0]
        except BaseException:
            cancel.set()
            raise
        finally:
            producer.join()
    from speak.providers import Result, SENSEVOICE
    done = time.monotonic()
    return Result(text, "cloud", SENSEVOICE, elapsed), {"strategy": strategy,
        "recording_seconds": duration, "first_preview_seconds": first,
        "stop_wait_seconds": max(0, done-stopped[0]), "session_seconds": done-started,
        "request_count": cloud.request_count-before, "review_boundaries": review}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("manifest")
    parser.add_argument("--mode",choices=["local","cloud"],required=True)
    parser.add_argument("--output",default="data/evaluation.json")
    parser.add_argument("--cloud-strategy", choices=["whole", "segmented", "compare"], default="whole",
                        help="segmented/compare replay audio at real recording speed")
    args=parser.parse_args()
    samples=json.loads(Path(args.manifest).read_text("utf-8"))
    local=LocalProvider(); cloud=CloudProvider(SecretStore()); cancel=threading.Event()
    rows=[]
    failure = None
    try:
        for item in samples:
            if item.get("split")!="acceptance": continue
            strategies = (["whole", "segmented"] if args.cloud_strategy == "compare" else [args.cloud_strategy]) if args.mode == "cloud" else ["local"]
            for strategy in strategies:
                started=time.monotonic()
                metrics = {}
                print(f"Evaluating {Path(item['audio']).name}: {strategy}", flush=True)
                if args.mode=="cloud":
                    try:
                        result, metrics = cloud_sample(cloud, item["audio"], item.get("terms",[]), cancel, strategy)
                    except RecognitionError as exc:
                        failure = {"audio": str(item["audio"]), "strategy": strategy,
                                   "error_code": exc.code, "message": str(exc)}
                        break
                else:
                    text=""
                    with tempfile.TemporaryDirectory() as tmp:
                        for segment in split_wav(item["audio"],tmp):
                            result=local.segment(segment,item.get("terms",[]),text,cancel,lambda _:None)
                            text=result.text
                ref,out=tokens(item["reference"]),tokens(result.text)
                zh_ref=re.findall(r"[\u3400-\u9fff]",item["reference"])
                zh_out=re.findall(r"[\u3400-\u9fff]",result.text)
                terms=item.get("terms",[])
                rows.append({"audio":str(item["audio"]),"output":result.text,"provider":result.provider,"model":result.model,"total_seconds":time.monotonic()-started,"mixed_token_error_rate":distance(ref,out)/max(1,len(ref)),"chinese_character_error_rate":distance(zh_ref,zh_out)/max(1,len(zh_ref)),"term_accuracy":sum(t in result.text for t in terms)/max(1,len(terms)),"critical_present":all(c in result.text for c in item.get("critical",[])),"semantic_review":"requires human review", **metrics})
            if failure:
                break
    finally: local.stop()
    Path(args.output).parent.mkdir(parents=True,exist_ok=True)
    medians = {strategy: statistics.median([row["stop_wait_seconds"] for row in rows if row.get("strategy") == strategy])
               for strategy in ("whole", "segmented") if any(row.get("strategy") == strategy for row in rows)}
    Path(args.output).write_text(json.dumps({"status": "blocked" if failure else "completed", "failure": failure,
        "samples":rows,"median_stop_wait_seconds":medians,"note":"关键词存在不等于语义正确，需人工复核；segmented 按实际采集速度提交，whole 的 session_seconds 包含音频时长。"},ensure_ascii=False,indent=2),"utf-8")
    print(f"已评估 {len(rows)} 条，结果：{args.output}")
    if failure:
        print(f"评估停止：{failure['error_code']} · {failure['message']}")
        raise SystemExit(1)


if __name__=="__main__": main()
