# ui/utils.py
from __future__ import annotations

import time

from PySide6.QtCore import QRect
from PySide6.QtGui import QGuiApplication, QScreen
from PySide6.QtWidgets import QWidget

from src.models import ClipType


# 各类型的中文名（主窗口、历史窗口共用）
_TYPE_TEXTS = {
    ClipType.TEXT: "文本",
    ClipType.IMAGE: "图片",
    ClipType.FILES: "文件",
    ClipType.HTML: "HTML",
    ClipType.UNKNOWN: "未知",
}


def clip_type_text(clip_type: ClipType) -> str:
    """剪贴板类型 -> 中文名"""
    return _TYPE_TEXTS.get(clip_type, "未知")


def human_size(num_bytes: int) -> str:
    """字节数 -> 便于阅读的字符串"""
    n = max(0, int(num_bytes or 0))
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def format_time(timestamp: float) -> str:
    """时间戳 -> 本地时间字符串"""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))
    except (OSError, ValueError, TypeError):
        return ""


# ---------------------------------------------------------
# 多屏
# ---------------------------------------------------------
def screen_for_rect(rect: QRect) -> QScreen | None:
    """
    找出 rect 所在的显示器。

    顺序：中心点所在屏 -> 重叠面积最大的屏 -> 主屏。
    窗口被拖到屏幕外时中心点可能不在任何屏上，所以用面积兜底，
    保证任何情况下都能返回一个可用的屏幕。
    """
    screen = QGuiApplication.screenAt(rect.center())
    if screen is not None:
        return screen

    best: QScreen | None = None
    best_area = 0
    for s in QGuiApplication.screens():
        inter = s.geometry().intersected(rect)
        area = inter.width() * inter.height()
        if area > best_area:
            best, best_area = s, area
    if best is not None:
        return best

    return QGuiApplication.primaryScreen()


def screen_for_widget(widget: QWidget) -> QScreen | None:
    """窗口当前所在的显示器（贴边、探出、恢复位置都按它算）"""
    return screen_for_rect(widget.frameGeometry())


def screen_by_index(index: int) -> QScreen | None:
    """
    按序号取显示器（0 = 主屏），序号越界时回退到主屏。

    多屏行为没法在单屏机器上真跑，所以留这个入口：
    可以拿第二块屏的 availableGeometry() 手工喂给贴边逻辑做对照测试。
    """
    screens = QGuiApplication.screens()
    if not screens:
        return None
    if 0 <= index < len(screens):
        return screens[index]
    return QGuiApplication.primaryScreen()


def center_on_screen(dlg: QWidget, parent: QWidget | None = None) -> None:
    """
    把 dlg 移动到屏幕绝对中央。
    调用前必须已经确定 dlg 的最终尺寸（resize 或 adjustSize）。
    本函数不修改尺寸。
    """
    screen = None
    if parent is not None and parent.isVisible():
        screen = screen_for_rect(parent.frameGeometry())
    if screen is None:
        screen = QGuiApplication.primaryScreen()
    if screen is None:              # 无显示器（远程会话 / 测试环境）
        return

    sg = screen.availableGeometry()
    w = dlg.width()
    h = dlg.height()

    x = sg.x() + (sg.width() - w) // 2
    y = sg.y() + (sg.height() - h) // 2
    dlg.move(x, y)