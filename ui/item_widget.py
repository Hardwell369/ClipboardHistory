# ui/item_widget.py
from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal, QTimer, QPoint
from PySide6.QtGui import QPixmap, QAction
from PySide6.QtWidgets import (
    QFrame, QLabel, QMenu, QVBoxLayout, QHBoxLayout,
    QSizePolicy, QInputDialog, QMessageBox,
)

from core.models import ClipItem, ClipType
from ui.elided_label import ElidedLabel
from ui.file_icons import THUMB_SIZE, icon_name_for_item, icon_pixmap
from ui.utils import clip_type_text


ITEM_HEIGHT = 72          # 单条高度
ITEM_MARGIN = 6           # 卡片间距
PREVIEW_LINES = 2         # 预览最多显示几行（放不下用省略号，见 ui/elided_label.py）
LONG_PRESS_MS = 500       # 长按触发时间


class ItemWidget(QFrame):
    """
    单条剪贴板记录卡片。
    左键点击 -> clicked(item_id)
    右键/长按 -> 弹出菜单：修改（仅文本）、删除
    """

    clicked = Signal(int)
    edit_requested = Signal(int, str)   # item_id, 当前文本
    delete_requested = Signal(int)

    def __init__(self, item: ClipItem, parent=None):
        super().__init__(parent)
        self.item = item
        self._long_press_timer = QTimer(self)
        self._long_press_timer.setSingleShot(True)
        self._long_press_timer.timeout.connect(self._on_long_press)
        self._press_pos = QPoint()

        self.setObjectName("ItemCard")
        self.setFixedHeight(ITEM_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)

        self._build_ui()

    # ---------------------------------------------------------
    # UI
    # ---------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)

        # 左侧：缩略图 / 类型图标
        self.thumb = QLabel()
        self.thumb.setFixedSize(THUMB_SIZE, THUMB_SIZE)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setObjectName("ItemThumb")
        layout.addWidget(self.thumb)

        # 右侧：预览 + 类型
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(4)

        # 预览：最多 PREVIEW_LINES 行，超出部分用省略号（自己排版，
        # 不用 QLabel 的 wordWrap —— 那个在框比文本矮时会把第一行顶出框外）
        self.preview_label = ElidedLabel(self.item.preview or "",
                                         max_lines=PREVIEW_LINES)
        self.preview_label.setObjectName("ItemPreview")

        self.type_label = QLabel(self._type_text())
        self.type_label.setObjectName("ItemType")

        right.addWidget(self.preview_label, 1)
        right.addWidget(self.type_label, 0)
        layout.addLayout(right, 1)

        self._load_thumb()

    def _type_text(self) -> str:
        return clip_type_text(self.item.type)

    def _load_thumb(self) -> None:
        dpr = self.devicePixelRatioF()

        # 图片：显示缩略图（按屏幕缩放比渲染，避免高分屏下发虚）
        if self.item.type == ClipType.IMAGE and self.item.file_path \
                and os.path.exists(self.item.file_path):
            pix = QPixmap(self.item.file_path)
            if not pix.isNull():
                edge = max(1, int(round(THUMB_SIZE * dpr)))
                scaled = pix.scaled(edge, edge,
                                    Qt.KeepAspectRatio, Qt.SmoothTransformation)
                scaled.setDevicePixelRatio(dpr)
                self.thumb.setPixmap(scaled)
                self._set_thumb_kind("image")
                return

        # 纯文本 / 文件 / HTML：显示对应的类型图标
        pix = icon_pixmap(icon_name_for_item(self.item), THUMB_SIZE, dpr)
        if not pix.isNull():
            self.thumb.setPixmap(pix)
            self._set_thumb_kind("icon")
            return

        # 图标缺失时兜底：显示类型首字
        self.thumb.setText(self._type_text()[0])
        self._set_thumb_kind("text")

    def _set_thumb_kind(self, kind: str) -> None:
        """标记缩略图种类，让 style.qss 决定要不要灰底（图标自带底色）"""
        self.thumb.setProperty("thumbKind", kind)

    # ---------------------------------------------------------
    # 鼠标事件
    # ---------------------------------------------------------
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press_pos = e.position().toPoint()
            self._long_press_timer.start(LONG_PRESS_MS)
            e.accept()
            return
        if e.button() == Qt.RightButton:
            self._show_menu(e.globalPosition().toPoint())
            e.accept()
            return
        # 其他按键才交给父类
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._long_press_timer.isActive():
            self._long_press_timer.stop()
            self.clicked.emit(self.item.id)
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def mouseMoveEvent(self, e):
        # 移动超过阈值取消长按
        if self._long_press_timer.isActive():
            delta = (e.position().toPoint() - self._press_pos).manhattanLength()
            if delta > 6:
                self._long_press_timer.stop()
        super().mouseMoveEvent(e)

    def _on_long_press(self):
        # 长按等价右键
        pos = self.mapToGlobal(self.rect().center())
        self._show_menu(pos)

    # ---------------------------------------------------------
    # 右键菜单
    # ---------------------------------------------------------
    def _show_menu(self, global_pos: QPoint) -> None:
        menu = QMenu(self)

        if self.item.type == ClipType.TEXT:
            act_edit = QAction("修改", menu)
            act_edit.triggered.connect(self._emit_edit)
            menu.addAction(act_edit)

        act_del = QAction("删除", menu)
        act_del.triggered.connect(lambda: self.delete_requested.emit(self.item.id))
        menu.addAction(act_del)

        menu.exec(global_pos)

    def _emit_edit(self) -> None:
        # 由 MainWindow 负责取正文（可能在磁盘）
        self.edit_requested.emit(self.item.id, self.item.content or "")