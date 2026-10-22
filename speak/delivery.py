"""Deliver one result to its focused editor, retrying transient focus/clipboard failures."""
from __future__ import annotations

import time
from dataclasses import dataclass

from . import windows


@dataclass
class PendingText:
    task_id: str
    text: str
    target: object
    window: tuple | None
    expires: float


class TextDelivery:
    def __init__(self, schedule, clock=time.monotonic, timeout=30):
        self.schedule = schedule
        self.clock = clock
        self.timeout = timeout
        self.pending = None
        self.next_attempt = 0

    def queue(self, task, text):
        target = task.target
        window = (target.hwnd, target.pid) if target else task.target_window
        self.pending = PendingText(task.id, text, target, window, self.clock() + self.timeout)
        self.next_attempt = 0

    def cancel(self):
        self.pending = None

    def attempt(self, active_task):
        pending = self.pending
        if pending is None:
            return None
        if active_task is None or active_task.id != pending.task_id or active_task.cancel.is_set():
            self.cancel()
            return "cancelled"
        now = self.clock()
        if now >= pending.expires:
            self.cancel()
            return "expired"
        if now < self.next_attempt:
            return "waiting"
        self.next_attempt = now + 0.25
        if pending.target is None and pending.window is not None:
            target = windows.capture_target()
            if target and (target.hwnd, target.pid) == pending.window:
                pending.target = target
        if pending.target is None:
            return "waiting"
        if windows.paste(pending.target, pending.text, self.schedule):
            self.cancel()
            return "inserted"
        return "waiting"
