# ui/uninstall_tk.py
"""
卸载器界面（tkinter）。

为什么不用 Qt：卸载器是**独立的小 exe**，塞一份 PySide6 要多背 40 MB
（安装包直接从 117 MB 涨到 150 MB 以上）。卸载界面本来就简单——几个勾选框、
一个进度文本、一个结果页——标准库的 tkinter 够用，打出来只有十几 MB。

界面分两段，对应两个进程：
1. ask_choices()       安装目录里的 uninstaller.exe 跑：问用户删什么（带条数/体积）
2. run_with_progress() 复制到 TEMP 的副本跑：显示进度与结果
   （必须换进程的原因见 src/uninstall.py：正在运行的 exe 删不掉自己）

两个容易踩的坑（都踩过，所以写在这里）：
- **窗口尺寸不能写死**：高 DPI（150%/200%）下 tk 的 scaling 会让字体大一倍，
  写死 560x520 会把底部按钮挤出可见区域。这里按内容请求尺寸算（_fit_window）。
- **pack 顺序**：底部按钮栏必须**先** pack（side="bottom"），
  否则中间的 expand=True 区域会把剩余空间全吃掉，按钮栏被压成 1 像素高。

tkinter 起不来（极端受限环境）时全部退到原生 MessageBox，绝不出现「点了没反应」。
"""
from __future__ import annotations

import ctypes
import os
import queue
import threading
import tkinter as tk
from tkinter import ttk
from typing import Callable

from src.data_clean import CATEGORY_KEYS, DirStat, format_size
from src.version import APP_NAME

APP_TITLE = f"卸载 {APP_NAME}"        # 与 uninstaller.py 的 TITLE 保持一致
FONT_FAMILY = "Microsoft YaHei UI"
WRAP = 580                            # 文字折行宽度（像素，按窗口宽度调）
BG = "#f5f6f8"
FG = "#1f2329"
MUTED = "#6b7280"
WARN = "#c0392b"

MB_OK = 0x00000000
MB_YESNO = 0x00000004
MB_ICONERROR = 0x00000010
MB_ICONQUESTION = 0x00000020
MB_ICONWARNING = 0x00000030
MB_ICONINFORMATION = 0x00000040
MB_TOPMOST = 0x00040000
IDYES = 6


