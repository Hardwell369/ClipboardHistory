# core/manual.py
"""
用户手册（docs/用户手册.txt）的定位与打开。

手册是随程序发布的纯文本文件，用系统默认程序（一般是记事本）打开，
这样用户能自己缩放、搜索、打印，也能直接拷给别人。

路径要兼容两种运行方式：
- 打包后：PyInstaller 会把 docs 一起解包到 sys._MEIPASS（onedir 下就是
  _internal\\docs\\用户手册.txt），另外也允许把手册放在 exe 同级目录；
- 源码运行：项目根目录下的 docs\\用户手册.txt。

这里只用标准库，不依赖 Qt —— 真正的「打不开就在程序里显示」由 ui/help.py 负责。
"""
from __future__ import annotations

import os
import subprocess
import sys

from core import applog
from core.paths import app_root, resource

MANUAL_FILE_NAME = "用户手册.txt"
MANUAL_SUBDIR = "docs"


def manual_candidates() -> list[str]:
    """按优先级列出手册可能的位置（去重，保持顺序）"""
    out: list[str] = []

    # 1) 打包后的解包目录 / 源码的项目目录
    out.append(resource(MANUAL_SUBDIR, MANUAL_FILE_NAME))

    # 2) 可执行文件旁边：方便用户自己替换/补充手册（绿色版常见做法）
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = app_root()
    out.append(os.path.join(base, MANUAL_SUBDIR, MANUAL_FILE_NAME))
    out.append(os.path.join(base, MANUAL_FILE_NAME))

    seen: set[str] = set()
    uniq: list[str] = []
    for path in out:
        key = os.path.normcase(os.path.abspath(path))
        if key not in seen:
            seen.add(key)
            uniq.append(path)
    return uniq


def manual_path() -> str:
    """实际存在的手册文件；都没有就返回首选路径（用于提示用户放哪）"""
    for path in manual_candidates():
        if os.path.isfile(path):
            return path
    return manual_candidates()[0]


def read_manual_text() -> str:
    """读取手册全文（读不到返回空串，绝不抛异常）"""
    path = manual_path()
    try:
        # 手册是给记事本看的，存的是 UTF-8 with BOM + CRLF
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            return f.read()
    except OSError as e:
        applog.get("manual").error("读取用户手册失败：%s（%s）", path, e)
        return ""


def open_with_editor(path: str | None = None) -> tuple[bool, str]:
    """
    用系统默认程序打开手册，返回 (是否成功, 说明)。

    两条路：
    1. os.startfile（走 ShellExecute，用 .txt 的默认关联程序）；
    2. 关联被改坏 / 被安全软件拦时，直接用 %SystemRoot%\\System32\\notepad.exe。
    """
    log = applog.get("manual")
    target = path or manual_path()
    if not os.path.isfile(target):
        return False, f"手册文件不存在：{target}"

    try:
        os.startfile(target)                    # type: ignore[attr-defined]
        log.info("已用默认程序打开用户手册：%s", target)
        return True, "已用默认程序打开"
    except OSError as e:
        log.warning("默认程序打不开手册（%s），改用 notepad.exe", e)
        first_error = f"os.startfile：{e}"

    notepad = os.path.join(os.environ.get("SystemRoot") or r"C:\Windows",
                           "System32", "notepad.exe")
    if not os.path.isfile(notepad):
        notepad = "notepad.exe"
    try:
        subprocess.Popen([notepad, os.path.normpath(target)], close_fds=True)
        log.info("已用 notepad.exe 打开用户手册：%s", target)
        return True, "已用记事本打开"
    except OSError as e:
        log.error("记事本也打不开手册：%s（%s）", target, e)
        return False, f"{first_error}；notepad.exe：{e}"


def is_manual_available() -> bool:
    return os.path.isfile(manual_path())
