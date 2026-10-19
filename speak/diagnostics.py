import json
import os
import platform
import sys
import threading
import time
import wave
from pathlib import Path


def diagnose(output):
    output = Path(output).resolve()
    import psutil
    import sounddevice as sd
    from .storage import ROOT, DATA
    from .local import LocalProvider, source_root
    from .windows import automation
    report={"platform":platform.platform(),"python":sys.version,"frozen":bool(getattr(sys,"frozen",False)),"home":str(ROOT),"data":str(DATA)}
    try:
        import tkinter as tk
        root=tk.Tk();root.withdraw();root.update()
        automation()
        report["tkinter_and_uia"]=True
        report["microphones"]=sum(d["max_input_channels"]>0 for d in sd.query_devices())
        report["cloud_idle_private_mb"]=round(psutil.Process().memory_info().private/1024**2,1)
        report["cloud_idle_working_set_mb"]=round(psutil.Process().memory_info().rss/1024**2,1)
        report["local_process_started_in_cloud"]=False
        root.destroy()
    except Exception as e: report["ui_error"]=type(e).__name__
    try:
        previous_cwd = Path.cwd()
        sys.path.insert(0,str(source_root()))
        import config_server
        config_server.ServerConfig.log_level="CRITICAL"
        from core.server.engines.qwen_asr_gguf.asr_engine import QwenASREngine
        report["local_engine_import"]=True
    except Exception as e:
        report["local_engine_import"]=False
        report["local_import_error"]=type(e).__name__+": "+str(e)
    finally:
        os.chdir(previous_cwd)
    report["local_model_files_ready"]=LocalProvider().available()
    Path(output).parent.mkdir(parents=True,exist_ok=True)
    Path(output).write_text(json.dumps(report,ensure_ascii=False,indent=2),"utf-8")
