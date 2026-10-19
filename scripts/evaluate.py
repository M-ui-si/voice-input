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
from speak.providers import CloudProvider
from speak.storage import SecretStore
from speak.local import LocalProvider
from speak.recording import split_wav
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


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("manifest")
    parser.add_argument("--mode",choices=["local","cloud"],required=True)
    parser.add_argument("--output",default="data/evaluation.json")
    args=parser.parse_args()
    samples=json.loads(Path(args.manifest).read_text("utf-8"))
    local=LocalProvider(); cloud=CloudProvider(SecretStore()); cancel=threading.Event()
    rows=[]
    try:
        for item in samples:
            if item.get("split")!="acceptance": continue
            started=time.monotonic()
            if args.mode=="cloud": result=cloud.transcribe(item["audio"],item.get("terms",[]),cancel)
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
            rows.append({"audio":str(item["audio"]),"output":result.text,"provider":result.provider,"model":result.model,"total_seconds":time.monotonic()-started,"mixed_token_error_rate":distance(ref,out)/max(1,len(ref)),"chinese_character_error_rate":distance(zh_ref,zh_out)/max(1,len(zh_ref)),"term_accuracy":sum(t in result.text for t in terms)/max(1,len(terms)),"critical_present":all(c in result.text for c in item.get("critical",[])),"semantic_review":"requires human review"})
    finally: local.stop()
    Path(args.output).parent.mkdir(parents=True,exist_ok=True)
    Path(args.output).write_text(json.dumps({"samples":rows,"note":"关键词存在不等于语义正确，需人工复核；总耗时含冷启动。"},ensure_ascii=False,indent=2),"utf-8")
    print(f"已评估 {len(rows)} 条，结果：{args.output}")


if __name__=="__main__": main()
