# src/paths.py
"""
资源路径。

源码运行时资源就在项目目录下；PyInstaller 打包后会被解包到 sys._MEIPASS，
__file__ 的位置不再可靠，所以统一从这里取，避免打包后图标/样式找不到。
"""
from __future__ import annotations

import os
import sys


def app_root() -> str:
    """资源根目录：打包后是解包目录，源码运行是项目根目录"""
    if getattr(sys, "frozen", False):
        # onefile / onedir 都会设置 _MEIPASS；拿不到就退回 exe 所在目录
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource(*parts: str) -> str:
    """拼出资源的绝对路径，例如 resource("assets", "icon.ico")"""
    return os.path.join(app_root(), *parts)