# ---------------------------------------------------------
# 基础设施
# ---------------------------------------------------------
def make_dpi_aware() -> None:
    """让窗口在高 DPI 下不糊（打包版默认是 DPI 不感知的）"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:                       # noqa: BLE001 - 老系统没有 shcore
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:                   # noqa: BLE001
            pass


def message_box(text: str, title: str = APP_TITLE,
                flags: int = MB_OK | MB_ICONINFORMATION) -> int:
    """原生消息框（tkinter 不可用时的兜底）"""
    try:
        user32 = ctypes.windll.user32
        user32.MessageBoxW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                       ctypes.c_wchar_p, ctypes.c_uint]
        user32.MessageBoxW.restype = ctypes.c_int
        return int(user32.MessageBoxW(None, text, title, flags | MB_TOPMOST))
    except Exception:                       # noqa: BLE001
        return IDYES


def _icon_path() -> str:
    """卸载器自带的图标（打在 assets/ 里；取不到就不设图标）"""
    import sys

    candidates = []
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        candidates.append(os.path.join(meipass, "assets", "icon.ico"))
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(here, os.pardir, "assets", "icon.ico"))
    for path in candidates:
        if os.path.isfile(path):
            return os.path.abspath(path)
    return ""


def _new_root(title: str) -> tk.Tk:
    """建窗口（**不设固定尺寸**，等界面搭完再按内容量）"""
    make_dpi_aware()                        # 兜底：入口没设置时也要设置
    root = tk.Tk()
    root.title(title)
    root.configure(bg=BG)
    root.resizable(True, True)
    icon = _icon_path()
    if icon:
        try:
            root.iconbitmap(icon)
        except Exception:                   # noqa: BLE001 - 图标设不上不影响卸载
            pass
    root.attributes("-topmost", True)
    root.after(600, lambda: root.attributes("-topmost", False))
    return root


def _fit_window(win: tk.Tk, min_width: int = 660,
                min_height: int = 420) -> None:
    """
    按内容请求尺寸摆好窗口。

    高 DPI 下字体可能大一倍，所以尺寸不能写死：先让 tk 算好各控件的请求尺寸，
    再取「请求尺寸」和屏幕的 90% 之间的较小值，这样按钮永远不会被挤出屏幕。
    """
    win.update_idletasks()
    width = max(min_width, win.winfo_reqwidth())
    height = max(min_height, win.winfo_reqheight())
    screen_w = win.winfo_screenwidth()
    screen_h = win.winfo_screenheight()
    width = min(width, int(screen_w * 0.95))
    height = min(height, int(screen_h * 0.90))
    x = max(0, (screen_w - width) // 2)
    y = max(0, (screen_h - height) // 3)
    win.geometry(f"{width}x{height}+{x}+{y}")


def _style(root: tk.Tk) -> ttk.Style:
    style = ttk.Style(root)
    try:
        style.theme_use("vista")
    except tk.TclError:
        pass
    root.option_add("*Font", (FONT_FAMILY, 10))
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=FG,
                    font=(FONT_FAMILY, 10))
    style.configure("Title.TLabel", font=(FONT_FAMILY, 14, "bold"))
    style.configure("Muted.TLabel", foreground=MUTED, font=(FONT_FAMILY, 9))
    style.configure("Warn.TLabel", foreground=WARN, font=(FONT_FAMILY, 10))
    style.configure("TCheckbutton", background=BG, foreground=FG,
                    font=(FONT_FAMILY, 10))
    style.configure("TButton", font=(FONT_FAMILY, 10), padding=(12, 6))
    return style


def _text_area(parent: tk.Widget, height: int = 9) -> tk.Text:
    frame = tk.Frame(parent, bg="#ffffff", highlightthickness=1,
                     highlightbackground="#d5d8dd")
    frame.pack(fill="both", expand=True)
    text = tk.Text(frame, height=height, wrap="word", bd=0, bg="#ffffff",
                   fg=FG, font=(FONT_FAMILY, 9), padx=10, pady=8,
                   highlightthickness=0)
    scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    text.pack(side="left", fill="both", expand=True)
    text.configure(state="disabled")
    return text


def _append(text: tk.Text, line: str) -> None:
    text.configure(state="normal")
    text.insert("end", line + "\n")
    text.see("end")
    text.configure(state="disabled")


def _copy_to_clipboard(root: tk.Tk, content: str, button: ttk.Button) -> None:
    try:
        root.clipboard_clear()
        root.clipboard_append(content)
        button.configure(text="已复制")
    except tk.TclError:
        pass


def _categories():
    from src.data_clean import CATEGORIES
    return CATEGORIES


# ---------------------------------------------------------
# 第一段：问用户删什么
# ---------------------------------------------------------
def ask_choices(install_dir: str, stats: list[DirStat],
                version: str = "") -> dict | None:
    """
    显示「卸载 + 删除哪些数据」的选择窗口。

    返回 {"categories": [...], "close_running": bool} 或 None（用户取消）。
    """
    try:
        root = _new_root(APP_TITLE)
    except Exception as e:                  # noqa: BLE001 - tkinter 起不来时退到原生框
        return _fallback_choices(e, stats)

    _style(root)
    result: dict | None = None

    # 底部按钮栏**先** pack：否则中间 expand=True 的区域会把它的高度吃光
    foot = ttk.Frame(root, padding=(16, 8, 16, 14))
    foot.pack(fill="x", side="bottom")

    head = ttk.Frame(root, padding=(16, 14, 16, 6))
    head.pack(fill="x")
    ttk.Label(head, text=f"卸载 {APP_NAME} {version}".strip(),
              style="Title.TLabel").pack(anchor="w")
    ttk.Label(head, text=f"程序目录：{install_dir}", style="Muted.TLabel",
              wraplength=WRAP, justify="left").pack(anchor="w", pady=(6, 0))

    body = ttk.Frame(root, padding=(16, 4, 16, 4))
    body.pack(fill="both", expand=True)
    ttk.Label(body, text="程序文件一定会被删除。本机保存的剪贴板数据怎么处理？",
              wraplength=WRAP, justify="left").pack(anchor="w", pady=(6, 6))

    # ---- 数据目录概览 ----
    total_disk = sum(stat.total_disk for stat in stats)
    total_count = sum(stat.total_count for stat in stats)
    info_lines = []
    for stat in stats:
        if not stat.exists:
            continue
        detail = (f"{stat.total_count} 条记录" if stat.has_db else "仅配置/日志")
        # 缩进两格而不是加「·」：长路径折行时不会出现孤零零一个点的难看情况
        info_lines.append(f"   {stat.path}（{detail}，{format_size(stat.total_disk)}）")
    if not info_lines:
        info_lines.append("   （没有找到本机数据目录）")
    ttk.Label(body, text="\n".join(info_lines), style="Muted.TLabel",
              wraplength=WRAP, justify="left").pack(anchor="w")

    # ---- 勾选 ----
    box = ttk.Frame(body)
    box.pack(fill="x", pady=(12, 4))
    ttk.Label(box, text="要删除的内容：").pack(anchor="w")

    vars_map: dict[str, tk.BooleanVar] = {}
    counts: dict[str, tuple[int, int]] = {}
    for stat in stats:
        for key, cat in stat.categories.items():
            cnt, disk = counts.get(key, (0, 0))
            counts[key] = (cnt + cat.count, disk + cat.disk)

    for key, title, _types in _categories():
        count, disk = counts.get(key, (0, 0))
        var = tk.BooleanVar(value=count > 0)
        vars_map[key] = var
        label = f"{title} —— {count} 条，{format_size(disk)}"
        ttk.Checkbutton(box, text=label, variable=var,
                        state=("normal" if count > 0 else "disabled")).pack(
            anchor="w", pady=1)

    ttk.Label(box,
              text=("删除后无法恢复。设置项与日志只有在上面全部勾选、数据已清空时"
                    "才会一并删除，否则会保留（下次安装还能沿用）。"),
              style="Warn.TLabel", wraplength=WRAP, justify="left").pack(
        anchor="w", pady=(10, 0))

    def select_all(value: bool) -> None:
        for key, var in vars_map.items():
            if counts.get(key, (0, 0))[0] > 0:
                var.set(value)

    links = ttk.Frame(body)
    links.pack(fill="x", pady=(8, 0))
    ttk.Button(links, text="全选（清理干净）",
               command=lambda: select_all(True)).pack(side="left")
    ttk.Button(links, text="全不选（保留数据）",
               command=lambda: select_all(False)).pack(side="left", padx=(8, 0))
    ttk.Label(links, text=f"合计 {total_count} 条 / {format_size(total_disk)}",
              style="Muted.TLabel").pack(side="right")

    def on_cancel() -> None:
        nonlocal result
        result = None
        root.destroy()

    def on_confirm() -> None:
        nonlocal result
        result = {
            "categories": [k for k, v in vars_map.items() if v.get()],
            "close_running": True,
        }
        root.destroy()

    ttk.Button(foot, text="取消", command=on_cancel).pack(side="right")
    ttk.Button(foot, text="开始卸载", command=on_confirm).pack(side="right",
                                                              padx=(0, 8))

    root.protocol("WM_DELETE_WINDOW", on_cancel)
    _fit_window(root, min_width=660, min_height=460)
    root.mainloop()
    return result


def _fallback_choices(err: Exception, stats: list[DirStat]) -> dict | None:
    """tkinter 用不了时的兜底：原生消息框二选一"""
    count = sum(stat.total_count for stat in stats)
    text = (f"确定要卸载{APP_NAME}吗？\n\n"
            f"程序文件一定会被删除。\n"
            f"本机保存的数据（{count} 条记录）要一起删除吗？\n\n"
            f"是 = 删除全部文本、图片、文件（不可恢复）\n"
            f"否 = 保留数据，只删程序\n\n"
            f"（界面无法显示：{type(err).__name__}: {err}）")
    answer = message_box(text, APP_TITLE, MB_YESNO | MB_ICONWARNING)
    if answer == IDYES:
        return {"categories": list(CATEGORY_KEYS), "close_running": True}
    return {"categories": [], "close_running": True}


# ---------------------------------------------------------
# 第二段：进度 + 结果
# ---------------------------------------------------------
def run_with_progress(worker: Callable[[Callable[[str], None]], tuple[bool, str]],
                      title: str = APP_TITLE) -> int:
    """
    跑卸载任务并显示进度/结果。

    worker(progress) 在工作线程里执行，返回 (ok, 报告文本)；
    progress(文本) 往界面上追加一行。
    """
    try:
        root = _new_root(title)
    except Exception:                       # noqa: BLE001
        ok, report = worker(lambda line: None)
        message_box(report, title,
                    MB_OK | (MB_ICONINFORMATION if ok else MB_ICONWARNING))
        return 0 if ok else 1

    _style(root)

    # 底部按钮栏先 pack（同 ask_choices 的说明）
    foot = ttk.Frame(root, padding=(16, 8, 16, 14))
    foot.pack(fill="x", side="bottom")

    head = ttk.Frame(root, padding=(16, 14, 16, 4))
    head.pack(fill="x")
    title_label = ttk.Label(head, text="正在卸载…", style="Title.TLabel")
    title_label.pack(anchor="w")

    bar = ttk.Progressbar(head, mode="indeterminate", length=560)
    bar.pack(fill="x", pady=(10, 0))
    bar.start(12)

    body = ttk.Frame(root, padding=(16, 8, 16, 4))
    body.pack(fill="both", expand=True)
    text = _text_area(body, height=9)

    messages: queue.Queue = queue.Queue()
    state: dict[str, object] = {}

    def progress(message: str) -> None:
        messages.put(message)

    def work() -> None:
        try:
            ok, report = worker(progress)
        except Exception as e:              # noqa: BLE001 - 任何异常都要让用户看到
            ok, report = False, f"卸载过程中出错：{type(e).__name__}: {e}"
        state["ok"] = ok
        state["report"] = report
        messages.put(None)

    threading.Thread(target=work, daemon=True).start()

    def finish() -> None:
        ok = bool(state.get("ok"))
        report = str(state.get("report") or "")
        bar.stop()
        bar.pack_forget()
        title_label.configure(text="卸载完成" if ok else "卸载完成（有需要留意的地方）")
        _append(text, "")
        _append(text, report)
        for child in foot.winfo_children():
            child.destroy()

        def copy_report() -> None:
            _copy_to_clipboard(root, report, copy_btn)

        copy_btn = ttk.Button(foot, text="复制报告", command=copy_report)
        copy_btn.pack(side="right")
        ttk.Button(foot, text="关闭", command=root.destroy).pack(
            side="right", padx=(0, 8))
        root.protocol("WM_DELETE_WINDOW", root.destroy)
        _fit_window(root, min_width=680, min_height=440)

    def poll() -> None:
        while True:
            try:
                item = messages.get_nowait()
            except queue.Empty:
                break
            if item is None:
                finish()
                return
            _append(text, str(item))
        root.after(120, poll)

    root.after(120, poll)
    _fit_window(root, min_width=680, min_height=440)
    root.mainloop()
    return 0 if state.get("ok") else 1
