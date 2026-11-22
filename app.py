from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import ctypes
import os
import re
import shutil
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk

from PIL import Image, ImageDraw, ImageFont, ImageTk

from staff import find_staff


FROZEN = getattr(sys, "frozen", False)
APP_DIR = Path(sys.executable if FROZEN else __file__).resolve().parent
BUNDLED_FONT = APP_DIR / "fonts" / "NotoSansSC.ttf"


@dataclass
class Cut:
    image: Image.Image
    count: int
    top: int
    spacing: float | None
    time: float
    box: tuple[int, int, int, int]


def find_ffmpeg():
    bundled = APP_DIR / "ffmpeg" / "ffmpeg.exe"
    if bundled.is_file():
        return bundled
    path = shutil.which("ffmpeg")
    if path:
        return Path(path)
    legion = Path("C:/Program Files (x86)/Lenovo/LegionZone")
    return next(legion.glob("*/SEGamingAI/services/editor/ffmpeg.exe"), None)


def ffmpeg_run(exe, args):
    set_dll_directory = ctypes.windll.kernel32.SetDllDirectoryW if FROZEN and sys.platform == "win32" else None
    if set_dll_directory is not None:
        # PyInstaller's DLL directory would otherwise be inherited by FFmpeg.
        set_dll_directory(None)
    try:
        return subprocess.run(
            [str(exe), "-nostdin", "-hide_banner", *map(str, args)],
            capture_output=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    finally:
        if set_dll_directory is not None:
            set_dll_directory(sys._MEIPASS)


def video_duration(exe, path):
    result = ffmpeg_run(exe, ["-i", path])
    details = result.stderr.decode("utf-8", errors="replace")
    duration = re.search(r"Duration:\s+(\d+):(\d+):([\d.]+)", details)
    if not duration or "Video:" not in details:
        raise ValueError("无法读取这个视频的时长或画面。")
    hours, minutes, seconds = map(float, duration.groups())
    return hours * 3600 + minutes * 60 + seconds


def video_frame(exe, path, seconds):
    result = ffmpeg_run(exe, [
        "-loglevel", "error", "-ss", f"{seconds:.3f}", "-i", path,
        "-map", "0:v:0", "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "-",
    ])
    if result.returncode or not result.stdout:
        raise ValueError("这一时间点没有可用画面，请稍微往前移。")
    with Image.open(BytesIO(result.stdout)) as decoded:
        return decoded.convert("RGB")


def compose(cuts):
    pieces = []
    spacing = next((item.spacing for item in cuts if item.spacing), None)
    for item in cuts:
        image = item.image
        factor = spacing / item.spacing if spacing and item.spacing else 1
        if factor != 1:
            image = image.resize(
                (max(1, round(image.width * factor)), max(1, round(image.height * factor))),
                Image.Resampling.LANCZOS,
            )
        pieces.append((image, round(item.top * factor)))
    above = max(top for _, top in pieces)
    below = max(image.height - top for image, top in pieces)
    row = Image.new("RGB", (sum(image.width for image, _ in pieces), above + below), "white")
    x = 0
    for image, top in pieces:
        row.paste(image, (x, above - top))
        x += image.width
    return row


def group_cuts(cuts):
    rows, pending, count = [], [], 0
    for cut in cuts:
        if not 1 <= cut.count <= 4 - count:
            raise ValueError("加入的小节不能跨过当前行。")
        pending.append(cut)
        count += cut.count
        if count == 4:
            rows.append((count, compose(pending)))
            pending, count = [], 0
    if pending:
        rows.append((count, compose(pending)))
    return rows


def write_pdf(path, title, rows):
    if not rows:
        raise ValueError("请先加入谱面。")
    windows_fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    regular = next((candidate for candidate in (
        BUNDLED_FONT, windows_fonts / "msyh.ttc", windows_fonts / "segoeui.ttf",
    ) if candidate.is_file()), None)
    if regular is None:
        raise OSError("找不到用于导出 PDF 的字体。")
    font = ImageFont.truetype(str(regular), 27)
    heading = ImageFont.truetype(str(regular), 47)
    if regular == BUNDLED_FONT:
        font.set_variation_by_name("Regular")
        heading.set_variation_by_name("Medium")
    size = (1654, 2339)
    pages = []
    number = 1
    page_count = (len(rows) + 5) // 6
    title_width = heading.getlength(title)
    if title_width > 1510:
        while heading.getlength(title + "…") > 1510:
            title = title[:-1]
        title += "…"
    for page_index in range(page_count):
        page = Image.new("RGB", size, "white")
        draw = ImageDraw.Draw(page)
        draw.text((72, 56), title, font=heading, fill="#19262d")
        draw.line((72, 131, 1582, 131), fill="#9dafb4", width=2)
        draw.text((1582, 2248), f"{page_index + 1} / {page_count}", anchor="ra", font=font, fill="#495961")
        for j, (count, row) in enumerate(rows[page_index * 6:(page_index + 1) * 6]):
            y = 175 + j * 342
            last = number + count - 1
            label = f"第 {number}–{last} 小节" if count > 1 else f"第 {number} 小节"
            draw.text((72, y), label, font=font, fill="#495961")
            scale = min(1510 / row.width, 280 / row.height)
            thumb = row.resize((max(1, round(row.width * scale)), max(1, round(row.height * scale))),
                               Image.Resampling.LANCZOS)
            page.paste(thumb, (72, y + 45))
            number = last + 1
        pages.append(page)
    pages[0].save(path, format="PDF", save_all=True, append_images=pages[1:],
                  resolution=200, quality=95, title=title)


def format_time(seconds):
    minutes, remainder = divmod(seconds, 60)
    return f"{int(minutes):02d}:{remainder:05.2f}"


def parse_time(value):
    fields = value.strip().split(":")
    if not 1 <= len(fields) <= 3:
        raise ValueError("时间可输入秒数或 分:秒，例如 1:05.5。")
    return sum(float(part) * 60 ** place for place, part in enumerate(reversed(fields)))


class App:
    def __init__(self, root):
        self.root = root
        self.ffmpeg = find_ffmpeg()
        self.path = None
        self.frame = None
        self.duration = 0
        self.time = 0
        self.cuts = []
        self.box = None
        self.drag = None
        self.anchor = None
        root.title("视频取谱工具")
        root.geometry("1170x810")
        root.minsize(860, 580)

        main = ttk.Frame(root, padding=12)
        main.pack(fill="both", expand=True)
        bar = ttk.Frame(main)
        bar.pack(fill="x")
        ttk.Button(bar, text="打开视频", command=self.open_video).pack(side="left")
        ttk.Button(bar, text="预览谱面", command=self.preview).pack(side="left", padx=6)
        ttk.Button(bar, text="导出 PDF", command=self.export).pack(side="left")
        ttk.Label(bar, text="页首标题沿用原视频文件名").pack(side="left", padx=(8, 0))
        self.file_label = ttk.Label(bar, text="请选择含谱面的视频", width=60)
        self.file_label.pack(side="left", padx=14)

        tip = "拖动框住谱面，左右贴小节线，上下留出和弦名和节奏符号。也可右键点最上面的弦线来对齐。"
        ttk.Label(main, text=tip).pack(anchor="w", pady=(10, 5))
        self.canvas = tk.Canvas(main, background="#20282e", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _: self.redraw())
        self.canvas.bind("<ButtonPress-1>", self.begin_crop)
        self.canvas.bind("<B1-Motion>", self.drag_crop)
        self.canvas.bind("<ButtonRelease-1>", self.end_crop)
        self.canvas.bind("<Button-3>", self.mark_staff)

        self.timeline = ttk.Scale(main, from_=0, to=1, command=self.slider_moved)
        self.timeline.pack(fill="x", pady=(6, 0))
        self.timeline.bind("<ButtonRelease-1>", lambda _: self.seek(self.timeline.get()))
        self.timeline.bind("<KeyRelease>", lambda _: self.seek(self.timeline.get()))

        controls = ttk.Frame(main)
        controls.pack(fill="x", pady=5)
        for label, step in (("− 5 秒", -5), ("− 1 秒", -1), ("+ 1 秒", 1), ("+ 5 秒", 5)):
            ttk.Button(controls, text=label, width=8,
                       command=lambda step=step: self.seek(self.time + step)).pack(side="left", padx=(0, 3))
        self.time_text = tk.StringVar(value="00:00.00")
        entry = ttk.Entry(controls, textvariable=self.time_text, width=12)
        entry.pack(side="left", padx=(12, 3))
        entry.bind("<Return>", lambda _: self.jump())
        ttk.Button(controls, text="跳转", command=self.jump, width=7).pack(side="left")
        self.duration_label = ttk.Label(controls)
        self.duration_label.pack(side="left", padx=10)

        actions = ttk.Frame(main)
        actions.pack(fill="x", pady=(3, 0))
        ttk.Label(actions, text="框内有").pack(side="left")
        self.add_buttons = []
        for count in range(1, 5):
            button = ttk.Button(actions, text=f"{count} 小节", width=9, command=lambda count=count: self.add(count))
            button.pack(side="left", padx=3)
            self.add_buttons.append(button)
        self.undo_button = ttk.Button(actions, text="撤回上次加入", command=self.undo)
        self.undo_button.pack(side="left", padx=(12, 5))
        self.progress_label = ttk.Label(actions)
        self.progress_label.pack(side="left", padx=6)
        self.status = ttk.Label(main, text="打开视频后，拖动进度条找谱面。")
        self.status.pack(anchor="w", pady=(6, 0))
        self.refresh()
        root.protocol("WM_DELETE_WINDOW", self.close)

    def close(self):
        if self.cuts and not self.exported:
            if not messagebox.askyesno("关闭工具", "本次有尚未导出的谱面，确定关闭？", parent=self.root):
                return
        self.root.destroy()

    def open_video(self):
        path = filedialog.askopenfilename(parent=self.root, title="选择视频",
                                           filetypes=[("视频", "*.mp4 *.mkv *.mov *.webm *.avi *.m4v"), ("所有文件", "*.*")])
        if not path:
            return
        if self.cuts and not messagebox.askyesno("打开新视频", "打开新视频会清空当前选段，继续吗？", parent=self.root):
            return
        if self.ffmpeg is None:
            chosen = filedialog.askopenfilename(parent=self.root, title="选择 ffmpeg.exe",
                                                filetypes=[("FFmpeg", "ffmpeg.exe")])
            if not chosen:
                return
            self.ffmpeg = Path(chosen)
        self.status.configure(text="正在打开视频…")
        self.root.update_idletasks()
        try:
            duration = video_duration(self.ffmpeg, path)
            frame = video_frame(self.ffmpeg, path, 0)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            self.error("视频打开失败", error)
            return
        self.path = Path(path)
        self.duration = duration
        self.cuts.clear()
        self.exported = False
        self.file_label.configure(text=self.path.name)
        self.duration_label.configure(text=f"总时长 {format_time(duration)}")
        self.timeline.configure(to=max(0.001, self.duration - 0.08))
        self.show_frame(frame, 0)

    def slider_moved(self, value):
        if self.path:
            self.time_text.set(format_time(float(value)))

    def jump(self):
        try:
            self.seek(parse_time(self.time_text.get()))
        except (ValueError, OverflowError):
            self.error("时间格式", "请输入秒数或 分:秒，例如 1:05.5。")

    def seek(self, seconds):
        if self.path is None:
            return
        seconds = float(seconds)
        if not 0 <= abs(seconds) < float("inf"):
            raise ValueError("时间必须是有效数字。")
        seconds = max(0, min(seconds, self.duration - 0.08))
        self.status.configure(text=f"正在取帧 {format_time(seconds)}…")
        self.root.update_idletasks()
        try:
            frame = video_frame(self.ffmpeg, self.path, seconds)
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            self.error("取帧失败", error)
            return
        self.show_frame(frame, seconds)

    def show_frame(self, frame, seconds):
        self.frame, self.time = frame, seconds
        self.timeline.set(seconds)
        self.time_text.set(format_time(seconds))
        self.box = self.drag = self.anchor = None
        self.redraw()
        self.refresh()
        self.status.configure(text=f"当前画面 {format_time(seconds)}。请框选一段连续谱面。")

    def redraw(self):
        canvas = self.canvas
        canvas.delete("all")
        if self.frame is None:
            canvas.create_text(canvas.winfo_width() / 2, canvas.winfo_height() / 2,
                               text="打开一个含六线谱画面的视频", fill="#f4f8fa", font="TkHeadingFont")
            return
        w, h = self.frame.size
        scale = min(max(1, canvas.winfo_width()) / w, max(1, canvas.winfo_height()) / h)
        dw, dh = max(1, round(w * scale)), max(1, round(h * scale))
        self.origin = ((canvas.winfo_width() - dw) // 2, (canvas.winfo_height() - dh) // 2)
        self.zoom = (dw / w, dh / h)
        self.photo = ImageTk.PhotoImage(self.frame.resize((dw, dh), Image.Resampling.LANCZOS))
        canvas.create_image(*self.origin, anchor="nw", image=self.photo)
        self.draw_crop()

    def point(self, event):
        ox, oy = self.origin
        sx, sy = self.zoom
        return (max(0, min(self.frame.width, round((event.x - ox) / sx))),
                max(0, min(self.frame.height, round((event.y - oy) / sy))))

    def draw_crop(self):
        self.canvas.delete("crop")
        if not self.box:
            return
        ox, oy = self.origin
        sx, sy = self.zoom
        x0, y0, x1, y1 = self.box
        self.canvas.create_rectangle(ox + x0 * sx, oy + y0 * sy, ox + x1 * sx, oy + y1 * sy,
                                     fill="", outline="#16c5ae", width=2, tags="crop")
        if self.anchor is not None:
            self.canvas.create_line(ox + x0 * sx, oy + self.anchor * sy, ox + x1 * sx,
                                    oy + self.anchor * sy, fill="#16c5ae", width=2, dash=(5, 3), tags="crop")

    def begin_crop(self, event):
        if self.frame is not None:
            self.drag = self.point(event)
            self.box = self.anchor = None
            self.draw_crop()
            self.refresh()

    def drag_crop(self, event):
        if self.drag is not None:
            x, y = self.point(event)
            x0, y0 = self.drag
            self.box = (min(x, x0), min(y, y0), max(x, x0), max(y, y0))
            self.draw_crop()

    def end_crop(self, event):
        if self.drag is not None:
            self.drag_crop(event)
            self.drag = None
            x0, y0, x1, y1 = self.box
            if x1 - x0 < 24 or y1 - y0 < 24:
                self.box = None
            self.draw_crop()
            self.refresh()

    def mark_staff(self, event):
        if self.box is not None:
            x, y = self.point(event)
            x0, y0, x1, y1 = self.box
            if x0 <= x <= x1 and y0 <= y < y1:
                self.anchor = y
                self.draw_crop()

    def refresh(self):
        total = sum(cut.count for cut in self.cuts)
        remaining = 4 - total % 4
        self.progress_label.configure(text=f"已收录 {total} 小节 · 本行还能加入 {remaining} 小节")
        for n, button in enumerate(self.add_buttons, 1):
            button.configure(state="normal" if self.box and n <= remaining else "disabled")
        self.undo_button.configure(state="normal" if self.cuts else "disabled")

    def add(self, count):
        if self.box is None:
            return
        if any(cut.time == self.time and cut.box == self.box for cut in self.cuts):
            self.error("重复选区", "同一帧的这个选区已经加入。请选下一段。")
            return
        image = self.frame.crop(self.box)
        lines = find_staff(image)
        if self.anchor is not None:
            top = self.anchor - self.box[1]
        elif lines:
            top = lines[0]
        else:
            self.error("需要标记第一根弦线", "自动识别未找到六根线。请在选框内右键点最上面的弦线，然后再加入。")
            return
        spacing = (lines[-1] - lines[0]) / 5 if lines else None
        old_count = sum(cut.count for cut in self.cuts)
        if count > 4 - old_count % 4:
            return
        self.cuts.append(Cut(image, count, top, spacing, self.time, self.box))
        self.exported = False
        self.box = self.anchor = None
        self.draw_crop()
        self.refresh()
        self.status.configure(text=f"已加入第 {old_count + 1}–{old_count + count} 小节，来自 {format_time(self.time)}。")

    def undo(self):
        if self.cuts:
            cut = self.cuts.pop()
            self.exported = False
            self.refresh()
            self.status.configure(text=f"已撤回来自 {format_time(cut.time)} 的 {cut.count} 小节。")

    def preview(self):
        rows = group_cuts(self.cuts)
        if not rows:
            self.status.configure(text="先框选并加入谱面，再打开预览。")
            return
        window = tk.Toplevel(self.root)
        window.title("已收录谱面")
        window.geometry("1060x680")
        canvas = tk.Canvas(window, background="white", highlightthickness=0)
        scroll = ttk.Scrollbar(window, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        canvas.photos = []
        number, y = 1, 16
        for count, row in rows:
            canvas.create_text(18, y, text=f"第 {number}–{number + count - 1} 小节", anchor="nw")
            thumb = row.copy()
            thumb.thumbnail((990, 220), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(thumb)
            canvas.photos.append(photo)
            canvas.create_image(18, y + 26, image=photo, anchor="nw")
            y += photo.height() + 51
            number += count
        canvas.configure(scrollregion=(0, 0, 1030, y))

    def export(self):
        if not self.cuts:
            self.status.configure(text="先框选并加入谱面，再导出 PDF。")
            return
        path = filedialog.asksaveasfilename(parent=self.root, title="导出 PDF（页首标题沿用原视频文件名）", defaultextension=".pdf",
                                            initialfile=self.path.stem + " 吉他谱.pdf", filetypes=[("PDF", "*.pdf")])
        if not path:
            return
        try:
            write_pdf(path, self.path.stem, group_cuts(self.cuts))
        except (ValueError, OSError) as error:
            self.error("导出失败", error)
            return
        self.exported = True
        self.status.configure(text=f"已导出到 {path}")

    def error(self, title, error):
        self.status.configure(text=title)
        messagebox.showerror(title, str(error), parent=self.root)


def new_window():
    registered = sys.platform == "win32" and BUNDLED_FONT.is_file() and (
        ctypes.windll.gdi32.AddFontResourceExW(str(BUNDLED_FONT), 0x10, None)
    )
    root = tk.Tk()
    if registered:
        for name in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont"):
            tkfont.nametofont(name, root=root).configure(family="Noto Sans SC")
    return root


def self_test(video, output):
    root = new_window()
    try:
        root.withdraw()
        app = App(root)
        if app.ffmpeg is None or (FROZEN and app.ffmpeg != APP_DIR / "ffmpeg" / "ffmpeg.exe"):
            raise RuntimeError("Bundled FFmpeg was not found.")
        if FROZEN and not BUNDLED_FONT.is_file():
            raise RuntimeError("Bundled font was not found.")
        duration = video_duration(app.ffmpeg, video)
        for start, second_half in ((0, False), (duration / 2, True)):
            frame = video_frame(app.ffmpeg, video, start)
            app.show_frame(frame, start)
            middle = frame.width // 2
            app.box = (middle if second_half else 0, 0, frame.width if second_half else middle, frame.height)
            if not find_staff(frame.crop(app.box)):
                raise ValueError("Fixture must show a staff in the left half, then in the right half.")
            app.add(2)
        rows = group_cuts(app.cuts)
        if len(rows) != 1 or rows[0][0] != 4:
            raise RuntimeError("Expected one row of four measures.")
        row = rows[0][1]
        split = app.cuts[0].image.width
        left = find_staff(row.crop((0, 0, split, row.height)))
        right = find_staff(row.crop((split, 0, row.width, row.height)))
        if not left or not right or any(abs(a - b) > 2 for a, b in zip(left, right)):
            raise RuntimeError("The two staves did not align.")
        write_pdf(output, "便携版自检", rows)
        with Path(output).open("rb") as stream:
            if stream.read(5) != b"%PDF-":
                raise RuntimeError("The export is not a PDF.")
    finally:
        root.destroy()


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--self-test":
        try:
            self_test(*sys.argv[2:])
        except Exception as error:
            Path(sys.argv[3] + ".error.txt").write_text(str(error), encoding="utf-8")
            raise SystemExit(1)
    else:
        root = new_window()
        App(root)
        root.mainloop()
