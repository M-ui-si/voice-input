"""Windows integration. Uncertain focus/selection means no automatic paste."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import os
import sys
import time
from dataclasses import dataclass

import win32api
import win32clipboard
import win32con
import win32gui
import win32process

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
_uia = None
_pending_clipboard = None


def automation():
    global _uia
    if _uia is None:
        import comtypes.client
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen.UIAutomationClient import CUIAutomation, IUIAutomation
        _uia = comtypes.client.CreateObject(CUIAutomation, interface=IUIAutomation)
    return _uia


def selection_fingerprint(element):
    from comtypes.gen.UIAutomationClient import IUIAutomationTextPattern, IUIAutomationValuePattern
    # Writable ValuePattern is more reliable for empty Chromium/native inputs.
    # Current-caret insertion requires a content fingerprint, not fixed offsets.
    try:
        value = element.GetCurrentPattern(10002).QueryInterface(IUIAutomationValuePattern)
        if value.CurrentIsReadOnly:
            return None
        content = value.CurrentValue or ""
        return (hashlib.sha256(content.encode()).hexdigest(), None, None)
    except Exception:
        pass
    # Contenteditable documents may expose only TextPattern.
    try:
        pattern = element.GetCurrentPattern(10014).QueryInterface(IUIAutomationTextPattern)
        ranges = pattern.GetSelection()
        if ranges.Length != 1:
            return None
        selected = ranges.GetElement(0)
        doc = pattern.DocumentRange
        prefix = doc.Clone()
        prefix.MoveEndpointByRange(1, selected, 0)
        # Some UIA providers return a null BSTR for an empty range.
        before = prefix.GetText(-1) or ""
        content = doc.GetText(-1)
        if content is None:
            return None  # An unreadable document is not proof of an empty editor.
        selected_text = selected.GetText(-1) or ""
        return (hashlib.sha256(content.encode()).hexdigest(), len(before), len(selected_text))
    except Exception:
        pass
    return None


@dataclass(frozen=True)
class Target:
    hwnd: int
    pid: int
    runtime_id: tuple
    fingerprint: tuple


def foreground_identity():
    """Remember a destination window without activating or reading its text."""
    try:
        hwnd = win32gui.GetForegroundWindow()
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return (hwnd, pid) if hwnd and pid != os.getpid() else None
    except Exception:
        return None


def editable_element(element):
    """Accept writable browser composers, including rich-text descendants."""
    from comtypes.gen.UIAutomationClient import IUIAutomationValuePattern, IUIAutomationTextPattern
    for _ in range(6):
        if element is None or element.CurrentIsPassword or not element.CurrentIsEnabled:
            return None
        if element.CurrentControlType not in (50003, 50004, 50020, 50025, 50026, 50030, 50033):
            return None
        try:
            value = element.GetCurrentPattern(10002).QueryInterface(IUIAutomationValuePattern)
            # Do not walk from a read-only control into a different editor.
            return None if value.CurrentIsReadOnly else element
        except Exception:
            pass
        try:
            text = element.GetCurrentPattern(10014).QueryInterface(IUIAutomationTextPattern)
            readonly = text.DocumentRange.GetAttributeValue(40015)
            if type(readonly) is bool:
                return None if readonly else element
        except Exception:
            pass
        try:
            role = str(element.GetCurrentPropertyValue(30101) or "").lower().split()
            props = str(element.GetCurrentPropertyValue(30102) or "").lower()
            if "textbox" in role or "searchbox" in role:
                return None if "readonly=true" in props or "disabled=true" in props else element
        except Exception:
            pass
        try:
            element = automation().ControlViewWalker.GetParentElement(element)
        except Exception:
            return None
    return None


def capture_target():
    try:
        hwnd = win32gui.GetForegroundWindow()
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid == os.getpid():
            return None
        element = automation().GetFocusedElement()
        if element.CurrentIsPassword or not element.CurrentIsEnabled or not element.CurrentHasKeyboardFocus:
            return None
        element = editable_element(element)
        if element is None:
            return None
        runtime_id = element.GetRuntimeId()
        if not runtime_id:
            return None
        # Identity is sufficient for current-caret insertion. Do not read or hash
        # the entire composer: editing it during recording is legitimate.
        return Target(hwnd, pid, tuple(runtime_id), (None, None, None))
    except Exception:
        return None


def target_matches(target):
    current = capture_target()
    if target is None or current is None:
        return False
    # Recheck the same focused writable editor; honor its current text and caret.
    return (current.hwnd, current.pid, current.runtime_id) == (
        target.hwnd, target.pid, target.runtime_id
    )


def set_clipboard(text):
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()


def snapshot_clipboard():
    """Materialize HGLOBAL formats before changing ownership (OLE proxies are lazy)."""
    import struct
    win32clipboard.OpenClipboard()
    try:
        formats, fmt = [], 0
        while True:
            fmt = win32clipboard.EnumClipboardFormats(fmt)
            if not fmt: break
            formats.append(fmt)
        result, size = [], 0
        for fmt in formats:
            if fmt == win32con.CF_BITMAP and (win32con.CF_DIB in formats or 17 in formats):
                continue  # Windows recreates CF_BITMAP from DIB/DIBV5.
            if fmt in (2, 3, 9, 14, 0x80, 0x82, 0x83, 0x8E):
                raise ValueError("Unsupported clipboard handle; use explicit copy instead")
            data = win32clipboard.GetClipboardData(fmt)
            if fmt == win32con.CF_HDROP:
                data = struct.pack("IiiII", 20, 0, 0, 0, 1) + ("\x00".join(data) + "\x00\x00").encode("utf-16le")
            if not isinstance(data, (bytes, str)):
                raise ValueError("Unsupported clipboard format")
            size += len(data) * (2 if isinstance(data, str) else 1)
            if size > 64 * 1024 * 1024:
                raise ValueError("Clipboard too large to snapshot safely")
            result.append((fmt, data))
        return result, win32clipboard.GetClipboardSequenceNumber()
    finally:
        win32clipboard.CloseClipboard()


def restore_clipboard(snapshot, expected_sequence=None):
    win32clipboard.OpenClipboard()
    try:
        if expected_sequence is not None and win32clipboard.GetClipboardSequenceNumber() != expected_sequence:
            return
        win32clipboard.EmptyClipboard()
        for fmt, data in snapshot:
            win32clipboard.SetClipboardData(fmt, data)
    finally:
        win32clipboard.CloseClipboard()


def restore_pending_clipboard():
    """Restore before closing Tk so a scheduled callback is not lost on exit."""
    global _pending_clipboard
    pending = _pending_clipboard
    if pending is not None:
        restore_clipboard(pending[0], pending[1])
        if _pending_clipboard is pending:
            _pending_clipboard = None


def paste(target, text, schedule):
    global _pending_clipboard
    if not target_matches(target):
        return False
    if any(win32api.GetAsyncKeyState(k) & 0x8000 for k in (0x10, 0x11, 0x12, 0x5B, 0x5C)):
        return False
    # Materialized snapshots preserve rich text and files as well as Unicode text.
    # Never restore if the user changed the clipboard while the paste was pending.
    previous = []
    owned_sequence = None
    def restore():
        global _pending_clipboard
        try:
            if owned_sequence is not None and win32clipboard.GetClipboardSequenceNumber() == owned_sequence:
                restore_clipboard(previous, owned_sequence)
        except Exception:
            pass
        finally:
            if _pending_clipboard is not None and _pending_clipboard[1] == owned_sequence:
                _pending_clipboard = None
    try:
        previous, before = snapshot_clipboard()
        if _pending_clipboard is not None and _pending_clipboard[1] == before:
            # A second result arrived before the first delayed restore. Preserve
            # the user's original clipboard rather than our previous transcript.
            previous = _pending_clipboard[0]
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.GetClipboardSequenceNumber() != before:
                return False
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
        owned_sequence = win32clipboard.GetClipboardSequenceNumber()
        _pending_clipboard = (previous, owned_sequence)
        if not target_matches(target):
            restore()
            return False
        # Physical modifier keys held at completion would transform Ctrl+V.
        if any(win32api.GetAsyncKeyState(k) & 0x8000 for k in (0x10, 0x11, 0x12, 0x5B, 0x5C)):
            restore()
            return False
        win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
        win32api.keybd_event(ord("V"), 0, 0, 0)
        win32api.keybd_event(ord("V"), 0, win32con.KEYEVENTF_KEYUP, 0)
        win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
        schedule(1500, restore)
        return True
    except Exception:
        restore()
        return False


def parse_hotkey(text):
    parts = [p.strip().lower() for p in text.split("+")]
    if len(parts) < 2:
        raise ValueError("请使用带 Ctrl / Alt / Shift 的组合键，例如 Ctrl+Alt+Space。")
    modifiers = {"ctrl": 2, "alt": 1, "shift": 4, "win": 8}
    flags = 0x4000  # MOD_NOREPEAT
    for part in parts[:-1]:
        if part not in modifiers:
            raise ValueError("快捷键修饰键无效。")
        flags |= modifiers[part]
    key = parts[-1]
    if key == "space":
        code = 32
    elif len(key) == 1 and key.isascii() and key.isalnum():
        code = ord(key.upper())
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        code = 111 + int(key[1:])
    else:
        raise ValueError("主键支持字母、数字、Space 和 F1～F24。")
    return flags, code


def position_floating(root, x, y):
    # Win32 uses actual virtual-desktop coordinates, including monitors left of the primary.
    monitor = win32api.MonitorFromPoint((int(x), int(y)), win32con.MONITOR_DEFAULTTONEAREST)
    left, top, right, bottom = win32api.GetMonitorInfo(monitor)["Work"]
    x = max(left, min(int(x), right - 286))
    y = max(top, min(int(y), bottom - 78))
    hwnd = win32gui.GetAncestor(root.winfo_id(), win32con.GA_ROOT)
    win32gui.SetWindowPos(hwnd, 0, x, y, 286, 78,
                          win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER)


def preview_geometry(anchor, work, width=420, height=170, gap=8):
    """Place the preview beside the actual Win32 button rect, never covering it."""
    left, top, right, bottom = work
    ax, ay, ar, ab = anchor
    x = max(left, min(ax, right - width))
    y = max(top, min(ay, bottom - height))
    candidates = [(ar + gap, y), (ax - gap - width, y),
                  (x, ab + gap), (x, ay - gap - height)]
    for px, py in candidates:
        if left <= px and top <= py and px + width <= right and py + height <= bottom:
            return px, py, width, height
    # Small work areas: shrink into the largest free region rather than obscure controls.
    regions = [(max(left, ar + gap), top, right, bottom),
               (left, top, min(right, ax - gap), bottom),
               (left, max(top, ab + gap), right, bottom),
               (left, top, right, min(bottom, ay - gap))]
    regions = [r for r in regions if r[2] > r[0] and r[3] > r[1]]
    if not regions:
        return None
    l, t, r, b = max(regions, key=lambda area:
                     min(width, area[2] - area[0]) * min(height, area[3] - area[1]))
    w, h = min(width, r - l), min(height, b - t)
    return max(l, min(ax, r - w)), max(t, min(ay, b - h)), w, h


class WindowHooks:
    HOTKEY_ID = 0x531
    def __init__(self, root, on_hotkey):
        root.update_idletasks()
        self.root = root
        self.hwnd = win32gui.GetAncestor(root.winfo_id(), win32con.GA_ROOT)
        style = win32gui.GetWindowLong(self.hwnd, win32con.GWL_EXSTYLE)
        win32gui.SetWindowLong(self.hwnd, win32con.GWL_EXSTYLE, style | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TOOLWINDOW)
        self.previous = None
        def proc(hwnd, msg, wparam, lparam):
            if msg == win32con.WM_MOUSEACTIVATE:
                return 3  # MA_NOACTIVATE
            if msg == win32con.WM_HOTKEY and wparam == self.HOTKEY_ID:
                root.after(0, on_hotkey)
                return 0
            return win32gui.CallWindowProc(self.previous, hwnd, msg, wparam, lparam)
        self.proc = proc
        self.previous = win32gui.SetWindowLong(self.hwnd, win32con.GWL_WNDPROC, self.proc)
        self.current = None

    def register(self, hotkey):
        flags, key = parse_hotkey(hotkey)
        if self.current == hotkey:
            return
        user32.UnregisterHotKey(self.hwnd, self.HOTKEY_ID)
        if not user32.RegisterHotKey(self.hwnd, self.HOTKEY_ID, flags, key):
            if self.current:
                flags, key = parse_hotkey(self.current)
                user32.RegisterHotKey(self.hwnd, self.HOTKEY_ID, flags, key)
            raise ValueError("快捷键被其他软件占用，请换一个组合。")
        self.current = hotkey

    def close(self):
        user32.UnregisterHotKey(self.hwnd, self.HOTKEY_ID)
        if win32gui.IsWindow(self.hwnd):
            setter = user32.SetWindowLongPtrW
            setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
            setter.restype = ctypes.c_ssize_t
            setter(self.hwnd, win32con.GWL_WNDPROC, self.previous)


def set_autostart(enabled, root):
    import winreg
    import subprocess
    command = [sys.executable] if getattr(sys, "frozen", False) else [str(root / ".venv" / "Scripts" / "pythonw.exe"), str(root / "main.py")]
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
        if enabled:
            winreg.SetValueEx(key, "SpeakDesktop", 0, winreg.REG_SZ, subprocess.list2cmdline(command))
        else:
            try:
                winreg.DeleteValue(key, "SpeakDesktop")
            except FileNotFoundError:
                pass
