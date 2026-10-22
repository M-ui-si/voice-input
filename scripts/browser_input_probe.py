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
import os
import tkinter as tk
import threading
from types import SimpleNamespace
from unittest.mock import patch
from websockets.sync.client import connect
from speak import windows


def probe_rich_composer(evaluate, hwnd, directory):
    """Exercise real floating controls with fake ASR, on a local Grok-shaped editor."""
    os.environ["SPEAK_DATA_DIR"] = str(directory / "speak-profile")
    from speak.controller import Controller, Task
    from speak.providers import Result, FreePriceGuard
    from speak.storage import Settings
    from speak.ui import App
    Settings(mode="local", x=1040, y=200).save()

    class ProbeController(Controller):
        def start(self, target, target_window=None):
            self.task = Task(str(time.monotonic_ns()), self.history.audio_dir / "synthetic.wav",
                             self.settings, target, target_window=target_window)
            self.task.recorder = SimpleNamespace(error=None, stream=SimpleNamespace(active=True),
                                                  started=time.monotonic(), level=0, stop=lambda: None,
                                                  segments=[])
            self.state = "recording"
        def stop_recording(self):
            self.state = "processing"
            self.task.stopped.set()

    evaluate('''document.body.innerHTML = `<h2>Direct input test - synthetic recognition only</h2>
      <div id="rich" contenteditable="true" role="combobox" aria-multiline="true"
        aria-label="Local rich composer" style="border:1px solid;padding:12px;min-height:100px"><p>已有内容</p></div>
      <textarea id="other" aria-label="Other input"></textarea>`;
      window.enters=0;document.addEventListener('keydown',e=>{if(e.key==='Enter')window.enters++});''')
    root = tk.Tk()
    with patch("speak.ui.Controller", ProbeController), patch.object(FreePriceGuard, "check", lambda *a, **k: None):
        app = App(root)
        root.protocol("WM_DELETE_WINDOW", app.close)
        app.show_result = lambda: (_ for _ in ()).throw(AssertionError("Result dialog must not open"))
        def pump(seconds=.5):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                root.update()
                time.sleep(.03)
        def focus_rich():
            evaluate('''document.querySelector('#rich').focus();
                {const r=document.createRange();r.selectNodeContents(document.querySelector('#rich'));r.collapse(true);
                const s=window.getSelection();s.removeAllRanges();s.addRange(r);}''')
            pump(.25)
        def click_floater():
            assert win32gui.GetForegroundWindow() == hwnd, "must keep our test browser foreground"
            win32api.SetCursorPos((root.winfo_x()+35, root.winfo_y()+35))
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0)
            pump(.35)
        def result(text):
            app.events.put((app.controller.task.id,"result",Result(text,"probe","synthetic",0)))
            pump(.8)
        try:
            pump(.25)
            win32gui.SetWindowPos(hwnd,win32con.HWND_TOPMOST,260,180,750,500,win32con.SWP_SHOWWINDOW)
            current=win32api.GetCurrentThreadId()
            foreground=win32process.GetWindowThreadProcessId(win32gui.GetForegroundWindow())[0]
            attached=current!=foreground
            try:
                if attached:win32process.AttachThreadInput(current,foreground,True)
                win32gui.SetForegroundWindow(hwnd)
            finally:
                if attached:win32process.AttachThreadInput(current,foreground,False)
            focus_rich()
            for _ in range(30):
                if windows.capture_target():break
                pump(.1)
            assert windows.capture_target(), "rich editor must be detected"
            click_floater()
            assert app.controller.state=="recording"
            assert win32gui.GetForegroundWindow()==hwnd, "preview stole focus"
            # Change text and briefly move focus while recording. Returning should still work.
            evaluate('document.querySelector("#rich").textContent="已有内容和手工编辑";document.querySelector("#other").focus()')
            pump(.6)
            focus_rich()
            click_floater()
            assert app.controller.state=="processing"
            result("语音 API。")
            assert evaluate('document.querySelector("#rich").textContent')=="语音 API。已有内容和手工编辑"
            assert app.delivery.pending is None and "已填入" in app.status
            # Result arrives when another field is focused. Never insert there; return to recover.
            focus_rich(); click_floater(); click_floater()
            evaluate('document.querySelector("#other").focus()')
            result("第二句。")
            assert evaluate('document.querySelector("#other").value')==""
            assert app.delivery.pending is not None
            focus_rich();pump(.7)
            assert evaluate('document.querySelector("#rich").textContent')=="第二句。语音 API。已有内容和手工编辑"
            assert app.delivery.pending is None
            assert evaluate('window.enters')==0
            return {"rich_combobox_direct_insert":True,"floating_start_stop":True,
                    "preview_does_not_activate":True,"manual_edits_preserved":True,
                    "focus_return_auto_insert":True,"other_input_not_written":True,
                    "no_result_dialog":True,"no_enter":True,"recognition":"synthetic; no microphone or cloud call"}
        finally:
            app.close()


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
        for _ in range(30):
            if windows.target_matches(captured): break
            time.sleep(.1)
        current=windows.capture_target()
        assert windows.target_matches(captured), f"editor identity changed: old={captured} now={current}"
        assert evaluate('window.enters')==0
        report={"edge_textarea_paste":True,"no_enter":True,"current_caret_insert":True,"same_editor_edits_allowed":True,"profile":"isolated local test page"}
        report.update(probe_rich_composer(evaluate,hwnd,directory))
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
