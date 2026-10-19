"""Controlled Windows input test. Uses its own native EDIT window, never an Agent chat."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
import subprocess
import time
import ctypes
import win32api
import win32con
import win32gui
import win32clipboard
import pythoncom
import os

TITLE = "Speak automated native input test " + os.environ.get("SPEAK_PROBE_ID", str(os.getpid()))


def helper():
    wc = win32gui.WNDCLASS()
    wc.lpszClassName = "SpeakNativeTest"
    wc.hInstance = win32api.GetModuleHandle(None)
    def handler(hwnd, msg, wparam, lparam):
        if msg == win32con.WM_DESTROY:
            win32gui.PostQuitMessage(0); return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)
    wc.lpfnWndProc = handler
    win32gui.RegisterClass(wc)
    hwnd = win32gui.CreateWindow(wc.lpszClassName, TITLE, win32con.WS_OVERLAPPEDWINDOW, 280, 200, 560, 240, 0, 0, wc.hInstance, None)
    edit = win32gui.CreateWindow("EDIT", "", win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.ES_MULTILINE | win32con.WS_BORDER, 10, 10, 520, 140, hwnd, 0, wc.hInstance, None)
    win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
    win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 280, 200, 560, 240, win32con.SWP_SHOWWINDOW)
    win32api.SetCursorPos((340, 270))
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0)
    win32gui.SetFocus(edit)
    win32gui.PumpMessages()


def probe():
    from speak import windows
    original, _ = windows.snapshot_clipboard()
    cursor = win32api.GetCursorPos()
    modified = False
    child = subprocess.Popen([sys.executable, __file__, "--helper"], env=dict(os.environ, SPEAK_PROBE_ID=str(os.getpid())))
    try:
        hwnd=0
        for _ in range(50):
            hwnd=win32gui.FindWindow(None,TITLE)
            if hwnd: break
            time.sleep(0.1)
        if not hwnd: raise RuntimeError("helper did not start")
        time.sleep(0.8)
        target=None
        for _ in range(30):
            if win32gui.GetForegroundWindow()==hwnd:
                candidate=windows.capture_target()
                if candidate and candidate.hwnd==hwnd:
                    target=candidate
                    break
            time.sleep(.1)
        if not target: raise RuntimeError("native edit focus could not be captured")
        edit=win32gui.FindWindowEx(hwnd,0,"EDIT",None)
        # Preserve a custom rich format along with Unicode text.
        fmt=win32clipboard.RegisterClipboardFormat("SpeakProbeFormat")
        windows.set_clipboard("original clipboard")
        modified = True
        win32clipboard.OpenClipboard()
        win32clipboard.SetClipboardData(fmt,b"test custom format\x00")
        win32clipboard.CloseClipboard()
        pending=[]
        text="不要发送。测试 API、ROP 和 123。"
        pasted=windows.paste(target,text,lambda delay,fn:pending.append(fn))
        time.sleep(0.6)
        assert pasted, "paste rejected"
        length = win32gui.SendMessage(edit, win32con.WM_GETTEXTLENGTH)
        buffer = win32gui.PyMakeBuffer((length+1)*2)
        win32gui.SendMessage(edit, win32con.WM_GETTEXT, length+1, buffer)
        actual = bytes(buffer).decode("utf-16le").rstrip("\x00")
        assert actual==text, f"native input mismatch: {actual!r}"
        for fn in pending: fn()
        win32clipboard.OpenClipboard()
        try:
            restored=win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            custom=win32clipboard.GetClipboardData(fmt)
        finally:
            win32clipboard.CloseClipboard()
        assert restored=="original clipboard" and bytes(custom).startswith(b"test custom format")
        # New user clipboard contents must survive a pending restoration.
        target=windows.capture_target(); pending=[]
        assert windows.paste(target,"追加",lambda delay,fn:pending.append(fn))
        time.sleep(0.3)
        windows.set_clipboard("user copied something new")
        for fn in pending: fn()
        win32clipboard.OpenClipboard()
        assert win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)=="user copied something new"
        win32clipboard.CloseClipboard()
        result={"native_edit_insert":True,"rich_clipboard_restore":True,"new_clipboard_preserved":True,"no_enter_sent":True}
        Path("data/native-input-test.json").write_text(json.dumps(result,indent=2),"utf-8")
        print(json.dumps(result))
    finally:
        if child.poll() is None:
            if hwnd and win32gui.IsWindow(hwnd):
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            try:
                child.wait(5)
            except subprocess.TimeoutExpired:
                child.terminate(); child.wait(5)
        win32api.SetCursorPos(cursor)
        if modified:
            for _ in range(5):
                try:
                    windows.restore_clipboard(original)
                    break
                except Exception:
                    time.sleep(0.2)


if __name__=="__main__":
    helper() if "--helper" in sys.argv else probe()
