from __future__ import annotations

import json
import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import ttk, messagebox, filedialog

from .controller import Controller
from .delivery import TextDelivery
from .providers import RecognitionError
from .storage import DATA, ROOT, Settings
from . import windows

BG, INK, MUTED, BLUE, LINE, RECORD = "#F5F8FC", "#21334C", "#64748B", "#245AC7", "#DAE3EF", "#C43F4B"


class App:
    def __init__(self, root):
        self.root = root
        self.settings = Settings.load()
        self.events = queue.Queue()
        self.controller = Controller(self.settings, self.events)
        self.delivery = TextDelivery(root.after)
        self.status = "点击开始 · 右键设置"
        self.settings_window = None
        self.history_window = None
        self.prepare_running = False
        self.prepare_cancel = threading.Event()
        self.price_status = "正在核实免费状态…"
        self.last_focus_check = 0
        self.last_cleanup = time.monotonic()
        self.shutting_down = False
        self.last_text = ""
        self.last_error = ""
        self.preview_window = None
        self.preview_widget = None
        self._preview_hwnd = None
        self.drag_origin = None
        root.title("轻声")
        root.configure(bg=BG)
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.geometry("286x78")
        self.canvas = tk.Canvas(root, width=286, height=78, bg=BG, highlightthickness=1, highlightbackground=LINE)
        self.canvas.pack()
        self.canvas.bind("<ButtonPress-1>", self.press)
        self.canvas.bind("<B1-Motion>", self.drag)
        self.canvas.bind("<ButtonRelease-1>", self.release)
        self.canvas.bind("<Button-3>", self.menu)
        self.hooks = windows.WindowHooks(root, self.toggle)
        windows.position_floating(root, self.settings.x, self.settings.y)
        try:
            self.hooks.register(self.settings.hotkey)
        except ValueError as exc:
            self.status = str(exc)
        style = ttk.Style(root)
        style.theme_use("vista")
        style.configure("TLabel", font=("Microsoft YaHei UI", 10))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(10, 6))
        style.configure("TNotebook.Tab", padding=(16, 7))
        self.tick()
        # Verify cloud pricing in background on launch. No audio or credential is sent.
        def check_price():
            try:
                self.controller.cloud.guard.check()
                self.events.put((None, "price", "已核实免费云端价格"))
            except RecognitionError as exc:
                self.events.put((None, "price", str(exc)))
        threading.Thread(target=check_price, daemon=True).start()

    def press(self, event):
        self.drag_origin = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y(), event.x)

    def drag(self, event):
        if self.drag_origin and self.drag_origin[4] > 62 and self.drag_origin[4] < 245:
            x, y, wx, wy, _ = self.drag_origin
            windows.position_floating(self.root, wx + event.x_root - x, wy + event.y_root - y)
            self._preview_position()

    def release(self, event):
        if not self.drag_origin:
            return
        x, y, _, _, initial_x = self.drag_origin
        moved = abs(event.x_root - x) + abs(event.y_root - y) > 6
        self.drag_origin = None
        if moved:
            self.settings.x, self.settings.y = self.root.winfo_x(), self.root.winfo_y()
            self.settings.save()
        elif initial_x > 245:
            self.menu(event)
        else:
            self.toggle()

    def menu(self, event):
        menu = tk.Menu(self.root, tearoff=False, font=("Microsoft YaHei UI", 10))
        menu.add_command(label="开始 / 停止录音", command=self.toggle)
        menu.add_command(label="取消本次", command=self.cancel)
        menu.add_separator()
        menu.add_command(label="设置", command=self.open_settings)
        menu.add_command(label="最近文字", command=self.open_history)
        menu.add_command(label="查看本次结果", command=self.show_result)
        menu.add_separator()
        menu.add_command(label="退出", command=self.close)
        menu.tk_popup(event.x_root, event.y_root)

    def toggle(self):
        state = self.controller.state
        if state == "recording":
            self.controller.stop_recording()
            self.status = "正在整理识别结果…"
            if self.preview_window and self.preview_window.winfo_exists():
                self.preview_header.configure(text="正在整理识别结果…")
                self.preview_stop.configure(state="disabled")
        elif state == "processing":
            self.status = "正在识别 · 右键可取消"
        elif state == "error":
            self.show_result()
        else:
            try:
                self.delivery.cancel()
                self.controller.start(windows.capture_target(), windows.foreground_identity())
                self.status = "再点一次结束"
                if self.settings.mode == "local":
                    self.open_preview()
            except RecognitionError as exc:
                self.status = str(exc)
                messagebox.showinfo("轻声", str(exc), parent=self.root)
                self.open_settings()

    def cancel(self):
        self.delivery.cancel()
        self.controller.cancel()
        self.status = "已取消 · 点击重新开始"
        self.last_error = ""
        self.close_preview()

    def tick(self):
        if self.shutting_down:
            return
        try:
            while True:
                task_id, kind, value = self.events.get_nowait()
                if kind == "prepare":
                    self.status = value
                    if hasattr(self, "prepare_var"):
                        self.prepare_var.set(value)
                    continue
                if kind == "price":
                    self.price_status = value
                    if hasattr(self, "price_var"):
                        self.price_var.set(value)
                    continue
                if not self.controller.accept_event(task_id, terminal=kind in ("result", "error")):
                    continue
                if kind == "progress":
                    if self.controller.state != "recording":
                        self.status = value
                elif kind == "error":
                    if self.controller.state == "recording":
                        self.controller.stop_recording()
                    self.controller.state = "error"
                    self.last_error = value
                    self.status = "未完成 · 点击重试"
                    self.show_result()
                elif kind == "result":
                    self.controller.state = "idle"
                    self.last_text, self.last_error = value.text, ""
                    task = self.controller.task
                    self.close_preview()
                    self.delivery.queue(task, value.text)
                    self.status = "识别完成 · 点回原输入框自动填入"
                elif kind == "preview":
                    self.update_preview(value, "本地实时预览 · 识别中…")
        except queue.Empty:
            pass
        task = self.controller.task
        if self.controller.state == "recording" and task and task.recorder:
            if task.recorder.error or (task.recorder.stream is not None and not task.recorder.stream.active):
                task.recorder.error = task.recorder.error or "麦克风已断开，录音可能不完整。"
                self.controller.stop_recording()
        if task and self.controller.state in ("recording", "processing") and task.target is None:
            if time.monotonic() - self.last_focus_check > 0.5:
                current = windows.capture_target()
                if current and (current.hwnd, current.pid) == task.target_window:
                    task.target = current
                self.last_focus_check = time.monotonic()
        delivered = self.delivery.attempt(task)
        if delivered == "inserted":
            self.status = "已填入光标位置 · 请检查后发送"
        elif delivered == "expired":
            self.status = "文字已保存 · 菜单可查看结果"
        self.controller.local.release_if_idle()
        if time.monotonic() - self.last_cleanup > 3600:
            active = [self.controller.task.path] if self.controller.task else []
            self.controller.history.clean(active)
            self.last_cleanup = time.monotonic()
        show_request = DATA / "show.request"
        if show_request.exists():
            show_request.unlink(missing_ok=True)
            self.open_settings()
        self.draw()
        self.root.after(100, self.tick)

    def draw(self):
        c = self.canvas
        c.delete("all")
        recording = self.controller.state == "recording"
        processing = self.controller.state == "processing"
        color = RECORD if recording else BLUE
        c.create_oval(12, 13, 58, 59, fill=color, outline="")
        if recording:
            c.create_rectangle(28, 29, 42, 43, fill="white", outline="")
        elif processing:
            c.create_arc(25, 26, 45, 46, start=int(time.monotonic() * 200) % 360, extent=250, style="arc", outline="white", width=3)
        else:
            c.create_oval(30, 23, 40, 39, fill="white", outline="")
            c.create_arc(25, 28, 45, 46, start=180, extent=180, style="arc", outline="white", width=2)
            c.create_line(35, 45, 35, 50, fill="white", width=2)
        mode = "云端" if self.settings.mode == "cloud" else "本地"
        title = f"轻声  ·  {mode}"
        if recording:
            elapsed = int(time.monotonic() - self.controller.task.recorder.started)
            title = f"录音中  {elapsed // 60:02d}:{elapsed % 60:02d}"
            level = min(1.0, self.controller.task.recorder.level * 3)
            c.create_rectangle(76, 64, 76 + 145 * level, 67, fill=RECORD, outline="")
        c.create_text(74, 24, text=title, anchor="w", font=("Microsoft YaHei UI", 11, "bold"), fill=INK)
        text = self.status if len(self.status) <= 22 else self.status[:21] + "…"
        c.create_text(74, 48, text=text, anchor="w", font=("Microsoft YaHei UI", 9), fill=MUTED)
        c.create_text(263, 34, text="⋮", font=("Segoe UI", 21), fill=MUTED)

    def open_preview(self):
        if self.preview_window and self.preview_window.winfo_exists():
            return
        import win32con
        import win32gui

        window = self.preview_window = tk.Toplevel(self.root)
        window.withdraw()
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.configure(bg="white", highlightthickness=1, highlightbackground=LINE)
        header = tk.Frame(window, bg="white")
        header.pack(fill="x")
        self.preview_stop = ttk.Button(header, text="停止录音", takefocus=False,
                                       command=self.stop_preview_recording)
        self.preview_stop.pack(side="right", padx=8, pady=4)
        self.preview_header = tk.Label(header, text="本地实时预览 · 等待第一段语音…", bg="white", fg=BLUE,
                                       anchor="w", padx=12, pady=8,
                                       font=("Microsoft YaHei UI", 10, "bold"))
        self.preview_header.pack(side="left", fill="x", expand=True)
        body = tk.Text(window, wrap="word", state="disabled", relief="flat", bg="white", fg=INK,
                       padx=12, pady=4, font=("Microsoft YaHei UI", 11), height=6, width=48)
        body.pack(fill="both", expand=True)
        self.preview_widget = body
        window.update_idletasks()
        hwnd = win32gui.GetAncestor(window.winfo_id(), win32con.GA_ROOT)
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE,
                               style | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TOOLWINDOW)
        self._preview_hwnd = hwnd
        window.deiconify()
        self._preview_position()

    def _preview_position(self):
        if not self.preview_window or not self.preview_window.winfo_exists():
            return
        import win32api
        import win32con
        import win32gui

        anchor = win32gui.GetWindowRect(self.hooks.hwnd)
        monitor = win32api.MonitorFromWindow(self.hooks.hwnd, win32con.MONITOR_DEFAULTTONEAREST)
        geometry = windows.preview_geometry(anchor, win32api.GetMonitorInfo(monitor)["Work"])
        if geometry is None:
            win32gui.ShowWindow(self._preview_hwnd, win32con.SW_HIDE)
            return
        win32gui.SetWindowPos(self._preview_hwnd, win32con.HWND_TOPMOST, *geometry,
                              win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW)

    def stop_preview_recording(self):
        # A queued/double click may arrive after stop; it must never start a new recording.
        if self.controller.state == "recording":
            self.toggle()

    def update_preview(self, text, title):
        if not self.preview_widget or not self.preview_window or not self.preview_window.winfo_exists():
            return
        self.preview_header.configure(text=title)
        self.preview_widget.configure(state="normal")
        self.preview_widget.delete("1.0", "end")
        self.preview_widget.insert("1.0", text)
        self.preview_widget.see("end")
        self.preview_widget.configure(state="disabled")
        self._preview_position()

    def close_preview(self):
        if self.preview_window and self.preview_window.winfo_exists():
            self.preview_window.destroy()
        self.preview_window = None
        self.preview_widget = None
        self._preview_hwnd = None

    def window(self, title, geometry):
        window = tk.Toplevel(self.root)
        window.title(title)
        window.geometry(geometry + "+180+100")
        window.minsize(560, 440)
        window.configure(bg=BG)
        window.attributes("-topmost", True)
        return window

    def open_settings(self):
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.lift()
            return
        window = self.settings_window = self.window("轻声 · 设置", "650x740")
        window.minsize(620, 720)
        header = tk.Frame(window, bg=BG, padx=22, pady=16)
        header.pack(fill="x")
        tk.Label(header, text="说出来，留在输入框。", bg=BG, fg=INK, font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w")
        tk.Label(header, text="免费云端优先 · 本地按需备用 · 由你检查并发送", bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 10)).pack(anchor="w", pady=(6, 0))
        tabs = ttk.Notebook(window)
        tabs.pack(fill="both", expand=True, padx=20)
        general, terms_tab, local_tab = (ttk.Frame(tabs, padding=18) for _ in range(3))
        tabs.add(general, text="输入与云端")
        tabs.add(terms_tab, text="术语与纠错")
        tabs.add(local_tab, text="本地备用")
        mode = tk.StringVar(value=self.settings.mode)
        ttk.Label(general, text="识别方式").pack(anchor="w")
        row = ttk.Frame(general); row.pack(fill="x", pady=(6, 14))
        ttk.Radiobutton(row, text="免费云端", variable=mode, value="cloud").pack(side="left")
        ttk.Radiobutton(row, text="本地准确模式", variable=mode, value="local").pack(side="left", padx=20)
        ttk.Label(general, text="云端在停止录音后返回整段文字；实时预览请切换到本地模式。",
                  wraplength=540, foreground=MUTED).pack(anchor="w", pady=(0, 10))
        key_title = tk.StringVar()
        ttk.Label(general, textvariable=key_title).pack(anchor="w")
        secret = tk.StringVar()
        ttk.Entry(general, textvariable=secret, show="●").pack(fill="x", pady=6)
        key_hint = tk.StringVar()
        key_status = ttk.Label(general, textvariable=key_hint, wraplength=540, font=("Microsoft YaHei UI", 10, "bold"))
        key_status.pack(anchor="w")
        def refresh_key_status():
            saved = bool(self.controller.secrets.load())
            key_title.set("替换硅基流动 API Key（可留空）" if saved else "硅基流动 API Key")
            key_hint.set("已保存并加密。输入框留空正常，不会清除已保存的 Key。" if saved else "尚未保存 API Key。请粘贴后点击“保存设置”。")
            key_status.configure(foreground="#16704A" if saved else MUTED)
        refresh_key_status()
        ttk.Label(general, text="云端会上传录音到硅基流动；仅调用核实免费的模型。", wraplength=540, foreground=MUTED).pack(anchor="w", pady=(6, 12))
        self.price_var = tk.StringVar(value=self.price_status)
        ttk.Label(general, textvariable=self.price_var, wraplength=540, foreground=MUTED).pack(anchor="w")
        ttk.Label(general, text="开始 / 停止快捷键").pack(anchor="w")
        hotkey = tk.StringVar(value=self.settings.hotkey)
        ttk.Entry(general, textvariable=hotkey).pack(fill="x", pady=6)
        ttk.Label(general, text="麦克风").pack(anchor="w", pady=(6, 0))
        import sounddevice as sd
        try:
            devices = [(None, "系统默认麦克风")] + [(i, d["name"]) for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]
        except Exception:
            devices = [(None, "系统默认麦克风（当前无法枚举设备）")]
        mic = ttk.Combobox(general, state="readonly", values=[f"{i if i is not None else '默认'} · {name}" for i, name in devices])
        mic.pack(fill="x", pady=6)
        mic.current(next((i for i, (device, _) in enumerate(devices) if device == self.settings.microphone), 0))
        startup = tk.BooleanVar(value=self.settings.autostart)
        ttk.Checkbutton(general, text="登录 Windows 后自动启动", variable=startup).pack(anchor="w", pady=8)
        ttk.Label(terms_tab, text="个人术语（每行一个）").pack(anchor="w")
        terms = tk.Text(terms_tab, height=8, font=("Microsoft YaHei UI", 10), relief="solid", borderwidth=1)
        terms.pack(fill="both", expand=True, pady=6)
        terms.insert("1.0", "\n".join(self.settings.terms))
        ttk.Label(terms_tab, text="确认过的错词 → 正确词（每行：错词=正确词）").pack(anchor="w", pady=(8, 0))
        rules = tk.Text(terms_tab, height=5, font=("Microsoft YaHei UI", 10), relief="solid", borderwidth=1)
        rules.pack(fill="both", expand=True, pady=6)
        rules.insert("1.0", "\n".join(f"{k}={v}" for k, v in self.settings.replacements.items()))
        ttk.Label(terms_tab, text="云端接口不支持热词参数；本地暂不传整张术语表，避免模型把词表回显。纠错只使用你在这里确认的规则。", wraplength=540, foreground=MUTED).pack(anchor="w")
        ttk.Label(local_tab, text="Qwen3-ASR-1.7B", font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
        ttk.Label(local_tab, text="模型下载约 1.95GB。云端模式不加载本地模型；本地空闲 5 分钟后释放内存和显存。", wraplength=530).pack(anchor="w", pady=12)
        self.prepare_var = tk.StringVar(value="本地文件已就绪" if self.controller.local.available() else "尚未准备完整")
        ttk.Label(local_tab, textvariable=self.prepare_var, wraplength=530, foreground=MUTED).pack(anchor="w", pady=8)
        ttk.Button(local_tab, text="准备 / 继续下载本地模型", command=self.prepare_local).pack(anchor="w", pady=8)
        ttk.Button(local_tab, text="暂停下载（保留进度）", command=self.prepare_cancel.set).pack(anchor="w", pady=4)
        ttk.Button(local_tab, text="导入已有模型目录", command=self.import_model).pack(anchor="w", pady=8)
        ttk.Label(local_tab, text="本地模式每隔约 8 秒更新一次屏幕预览，最终结果在停止录音后确认并填入。初次加载速度和识别准确率仍待实测；云端 Qwen 在接口及准确率验证完成前不会开放。",
                  wraplength=530, foreground=MUTED).pack(anchor="w", pady=14)
        footer = ttk.Frame(window, padding=18); footer.pack(fill="x", side="bottom", before=tabs)
        save_status = tk.StringVar()
        ttk.Label(window, textvariable=save_status, foreground="#16704A", padding=(20, 0, 20, 6)).pack(fill="x")
        def save():
            if self.controller.state in ("recording", "processing"):
                messagebox.showinfo("正在使用", "请结束或取消本次录音后再保存设置。", parent=window); return
            try:
                parsed = {}
                for line in rules.get("1.0", "end").splitlines():
                    if not line.strip(): continue
                    if "=" not in line or not line.split("=", 1)[0].strip():
                        raise ValueError("纠错规则请使用：错词=正确词")
                    k, v = line.split("=", 1)
                    if len(k.strip()) < 2:
                        raise ValueError("错词至少两个字符，避免单字规则替换整段句子。")
                    parsed[k.strip()] = v.strip()
                self.hooks.register(hotkey.get().strip())
                if secret.get().strip(): self.controller.secrets.save(secret.get())
                if startup.get() != self.settings.autostart: windows.set_autostart(startup.get(), ROOT)
                self.settings.mode, self.settings.hotkey = mode.get(), hotkey.get().strip()
                self.settings.microphone, self.settings.autostart = devices[mic.current()][0], startup.get()
                self.settings.terms = [s.strip() for s in terms.get("1.0", "end").splitlines() if s.strip()][:200]
                self.settings.replacements = parsed
                self.settings.save()
                if self.settings.mode == "cloud": self.controller.local.stop()
                self.status = "设置已保存 · 点击开始"
                secret.set("")
                refresh_key_status()
                save_status.set("设置已保存。关闭此窗口后即可开始语音输入。")
            except Exception as exc:
                messagebox.showerror("无法保存", str(exc) if isinstance(exc, ValueError) else f"保存失败（{type(exc).__name__}）", parent=window)
        ttk.Button(footer, text="最近文字", command=self.open_history).pack(side="left")
        def delete_key():
            self.controller.secrets.delete()
            secret.set("")
            refresh_key_status()
            save_status.set("已删除保存的 API Key。")
        ttk.Button(footer, text="删除密钥", command=delete_key).pack(side="left", padx=8)
        ttk.Button(footer, text="保存设置", command=save).pack(side="right")

    def prepare_local(self):
        if self.prepare_running:
            return
        if self.controller.state in ("recording", "processing") or self.controller.local.busy:
            messagebox.showinfo("稍后准备", "请等待当前任务结束后再准备模型。", parent=self.root)
            return
        self.controller.local.stop()
        self.prepare_running = True
        self.prepare_cancel.clear()
        def work():
            from .prepare import prepare
            try:
                prepare(lambda s: self.events.put((None, "prepare", s)), self.prepare_cancel)
            except InterruptedError:
                self.events.put((None, "prepare", "下载已暂停，进度已保留。"))
            except Exception as exc:
                self.events.put((None, "prepare", f"准备失败（{type(exc).__name__}），已保留下载进度，可再次点击继续。"))
            finally:
                self.prepare_running = False
        threading.Thread(target=work, daemon=True).start()

    def import_model(self):
        if self.controller.state in ("recording", "processing") or self.prepare_running or self.controller.local.busy:
            messagebox.showinfo("稍后导入", "请等待当前任务结束。", parent=self.settings_window); return
        directory = filedialog.askdirectory(title="选择包含 ONNX 和 GGUF 文件的模型目录", parent=self.settings_window)
        if not directory: return
        from .prepare import MODEL_DIR, MODEL_FILES
        from pathlib import Path
        import shutil
        source = Path(directory)
        if not all((source / name).exists() for name in MODEL_FILES):
            messagebox.showerror("模型不完整", "目录中需要两个 encoder ONNX 文件和 qwen3_asr_llm.gguf。", parent=self.settings_window); return
        self.controller.local.stop()
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        if source.resolve() != MODEL_DIR.resolve():
            for path in source.iterdir():
                if path.is_file(): shutil.copy2(path, MODEL_DIR / path.name)
        (MODEL_DIR / "prepared.json").write_text(json.dumps({"source": "user-import", "integrity": "not publisher-verified"}), "utf-8")
        self.prepare_var.set("模型已导入；若运行库未就绪，请点击准备本地模型。")

    def show_result(self):
        task = self.controller.task
        shown_task_id = task.id if task else None
        window = self.window("轻声 · 本次结果", "650x480")
        message = self.last_error or ("已保存在最近文字。可复制后粘贴到目标输入框。" if self.last_text else "还没有识别结果。")
        ttk.Label(window, text=message, wraplength=580, padding=20).pack(fill="x")
        text = tk.Text(window, wrap="word", font=("Microsoft YaHei UI", 11), padx=12, pady=12)
        text.pack(fill="both", expand=True, padx=20)
        text.insert("1.0", self.last_text if not self.last_error else "")
        buttons = ttk.Frame(window, padding=18); buttons.pack(fill="x")
        ttk.Button(buttons, text="复制文字", command=lambda: windows.set_clipboard(text.get("1.0", "end-1c"))).pack(side="left")
        if self.last_error:
            def is_current_error():
                current = self.controller.task
                return current is not None and current.id == shown_task_id and self.controller.state == "error"
            def retry(mode):
                if not is_current_error():
                    window.destroy()
                    return
                self.last_error = ""
                self.controller.retry(mode)
                self.status = "正在重试…"
                window.destroy()
            ttk.Button(buttons, text="重试云端", command=lambda: retry("cloud")).pack(side="left", padx=6)
            ttk.Button(buttons, text="本地识别", command=lambda: retry("local")).pack(side="left", padx=6)
            def discard():
                if is_current_error():
                    self.cancel()
                window.destroy()
            ttk.Button(buttons, text="丢弃并重录", command=discard).pack(side="right")
        else:
            ttk.Button(buttons, text="关闭", command=window.destroy).pack(side="right")

    def open_history(self):
        if self.history_window and self.history_window.winfo_exists():
            self.history_window.destroy()
        window = self.history_window = self.window("轻声 · 最近 7 天文字", "760x560")
        rows = self.controller.history.list()
        ttk.Label(window, text="最近文字", font=("Microsoft YaHei UI", 16, "bold"), padding=18).pack(anchor="w")
        listing = tk.Listbox(window, height=7, font=("Microsoft YaHei UI", 10), exportselection=False)
        listing.pack(fill="x", padx=18)
        for row in rows:
            listing.insert("end", f"{datetime.fromtimestamp(row[1]):%m-%d %H:%M}  {row[2][:65]}")
        text = tk.Text(window, wrap="word", font=("Microsoft YaHei UI", 11), padx=10, pady=10)
        text.pack(fill="both", expand=True, padx=18, pady=10)
        def select(event=None):
            if listing.curselection():
                text.delete("1.0", "end"); text.insert("1.0", rows[listing.curselection()[0]][2])
        listing.bind("<<ListboxSelect>>", select)
        if rows: listing.selection_set(0); select()
        controls = ttk.Frame(window, padding=18); controls.pack(fill="x")
        ttk.Button(controls, text="复制文字", command=lambda: windows.set_clipboard(text.get("1.0", "end-1c"))).pack(side="left")
        def recover():
            if self.controller.state in ("recording", "processing"):
                return
            path = filedialog.askopenfilename(title="恢复最近 24 小时暂存录音", initialdir=str(self.controller.history.audio_dir), filetypes=[("录音", "*.wav")], parent=window)
            if path:
                self.last_error = ""
                try:
                    self.controller.resume(path, self.settings.mode)
                except RecognitionError as exc:
                    messagebox.showerror("无法恢复", str(exc), parent=window)
                    return
                self.status = "正在恢复识别…"
                window.destroy()
        ttk.Button(controls, text="恢复暂存录音", command=recover).pack(side="left", padx=6)
        def clear():
            if self.controller.state in ("recording", "processing"):
                messagebox.showinfo("正在使用", "请先结束或取消当前任务。", parent=window); return
            if messagebox.askyesno("清空历史", "删除全部历史文字和暂存录音？", parent=window):
                self.controller.history.clear(); rows.clear(); listing.delete(0, "end"); text.delete("1.0", "end")
        ttk.Button(controls, text="清空历史与暂存录音", command=clear).pack(side="right")

    def close(self):
        self.shutting_down = True
        self.delivery.cancel()
        self.prepare_cancel.set()
        self.controller.shutdown()
        self.close_preview()
        self.hooks.close()
        try:
            windows.restore_pending_clipboard()
        except Exception:
            pass
        self.root.destroy()
