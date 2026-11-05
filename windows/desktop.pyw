import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import edge_tts
from playback import Player


PROJECT = Path(__file__).resolve().parents[1]
SAMPLES = PROJECT / "samples"
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
STARTER_VOICES = (
    DEFAULT_VOICE, "zh-CN-YunxiNeural", "zh-CN-YunjianNeural",
    "zh-CN-YunyangNeural", "zh-TW-HsiaoChenNeural", "en-US-JennyNeural",
)
SAMPLE_TEXT = "你好，这是 NeuralNote 的中文旁白语音合成测试。"


def synthesize(text, voice, rate, output):
    with NamedTemporaryFile(dir=output.parent, suffix=".mp3", delete=False) as file:
        temporary = Path(file.name)
    try:
        asyncio.run(edge_tts.Communicate(
            text, voice, rate=rate, connect_timeout=15, receive_timeout=30,
        ).save(str(temporary)))
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def fetch_voices():
    return sorted({voice["ShortName"] for voice in asyncio.run(edge_tts.list_voices())})


class Workbench:
    def __init__(self, root):
        self.root = root
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.future = None
        self.last_export = None
        self.audio_path = None
        self.player = Player()
        self.playback_poll = None
        self.track = tk.StringVar(value="尚无可播放音频")
        self.play_state = tk.StringVar(value="生成试听或导出后即可播放")
        self.output_dir = SAMPLES
        self.voice_names = sorted(STARTER_VOICES)
        self.voice = tk.StringVar(value=DEFAULT_VOICE)
        self.query = tk.StringVar()
        self.rate = tk.StringVar(value="-3")
        self.count = tk.StringVar(value="0 字符")
        self.status = tk.StringVar(value="就绪  ·  合成和更新音色需要联网")
        self.export_status = tk.StringVar(value="本次尚未导出音频")
        self.configure_window()
        self.build()
        self.query.trace_add("write", self.filter_voices)
        root.bind("<Control-o>", lambda event: self.import_text())
        root.bind("<Control-s>", lambda event: self.export())
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.editor.focus_set()

    def configure_window(self):
        self.root.title("Edge TTS · 中文配音")
        self.root.geometry("960x700")
        self.root.minsize(790, 630)
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=("Microsoft YaHei UI", 10))
        style.configure("TFrame", background="#fbf8f1")
        style.configure("TLabel", background="#fbf8f1", foreground="#262a25")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 25, "bold"))
        style.configure("Section.TLabel", font=("Microsoft YaHei UI", 15, "bold"))
        style.configure("Muted.TLabel", foreground="#595e53")
        style.configure("TButton", background="#eae4d8", foreground="#262a25",
                        borderwidth=0, padding=(15, 8), focuscolor="#225548")
        style.map("TButton", background=[("active", "#e0d8c9")],
                  foreground=[("disabled", "#74776c")])
        style.configure("Accent.TButton", background="#225548", foreground="white")
        style.map("Accent.TButton",
                  background=[("disabled", "#eae4d8"), ("active", "#184538")],
                  foreground=[("disabled", "#74776c")])
        for widget in ("TEntry", "TCombobox", "TSpinbox"):
            style.configure(widget, padding=7, fieldbackground="#fffdf9",
                            foreground="#262a25", bordercolor="#a8aa99")

    def build(self):
        shell = ttk.Frame(self.root, padding=(30, 20))
        shell.pack(fill="both", expand=True)
        ttk.Label(shell, text="EDGE TTS    /    配音工具", style="Muted.TLabel").pack(anchor="w")
        navigation = ttk.Frame(shell)
        navigation.pack(fill="x", pady=(12, 18))
        self.steps = []
        for index, label in enumerate(("01  写稿", "02  试音", "03  导出")):
            button = ttk.Button(navigation, text=label, command=lambda i=index: self.show_page(i))
            button.pack(side="left", padx=(0, 8))
            self.steps.append(button)
        footer = ttk.Frame(shell)
        footer.pack(side="bottom", fill="x", pady=(12, 0))
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=80)
        ttk.Label(footer, textvariable=self.status, style="Muted.TLabel",
                  wraplength=650).pack(side="left", fill="x", expand=True)
        ttk.Separator(shell).pack(side="bottom", fill="x", pady=(8, 0))

        stack = ttk.Frame(shell)
        stack.pack(fill="both", expand=True)
        stack.columnconfigure(0, weight=1)
        stack.rowconfigure(0, weight=1)
        self.pages = [ttk.Frame(stack) for _ in self.steps]
        self.current_page = None
        self.build_editor(self.pages[0])
        self.build_settings(self.pages[1])
        self.build_export(self.pages[2])
        self.show_page(0)

    def show_page(self, index):
        if self.current_page == 1 and index != 1 and self.playback_poll is not None:
            if not self.stop_audio():
                return
        if self.current_page is not None:
            self.pages[self.current_page].grid_remove()
        self.current_page = index
        self.pages[index].grid(row=0, column=0, sticky="nsew")
        for step, button in enumerate(self.steps):
            button.configure(style="Accent.TButton" if step == index else "TButton")
        if index == 0:
            self.editor.focus_set()
        else:
            self.steps[index].focus_set()

    def heading(self, panel, title, description):
        ttk.Label(panel, text=title, style="Title.TLabel").pack(anchor="w")
        ttk.Label(panel, text=description, style="Muted.TLabel").pack(anchor="w", pady=(4, 14))

    def build_editor(self, panel):
        self.heading(panel, "写下旁白", "粘贴文稿或导入 UTF-8 文本。")
        toolbar = ttk.Frame(panel)
        toolbar.pack(fill="x", pady=(0, 9))
        ttk.Button(toolbar, text="导入 TXT", command=self.import_text).pack(side="left")
        ttk.Button(toolbar, text="载入示例", command=self.load_sample).pack(side="left", padx=6)
        ttk.Button(toolbar, text="清空", command=lambda: self.replace_text("")).pack(side="left")
        ttk.Label(toolbar, textvariable=self.count, style="Muted.TLabel").pack(side="right")
        actions = ttk.Frame(panel)
        actions.pack(side="bottom", fill="x", pady=(12, 0))
        ttk.Button(actions, text="下一步：试音 →", style="Accent.TButton",
                   command=lambda: self.show_page(1)).pack(side="right")

        editor_box = ttk.Frame(panel)
        editor_box.pack(fill="both", expand=True)
        self.editor = tk.Text(
            editor_box, wrap="word", undo=True, font=("Microsoft YaHei UI", 13),
            foreground="#262a25", background="#fffdf9", insertbackground="#225548",
            selectbackground="#cee3d8", selectforeground="#262a25", highlightthickness=1,
            highlightbackground="#a8aa99", highlightcolor="#225548", relief="flat",
            padx=16, pady=12, height=4, spacing3=6,
        )
        scrollbar = ttk.Scrollbar(editor_box, command=self.editor.yview)
        self.editor.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.editor.pack(side="left", fill="both", expand=True)
        self.editor.bind("<<Modified>>", self.update_count)

    def build_settings(self, panel):
        self.heading(panel, "找到合适的声音", "选择音色和语速，用文稿开头最多 100 字试听。")
        form = ttk.Frame(panel)
        form.pack(anchor="w", fill="x")
        ttk.Label(form, text="搜索音色").grid(row=0, column=0, sticky="w", padx=(0, 18))
        ttk.Entry(form, textvariable=self.query, width=31).grid(row=0, column=1, sticky="ew")
        ttk.Label(form, text="所选音色").grid(row=1, column=0, sticky="w")
        self.voices = ttk.Combobox(form, textvariable=self.voice, width=30,
                                   values=self.voice_names)
        self.voices.grid(row=1, column=1, sticky="ew", pady=8)
        self.refresh_button = ttk.Button(form, text="更新在线音色", command=self.refresh)
        self.refresh_button.grid(row=1, column=2, padx=8, sticky="w")
        ttk.Label(form, text="语速 (%)").grid(row=2, column=0, sticky="w")
        ttk.Spinbox(form, textvariable=self.rate, from_=-100, to=100,
                    width=7).grid(row=2, column=1, sticky="w")
        ttk.Button(form, text="重置语速", command=lambda: self.rate.set("-3")).grid(
            row=2, column=2, padx=8, sticky="w")

        ttk.Separator(panel).pack(fill="x", pady=14)
        ttk.Label(panel, text="音频播放器", style="Section.TLabel").pack(anchor="w")
        ttk.Label(panel, textvariable=self.track, wraplength=590).pack(anchor="w", pady=(4, 0))
        ttk.Label(panel, textvariable=self.play_state, style="Muted.TLabel").pack(anchor="w")
        controls = ttk.Frame(panel)
        controls.pack(fill="x", pady=(8, 0))
        self.preview_button = ttk.Button(controls, text="生成试听", style="Accent.TButton",
                                         command=self.preview)
        self.preview_button.pack(side="left")
        self.play_button = ttk.Button(controls, text="播放", state="disabled",
                                      command=self.play_audio)
        self.play_button.pack(side="left", padx=8)
        self.stop_button = ttk.Button(controls, text="停止", state="disabled",
                                      command=self.stop_audio)
        self.stop_button.pack(side="left")
        ttk.Button(controls, text="下一步：导出 →",
                   command=lambda: self.show_page(2)).pack(side="right")

    def build_export(self, panel):
        self.heading(panel, "导出完整旁白", "将全文合成为 MP3，保存到你选择的位置。")
        self.export_summary = tk.StringVar()
        ttk.Label(panel, textvariable=self.export_summary).pack(anchor="w", pady=(3, 16))
        for variable in (self.voice, self.rate, self.count):
            variable.trace_add("write", self.update_export_summary)
        self.update_export_summary()
        self.export_button = ttk.Button(panel, text="选择位置并导出 MP3",
                                        style="Accent.TButton", command=self.export)
        self.export_button.pack(anchor="w")
        ttk.Separator(panel).pack(fill="x", pady=20)
        ttk.Label(panel, text="最近导出", style="Section.TLabel").pack(anchor="w")
        self.export_status.set("本次尚未导出音频")
        ttk.Label(panel, textvariable=self.export_status, wraplength=650,
                  style="Muted.TLabel").pack(anchor="w", pady=(6, 10))
        actions = ttk.Frame(panel)
        actions.pack(fill="x")
        self.review_button = ttk.Button(actions, text="试听导出的 MP3", state="disabled",
                                        command=self.review_export)
        self.review_button.pack(side="left")
        self.folder_button = ttk.Button(actions, text="打开文件夹", state="disabled",
                                        command=self.open_export_dir)
        self.folder_button.pack(side="left", padx=8)

    def update_export_summary(self, *_):
        self.export_summary.set(
            f"{self.count.get()}   ·   {self.voice.get()}   ·   语速 {self.rate.get()}%"
        )

    def update_count(self, event=None):
        if self.editor.edit_modified():
            self.count.set(f"{len(self.editor.get('1.0', 'end-1c')):,} 字符")
            self.editor.edit_modified(False)

    def replace_text(self, text):
        self.editor.edit_separator()
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", text)
        self.editor.edit_separator()
        self.editor.focus_set()

    def import_text(self):
        path = filedialog.askopenfilename(
            parent=self.root, filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            self.replace_text(Path(path).read_text(encoding="utf-8-sig"))
            self.show_page(0)
        except (OSError, UnicodeError) as exc:
            messagebox.showerror("读取失败", f"请使用 UTF-8 文本文件。\n{exc}", parent=self.root)

    def load_sample(self):
        self.replace_text(SAMPLE_TEXT)

    def filter_voices(self, *_):
        query = self.query.get().strip().casefold()
        matches = [voice for voice in self.voice_names if query in voice.casefold()]
        self.voices.configure(values=matches)

    def refresh(self):
        self.run_job("正在获取在线音色…", fetch_voices, self.show_voices)

    def show_voices(self, voices):
        self.voice_names = voices
        self.filter_voices()
        self.status.set(f"已加载 {len(voices)} 个在线音色。可按语言或名称搜索。")

    def read_settings(self, *, preview=False):
        text = self.editor.get("1.0", "end-1c").strip()
        voice = self.voice.get().strip()
        try:
            rate = int(self.rate.get())
            if not -100 <= rate <= 100:
                raise ValueError
        except ValueError:
            messagebox.showwarning("语速无效", "请输入 -100 到 100 之间的整数。", parent=self.root)
            return None
        if not voice or (not preview and not text):
            messagebox.showwarning("缺少输入", "请输入文稿并选择音色。", parent=self.root)
            return None
        return ((text or SAMPLE_TEXT)[:100] if preview else text), voice, f"{rate:+d}%"

    def preview(self):
        settings = self.read_settings(preview=True)
        if settings and self.stop_audio():
            self.run_job("正在合成试听片段…", synthesize, self.play_preview,
                         *settings, SAMPLES / "preview.mp3")

    def export(self):
        if self.future is not None:
            return
        settings = self.read_settings()
        if not settings:
            return
        name = filedialog.asksaveasfilename(
            parent=self.root, initialdir=self.output_dir, initialfile="narration.mp3",
            defaultextension=".mp3", filetypes=[("MP3 音频", "*.mp3")],
        )
        if not name:
            return
        output = Path(name)
        if output.suffix.lower() != ".mp3":
            messagebox.showwarning("文件类型", "请使用 .mp3 扩展名。", parent=self.root)
            return
        if not self.stop_audio():
            return
        self.run_job("正在合成完整文稿…", synthesize, self.show_export, *settings, output)

    def open_path(self, path):
        try:
            os.startfile(path)
        except OSError as exc:
            messagebox.showerror("打开失败", str(exc), parent=self.root)
            return False
        return True

    def load_audio(self, path, label):
        if not self.stop_audio():
            return
        self.audio_path = path
        self.track.set(label)
        self.play_state.set("就绪 · 点击播放从头试听")
        self.play_button.configure(state="normal")

    def play_preview(self, path):
        self.status.set("试听片段已生成")
        self.load_audio(path, "试听片段 · 文稿开头")
        if self.current_page == 1:
            self.play_audio()

    def show_export(self, path):
        self.last_export = path
        self.output_dir = path.parent
        self.export_status.set(str(path))
        self.review_button.configure(state="normal")
        self.status.set(f"已导出 {path}")
        self.folder_button.configure(state="normal")
        self.load_audio(path, f"已导出 · {path.name}")

    def play_audio(self):
        if self.audio_path is None or self.future is not None:
            return
        try:
            self.player.play(self.audio_path)
        except OSError as exc:
            self.play_state.set("播放失败")
            messagebox.showerror("播放失败", str(exc), parent=self.root)
            return
        self.play_state.set("正在播放")
        self.play_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.playback_poll = self.root.after(150, self.check_playback)

    def check_playback(self):
        self.playback_poll = None
        try:
            if self.player.playing():
                self.playback_poll = self.root.after(150, self.check_playback)
                return
        except OSError as exc:
            self.play_state.set(f"播放状态读取失败 · {exc}")
            return
        self.stop_audio("播放完成")

    def stop_audio(self, status="已停止 · 可从头重播"):
        try:
            self.player.stop()
        except OSError as exc:
            messagebox.showerror("停止失败", str(exc), parent=self.root)
            return False
        if self.playback_poll is not None:
            self.root.after_cancel(self.playback_poll)
            self.playback_poll = None
        self.stop_button.configure(state="disabled")
        if self.audio_path is not None:
            self.play_button.configure(state="normal")
            self.play_state.set(status)
        return True

    def review_export(self):
        self.show_page(1)
        self.load_audio(self.last_export, f"已导出 · {self.last_export.name}")

    def open_export_dir(self):
        self.open_path(self.last_export.parent)

    def run_job(self, status, operation, done, *args):
        if self.future is not None:
            return
        self.status.set(status)
        self.play_button.configure(state="disabled")
        for button in (self.export_button, self.preview_button, self.refresh_button):
            button.configure(state="disabled")
        self.progress.pack(side="right")
        self.progress.start(12)
        self.future = self.executor.submit(operation, *args)
        self.root.after(100, self.check_job, done)

    def check_job(self, done):
        if not self.future.done():
            self.root.after(100, self.check_job, done)
            return
        future, self.future = self.future, None
        self.progress.stop()
        self.progress.pack_forget()
        if self.audio_path is not None and self.playback_poll is None:
            self.play_button.configure(state="normal")
        for button in (self.export_button, self.preview_button, self.refresh_button):
            button.configure(state="normal")
        try:
            result = future.result()
        except Exception as exc:
            self.status.set("操作失败 · 可检查网络、音色或保存路径后重试")
            messagebox.showerror("操作失败", str(exc) or type(exc).__name__, parent=self.root)
        else:
            done(result)

    def close(self):
        if self.future is not None:
            self.status.set("当前任务仍在运行，完成后即可关闭。")
            return
        if not self.stop_audio():
            return
        self.executor.shutdown()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    Workbench(root)
    root.mainloop()
