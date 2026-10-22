"""Fill a synthetic draft in a fresh public Grok profile; never send a message."""
import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "data/grok-input-test"
OUT.mkdir(parents=True, exist_ok=True)
os.environ["SPEAK_DATA_DIR"] = str(OUT / "speak-profile")

import psutil
import requests
import win32api
import win32con
import win32gui
import win32process
from websockets.sync.client import connect
from speak import windows
from speak.controller import Controller, Task
from speak.providers import Result, FreePriceGuard
from speak.storage import Settings
from speak.ui import App


class ProbeController(Controller):
    def start(self, target, target_window=None):
        self.task = Task("grok-probe", self.history.audio_dir / "synthetic.wav", self.settings,
                         target, target_window=target_window)
        self.task.recorder = SimpleNamespace(error=None, stream=SimpleNamespace(active=True),
                                             started=time.monotonic(), level=0, stop=lambda: None, segments=[])
        self.state = "recording"
    def stop_recording(self):
        self.state = "processing"
        self.task.stopped.set()


def main():
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    profile = OUT / ("browser-" + str(time.time_ns()))
    child = subprocess.Popen([edge, f"--user-data-dir={profile}", f"--remote-debugging-port={port}",
                              "--no-first-run", "--no-default-browser-check", "--app=https://grok.com/",
                              "--window-size=900,700", "--window-position=120,120"])
    session = requests.Session(); session.trust_env = False
    ws = None; app = None; root = None
    owned = set()
    cursor = win32api.GetCursorPos()
    report = {"site": "https://grok.com/", "recognition": "synthetic; no microphone or ASR upload"}
    try:
        tab = None
        for _ in range(150):
            try:
                tabs = session.get(f"http://127.0.0.1:{port}/json", timeout=1).json()
                tab = next((t for t in tabs if t.get("type") == "page" and "grok.com" in t.get("url", "")), None)
                if tab: break
            except requests.RequestException: pass
            time.sleep(.1)
        if tab is None: raise RuntimeError("Public Grok page did not load")
        ws = connect(tab["webSocketDebuggerUrl"], proxy=None, legacy=True)
        counter = 0
        def command(method, params=None):
            nonlocal counter
            counter += 1
            ws.send(json.dumps({"id": counter, "method": method, "params": params or {}}))
            while True:
                result = json.loads(ws.recv(timeout=10))
                if result.get("id") == counter:
                    if "error" in result: raise RuntimeError("Browser command failed")
                    return result["result"]
        def evaluate(script):
            result = command("Runtime.evaluate", {"expression": script, "returnByValue": True})
            if "exceptionDetails" in result: raise RuntimeError("Probe page script failed")
            return result["result"].get("value")
        found = False
        for _ in range(90):
            found = evaluate('''window.__speakProbe = Array.from(document.querySelectorAll('[contenteditable="true"][role="combobox"]'))
                .find(e=>e.getBoundingClientRect().width>0 && e.getBoundingClientRect().height>0);
                Boolean(window.__speakProbe)''')
            if found: break
            time.sleep(.3)
        if not found: raise RuntimeError("Public Grok composer unavailable (possibly access challenge)")
        assert evaluate('window.__speakProbe.innerText.trim()') == "", "fresh profile must have no existing draft"
        # Dismiss only the cookie banner's own close icon in this disposable
        # profile. Do not click accept-all, send, or any account controls.
        evaluate('''for(const b of document.querySelectorAll('button')){
            const r=b.getBoundingClientRect(),label=(b.getAttribute('aria-label')||'').toLowerCase(),t=b.innerText.trim();
            if(!label.includes('close') && !(r.width>0&&r.width<50&&r.height<50&&['','×','✕','X'].includes(t)))continue;
            let p=b.parentElement;
            for(let i=0;p&&i<6;i++,p=p.parentElement){
                if(p===document.body||p.contains(window.__speakProbe))break;
                if(p.innerText.includes('We use essential cookies')){b.click();break;}
            }
        }
        window.__speakProbe.scrollIntoView({block:'center'});''')
        evaluate('''window.__speakProbeEnter=0;window.__speakProbeSubmit=0;
            window.addEventListener('keydown',e=>{if(e.key==='Enter'){window.__speakProbeEnter++;e.preventDefault();e.stopImmediatePropagation()}},true);
            window.addEventListener('submit',e=>{window.__speakProbeSubmit++;e.preventDefault();e.stopImmediatePropagation()},true);''')
        # Edge's launcher can exit after starting its browser process.
        for process in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if (process.info["name"] or "").lower() == "msedge.exe" and any(
                    str(profile).casefold() in arg.casefold() for arg in process.info["cmdline"] or []):
                    owned.add(process.pid)
                    owned.update(p.pid for p in process.children(recursive=True))
            except psutil.Error:
                pass
        if not owned: raise RuntimeError("Cannot identify the isolated browser process")
        handles = []
        win32gui.EnumWindows(lambda h, a: handles.append(h) if win32gui.IsWindowVisible(h)
            and win32process.GetWindowThreadProcessId(h)[1] in owned and win32gui.GetClassName(h) == "Chrome_WidgetWin_1" else None, None)
        if not handles: raise RuntimeError("Cannot identify the isolated browser window")
        handles=[h for h in handles if win32gui.GetWindowRect(h)[2]-win32gui.GetWindowRect(h)[0]>400
                 and win32gui.GetWindowRect(h)[3]-win32gui.GetWindowRect(h)[1]>300
                 and "grok" in win32gui.GetWindowText(h).lower()]
        if not handles: raise RuntimeError("Isolated browser has no visible main window")
        hwnd = handles[0]
        Settings(mode="cloud", x=1040, y=200).save()
        with patch("speak.ui.Controller", ProbeController), patch.object(FreePriceGuard, "check", lambda *a, **k: None):
            root = tk.Tk(); app = App(root)
        app.show_result = lambda: (_ for _ in ()).throw(AssertionError("Unexpected result dialog"))
        def pump(seconds=.4):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                root.update(); time.sleep(.03)
        pump()
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST,120,120,900,700,win32con.SWP_SHOWWINDOW)
        current = win32api.GetCurrentThreadId()
        foreground = win32process.GetWindowThreadProcessId(win32gui.GetForegroundWindow())[0]
        try:
            if current != foreground: win32process.AttachThreadInput(current,foreground,True)
            if win32gui.GetForegroundWindow() != hwnd:
                try:
                    win32gui.BringWindowToTop(hwnd)
                    win32gui.SetForegroundWindow(hwnd)
                except Exception:
                    if win32gui.GetForegroundWindow() != hwnd:
                        left,top,_,_=win32gui.GetWindowRect(hwnd)
                        win32api.SetCursorPos((left+150,top+12))
                        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN,0,0)
                        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP,0,0)
        finally:
            if current != foreground: win32process.AttachThreadInput(current,foreground,False)
        # Closing an overlay may cause React to recreate the composer. Resolve
        # the connected element again, then use a trusted click inside it.
        point = evaluate('''window.__speakProbe = Array.from(document.querySelectorAll('[contenteditable="true"][role="combobox"]'))
            .find(e=>e.isConnected&&e.getBoundingClientRect().width>0&&e.getBoundingClientRect().height>0);
            window.__speakProbe.scrollIntoView({block:'center'});window.__speakProbe.focus();
            {const r=window.__speakProbe.getBoundingClientRect(),x=r.x+Math.min(30,r.width/2),y=r.y+r.height/2;
             const hit=document.elementFromPoint(x,y);({x,y,visible:hit===window.__speakProbe||window.__speakProbe.contains(hit)})}''')
        if not point or not point["visible"]: raise RuntimeError("Grok composer is covered by an overlay")
        command("Input.dispatchMouseEvent", {"type":"mousePressed","x":point["x"],"y":point["y"],"button":"left","clickCount":1})
        command("Input.dispatchMouseEvent", {"type":"mouseReleased","x":point["x"],"y":point["y"],"button":"left","clickCount":1})
        pump(.5)
        captured = None
        for _ in range(30):
            captured = windows.capture_target()
            if captured and captured.hwnd == hwnd: break
            pump(.1)
        if not captured or captured.hwnd != hwnd:
            element=windows.automation().GetFocusedElement()
            fg=win32gui.GetForegroundWindow()
            raise AssertionError(f"Grok composer not captured; foreground={fg==hwnd}, foreground_owned={win32process.GetWindowThreadProcessId(fg)[1] in owned}, type={element.CurrentControlType}, role={element.GetCurrentPropertyValue(30101)}")
        def click():
            assert win32gui.GetForegroundWindow() == hwnd, "test browser must remain foreground"
            win32api.SetCursorPos((root.winfo_x()+35, root.winfo_y()+35))
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN,0,0)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP,0,0)
            pump(.3)
        click(); assert app.controller.state == "recording"
        click(); assert app.controller.state == "processing"
        text = "语音输入定位测试 API 123。"
        app.events.put((app.controller.task.id,"result",Result(text,"probe","synthetic",0)))
        pump(1.5)
        actual = evaluate('window.__speakProbe.innerText.trim()')
        assert actual == text, "Grok draft did not receive the result"
        assert app.delivery.pending is None
        assert evaluate('window.__speakProbeEnter') == 0 and evaluate('window.__speakProbeSubmit') == 0
        report.update(real_grok_draft_insert=True, floating_start_stop=True, no_result_dialog=True,
                      no_enter=True, no_submit=True, isolated_profile=True, status="passed")
        shot = command("Page.captureScreenshot", {"format":"png"})["data"]
        (OUT / "grok-draft.png").write_bytes(base64.b64decode(shot))
        (OUT / "last-success.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps(report,ensure_ascii=False))
    finally:
        if app is not None: app.close()
        try:
            if ws is not None:
                ws.send(json.dumps({"id":99999,"method":"Browser.close"})); ws.close()
        except Exception: pass
        for pid in owned:
            try: psutil.Process(pid).terminate()
            except psutil.Error: pass
        win32api.SetCursorPos(cursor)
        report.setdefault("status", "incomplete_or_interrupted; no message was sent")
        (OUT / "result.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")


if __name__ == "__main__": main()
