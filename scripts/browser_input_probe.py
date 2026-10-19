"""Checks a real Edge textarea in an isolated test profile, without opening user accounts."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import ctypes
import json
import socket
import subprocess
import time
import requests
import win32api
import win32gui
import win32con
import win32process
import psutil
from websockets.sync.client import connect
from speak import windows


def main():
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
    directory=Path("data/browser-test").resolve(); directory.mkdir(parents=True,exist_ok=True)
    page=directory/"input.html"
    page.write_text('<!doctype html><meta charset="utf-8"><title>Speak browser input test</title><style>body{font:20px sans-serif;padding:30px}textarea{width:90%;height:180px;font:20px sans-serif}</style><h2>语音输入测试（不会发送）</h2><textarea id="input"></textarea><script>window.enters=0;document.querySelector("textarea").addEventListener("keydown",e=>{if(e.key==="Enter")window.enters++})</script>',"utf-8")
    with socket.socket() as s: s.bind(("127.0.0.1",0)); port=s.getsockname()[1]
    edge=Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    child=subprocess.Popen([str(edge),f"--user-data-dir={directory/'profile'}",f"--remote-debugging-port={port}","--no-first-run","--no-default-browser-check","--force-renderer-accessibility",f"--app={page.as_uri()}","--window-size=750,500","--window-position=260,180"])
    snapshot,_=windows.snapshot_clipboard(); cursor=win32api.GetCursorPos()
    session=requests.Session();session.trust_env=False
    ws=None
    try:
        target=None
        for _ in range(100):
            try:
                tabs=session.get(f"http://127.0.0.1:{port}/json",timeout=1).json()
                target=next((t for t in tabs if t.get("url")==page.as_uri()),None)
                if target: break
            except requests.RequestException: pass
            time.sleep(.1)
        if not target: raise RuntimeError("Edge debug target did not start")
        ws=connect(target["webSocketDebuggerUrl"],proxy=None)
        counter=0
        def evaluate(expression):
            nonlocal counter
            counter+=1
            ws.send(json.dumps({"id":counter,"method":"Runtime.evaluate","params":{"expression":expression,"returnByValue":True}}))
            while True:
                response=json.loads(ws.recv(timeout=5))
                if response.get("id")==counter:
                    if "error" in response: raise RuntimeError(response["error"])
                    return response["result"]["result"].get("value")
        evaluate('document.querySelector("textarea").focus()')
        time.sleep(.5)
        handles=[]
        win32gui.EnumWindows(lambda hwnd,arg: handles.append(hwnd) if "Speak browser input test" in win32gui.GetWindowText(hwnd) else None,None)
        if not handles: raise RuntimeError("test browser window not found")
        hwnd=handles[0]
        win32gui.SetWindowPos(hwnd,win32con.HWND_TOPMOST,260,180,750,500,win32con.SWP_SHOWWINDOW)
        current=win32api.GetCurrentThreadId()
        foreground=win32process.GetWindowThreadProcessId(win32gui.GetForegroundWindow())[0]
        attached=False
        try:
            if current!=foreground:
                win32process.AttachThreadInput(current,foreground,True);attached=True
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
        finally:
            if attached: win32process.AttachThreadInput(current,foreground,False)
        time.sleep(.7)
        if win32gui.GetForegroundWindow()!=hwnd:
            raise RuntimeError("test browser is not foreground; no input attempted")
        evaluate('document.querySelector("textarea").focus()')
        time.sleep(.4)
        captured=None
        for _ in range(30):
            if windows.automation().GetFocusedElement().CurrentControlType == 50004:
                captured=windows.capture_target()
                if captured:
                    break
            time.sleep(.1)
        if not captured:
            e=windows.automation().GetFocusedElement()
            raise RuntimeError(f"could not capture editable test target; type={e.CurrentControlType}; DOM={evaluate('document.activeElement.tagName')}; pattern={windows.selection_fingerprint(e)}")
        pending=[]
        text="请检查 ELF 的 ROP 链，不要修改 API。"
        assert windows.paste(captured,text,lambda ms,fn:pending.append(fn))
        time.sleep(.6)
        assert evaluate('document.querySelector("textarea").value')==text
        assert evaluate('window.enters')==0
        for fn in pending: fn()
        # Moving the caret within the same unchanged editor is intentional.
        captured=windows.capture_target()
        evaluate('document.querySelector("textarea").setSelectionRange(0,0)')
        assert windows.target_matches(captured)
        pending=[]
        assert windows.paste(captured,"前置：",lambda ms,fn:pending.append(fn))
        time.sleep(.6)
        assert evaluate('document.querySelector("textarea").value')=="前置："+text
        for fn in pending: fn()
        captured=windows.capture_target()
        evaluate('document.querySelector("textarea").value += "用户新输入"; document.querySelector("textarea").dispatchEvent(new Event("input",{bubbles:true}))')
        # Chromium publishes accessibility changes asynchronously.
        for _ in range(30):
            changed=windows.capture_target()
            if changed and changed.fingerprint[0] != captured.fingerprint[0]:
                break
            time.sleep(.1)
        assert changed and changed.fingerprint[0] != captured.fingerprint[0]
        assert not windows.target_matches(captured)
        assert evaluate('window.enters')==0
        report={"edge_textarea_paste":True,"no_enter":True,"current_caret_insert":True,"document_change_rejected":True,"profile":"isolated local test page"}
        Path("data/browser-input-test.json").write_text(json.dumps(report,indent=2),"utf-8")
        print(json.dumps(report))
    finally:
        try:
            if ws:
                ws.send(json.dumps({"id":99999,"method":"Browser.close"}));ws.close()
        except Exception: pass
        try:
            proc=psutil.Process(child.pid)
            descendants=proc.children(recursive=True)
            for p in reversed(descendants):
                try: p.terminate()
                except psutil.Error: pass
            proc.terminate()
        except psutil.Error: pass
        win32api.SetCursorPos(cursor)
        windows.restore_clipboard(snapshot)


if __name__=="__main__": main()
