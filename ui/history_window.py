# ui/history_window.py
"""
独立的历史记录窗口。

主窗口只显示最新的一两条、且只显示截断后的预览；
这个窗口平铺列出全部记录，并把每条的内容尽量完整地展示出来：

- 文本  : 显示正文（超过 TEXT_DISPLAY_LIMIT 个字符时截断，可点“全文”查看/复制）
- 文件  : 每个路径单独一行，不再只显示第一个名字
- 图片  : 96×96 缩略图（图片已失效时退回类型图标）
- HTML  : 显示源码开头

窗口是独立的顶层窗口，关闭时只隐藏，可从托盘右键重新打开。
"""
from __future__ import annotations

import json
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from core.clipboard_io import write_item_to_clipboard
from core.models import ClipItem, ClipType
from core.storage import Storage
from ui.edit_dialog import DEFAULT_VIEW_TITLE, EditTextDialog, TextPreviewDialog
from ui.file_icons import icon_name_for_item, icon_pixmap
from ui.utils import center_on_screen, clip_type_text, format_time, human_size


WINDOW_TITLE = "剪贴板历史记录"
DEFAULT_SIZE = (880, 640)
MIN_SIZE = (560, 380)

ICON_SIZE = 48            # 类型图标边长
IMAGE_THUMB = 96          # 图片缩略图边长
TEXT_DISPLAY_LIMIT = 600  # 单条文本在列表里最多显示多少个字符


# ---------------------------------------------------------
# 工具
# ---------------------------------------------------------
def _item_paths(item: ClipItem) -> list[str]:
    """FILES 记录的 content 是 JSON 路径列表"""
    if not item.content:
        return []
    try:
        data = json.loads(item.content)
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    if isinstance(data, list):
        return [p for p in data if isinstance(p, str)]
    return []


def _read_text_head(item: ClipItem, limit: int) -> str:
    """读取文本开头（落盘文本只读前 limit 个字符，避免整文件读入）"""
    if item.file_path and os.path.exists(item.file_path):
        try:
            with open(item.file_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read(limit)
        except OSError:
            return ""
    return item.content or ""


class HistoryCard(QFrame):
    """一条完整记录的卡片"""

    copy_requested = Signal(int)
    edit_requested = Signal(int)
    delete_requested = Signal(int)
    full_text_requested = Signal(int)

    def __init__(self, item: ClipItem, parent=None):
        super().__init__(parent)
        self.item = item
        self.truncated = False

        self.setObjectName("HistoryCard")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self._build_ui()

    # ---------------------------------------------------------
    # UI
    # ---------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(12)

        # 左：缩略图 / 类型图标
        self.thumb = QLabel()
        self.thumb.setObjectName("ItemThumb")      # 复用主窗口缩略图样式
        self.thumb.setAlignment(Qt.AlignCenter)
        edge = IMAGE_THUMB if self.item.type == ClipType.IMAGE else ICON_SIZE
        self.thumb.setFixedSize(edge, edge)
        outer.addWidget(self.thumb, 0, Qt.AlignTop)

        # 右：正文 + 信息 + 按钮
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(6)

        self.content_label = QLabel(self._content_text())
        self.content_label.setObjectName("HistoryContent")
        self.content_label.setWordWrap(True)
        # 内容里可能是 HTML/XML 源码，必须按纯文本显示，否则 QLabel 会当成富文本渲染
        self.content_label.setTextFormat(Qt.PlainText)
        self.content_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.content_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        right.addWidget(self.content_label)

        self.meta_label = QLabel(self._meta_text())
        self.meta_label.setObjectName("HistoryMeta")
        right.addWidget(self.meta_label)

        right.addLayout(self._build_buttons())
        outer.addLayout(right, 1)

        self._load_thumb()

    def _build_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        if self.truncated:
            btn_full = self._make_button("全文", "查看完整内容")
            btn_full.clicked.connect(
                lambda: self.full_text_requested.emit(self.item.id))
            row.addWidget(btn_full)

        btn_copy = self._make_button("复制", "复制到剪贴板")
        btn_copy.clicked.connect(lambda: self.copy_requested.emit(self.item.id))
        row.addWidget(btn_copy)

        if self.item.type == ClipType.TEXT:
            btn_edit = self._make_button("修改", "修改这条文本")
            btn_edit.clicked.connect(
                lambda: self.edit_requested.emit(self.item.id))
            row.addWidget(btn_edit)

        btn_del = self._make_button("删除", "删除这条记录")
        btn_del.setObjectName("HistoryButtonDanger")
        btn_del.clicked.connect(lambda: self.delete_requested.emit(self.item.id))
        row.addWidget(btn_del)

        row.addStretch(1)
        return row

    @staticmethod
    def _make_button(text: str, tip: str = "") -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("HistoryButton")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedHeight(24)
        btn.setMinimumWidth(56)
        if tip:
            btn.setToolTip(tip)
        return btn

    # ---------------------------------------------------------
    # 内容
    # ---------------------------------------------------------
    def _content_text(self) -> str:
        item = self.item

        if item.type == ClipType.FILES:
            paths = _item_paths(item)
            return "\n".join(paths) if paths else "（记录里没有路径）"

        if item.type == ClipType.IMAGE:
            return item.preview or "图片"

        if item.type == ClipType.TEXT:
            text = _read_text_head(item, TEXT_DISPLAY_LIMIT + 1)
            if len(text) > TEXT_DISPLAY_LIMIT:
                self.truncated = True
                return text[:TEXT_DISPLAY_LIMIT] + " …"
            return text

        # HTML 等：显示源码开头
        text = item.content or item.preview or ""
        if len(text) > TEXT_DISPLAY_LIMIT:
            self.truncated = True
            return text[:TEXT_DISPLAY_LIMIT] + " …"
        return text

    def _meta_text(self) -> str:
        parts = [
            clip_type_text(self.item.type),
            human_size(self.item.size),
            format_time(self.item.created_at),
        ]
        if self.item.type == ClipType.TEXT and self.item.file_path:
            parts.append("正文在磁盘")
        if self.item.type == ClipType.FILES:
            n = len(_item_paths(self.item))
            if n > 1:
                parts.append(f"{n} 个文件")
        return "  ·  ".join(p for p in parts if p)

    def _load_thumb(self) -> None:
        dpr = self.devicePixelRatioF()

        if self.item.type == ClipType.IMAGE and self.item.file_path \
                and os.path.exists(self.item.file_path):
            pix = QPixmap(self.item.file_path)
            if not pix.isNull():
                edge = max(1, int(round(IMAGE_THUMB * dpr)))
                scaled = pix.scaled(edge, edge,
                                    Qt.KeepAspectRatio, Qt.SmoothTransformation)
                scaled.setDevicePixelRatio(dpr)
                self.thumb.setPixmap(scaled)
                self.thumb.setProperty("thumbKind", "image")
                return

        pix = icon_pixmap(icon_name_for_item(self.item), ICON_SIZE, dpr)
        if not pix.isNull():
            self.thumb.setPixmap(pix)
            self.thumb.setProperty("thumbKind", "icon")
            return

        self.thumb.setText(clip_type_text(self.item.type)[0])
        self.thumb.setProperty("thumbKind", "text")

    # ---------------------------------------------------------
    # 右键菜单（与按钮等价）
    # ---------------------------------------------------------
    def contextMenuEvent(self, e) -> None:
        menu = QMenu(self)
        if self.truncated:
            act_full = QAction("查看全文", menu)
            act_full.triggered.connect(
                lambda: self.full_text_requested.emit(self.item.id))
            menu.addAction(act_full)

        act_copy = QAction("复制", menu)
        act_copy.triggered.connect(lambda: self.copy_requested.emit(self.item.id))
        menu.addAction(act_copy)

        if self.item.type == ClipType.TEXT:
            act_edit = QAction("修改", menu)
            act_edit.triggered.connect(
                lambda: self.edit_requested.emit(self.item.id))
            menu.addAction(act_edit)

        menu.addSeparator()
        act_del = QAction("删除", menu)
        act_del.triggered.connect(lambda: self.delete_requested.emit(self.item.id))
        menu.addAction(act_del)

        menu.exec(e.globalPos())


# ---------------------------------------------------------
# 窗口
# ---------------------------------------------------------
class HistoryWindow(QWidget):
    """
    独立的历史记录窗口。
    changed 信号：内容被复制/修改/删除后发出，供主窗口同步刷新。
    """

    changed = Signal()

    def __init__(self, storage: Storage, anchor: QWidget | None = None):
        super().__init__(None)          # 无父窗口 —— 独立顶层窗口
        self.storage = storage
        self._anchor = anchor
        self._centered = False

        self.setObjectName("HistoryWindow")
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(*DEFAULT_SIZE)
        self.setMinimumSize(*MIN_SIZE)

        self._build_ui()
        self._setup_shortcuts()
        self.refresh()

    # ---------------------------------------------------------
    # UI
    # ---------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(8)

        root.addLayout(self._build_top_bar())

        self.scroll = QScrollArea()
        self.scroll.setObjectName("HistoryScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.viewport().setAutoFillBackground(False)

        self.list_container = QWidget()
        self.list_container.setObjectName("HistoryList")
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(8)
        self.list_layout.addStretch(1)

        self.scroll.setWidget(self.list_container)
        root.addWidget(self.scroll, 1)

        self.empty_label = QLabel("没有记录")
        self.empty_label.setObjectName("HistoryEmpty")
        self.empty_label.setAlignment(Qt.AlignCenter)
        root.addWidget(self.empty_label)
        self.empty_label.hide()

    def _build_top_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setContentsMargins(2, 0, 2, 0)
        bar.setSpacing(8)

        title = QLabel("历史记录")
        title.setObjectName("HistoryTitle")
        bar.addWidget(title)

        self.count_label = QLabel("")
        self.count_label.setObjectName("HistoryCount")
        bar.addWidget(self.count_label)

        bar.addStretch(1)

        self.search = QLineEdit()
        self.search.setObjectName("HistorySearch")
        self.search.setPlaceholderText("搜索内容或文件名…")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(220)
        self.search.textChanged.connect(lambda _: self.refresh())
        bar.addWidget(self.search)

        btn_refresh = QPushButton("刷新")
        btn_refresh.setObjectName("HistoryToolButton")
        btn_refresh.setCursor(Qt.PointingHandCursor)
        btn_refresh.clicked.connect(self.refresh)
        bar.addWidget(btn_refresh)

        btn_clear = QPushButton("清空全部")
        btn_clear.setObjectName("HistoryToolButtonDanger")
        btn_clear.setCursor(Qt.PointingHandCursor)
        btn_clear.clicked.connect(self._on_clear_all)
        bar.addWidget(btn_clear)

        return bar

    def _setup_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+F"), self, self._focus_search)
        QShortcut(QKeySequence("Esc"), self, self.hide)
        QShortcut(QKeySequence("F5"), self, self.refresh)

    # ---------------------------------------------------------
    # 数据
    # ---------------------------------------------------------
    def refresh(self) -> None:
        """重新载入全部记录（按搜索框过滤）"""
        keyword = self.search.text().strip().lower()
        items = self.storage.list_all()
        if keyword:
            items = [it for it in items if self._match(it, keyword)]
        self._rebuild(items)

    @staticmethod
    def _match(item: ClipItem, keyword: str) -> bool:
        haystacks = [item.preview or "", clip_type_text(item.type)]
        # 文本正文可能落盘，只搜得到 DB 里的预览；文件记录则搜完整路径
        if item.type == ClipType.FILES:
            haystacks.extend(_item_paths(item))
        elif not item.file_path:
            haystacks.append(item.content or "")
        elif item.file_path:
            haystacks.append(os.path.basename(item.file_path))
        return any(keyword in h.lower() for h in haystacks)

    def _rebuild(self, items: list[ClipItem]) -> None:
        while self.list_layout.count() > 1:
            child = self.list_layout.takeAt(0)
            w = child.widget()
            if w:
                w.deleteLater()

        for item in items:
            card = HistoryCard(item)
            card.copy_requested.connect(self._on_copy)
            card.edit_requested.connect(self._on_edit)
            card.delete_requested.connect(self._on_delete)
            card.full_text_requested.connect(self._on_full_text)
            self.list_layout.insertWidget(self.list_layout.count() - 1, card)

        self.count_label.setText(f"共 {len(items)} 条")
        if items:
            self.empty_label.hide()
            self.scroll.show()
        else:
            self.scroll.hide()
            self.empty_label.setText(
                "没有匹配的记录" if self.search.text().strip() else "还没有任何记录")
            self.empty_label.show()

    # ---------------------------------------------------------
    # 操作
    # ---------------------------------------------------------
    def _on_copy(self, item_id: int) -> None:
        item = self.storage.get(item_id)
        if not item:
            return
        if not write_item_to_clipboard(item, self.storage):
            QMessageBox.warning(self, "失败", "这条记录的内容已经无法读取。")
            return
        self.storage.touch(item_id)     # 与主窗口一致：复制后置顶
        self.refresh()
        self.changed.emit()

    def _on_edit(self, item_id: int) -> None:
        full_text = self.storage.read_text(item_id)
        if full_text is None:
            QMessageBox.warning(self, "失败", "该记录不是文本，无法修改。")
            return

        new_text = EditTextDialog.get_text(full_text, self)
        if new_text is None:            # 取消
            return
        if not self.storage.update_text(item_id, new_text):
            QMessageBox.warning(self, "失败", "保存失败，该记录可能不是文本。")
            return
        self.refresh()
        self.changed.emit()

    def _on_delete(self, item_id: int) -> None:
        if not self.storage.delete(item_id):
            return
        self.refresh()
        self.changed.emit()

    def _on_clear_all(self) -> None:
        total = self.storage.count()
        if total <= 0:
            return
        box = QMessageBox(self)
        box.setWindowTitle("清空全部记录")
        box.setText(f"确定要删除全部 {total} 条记录吗？")
        box.setInformativeText("图片和落盘的文本文件也会一并删除，此操作不可撤销。")
        box.setIcon(QMessageBox.Warning)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        box.setWindowFlags(box.windowFlags() | Qt.WindowStaysOnTopHint)
        box.setWindowModality(Qt.WindowModal)
        box.adjustSize()
        center_on_screen(box, self)
        if box.exec() != QMessageBox.Yes:
            return
        self.storage.clear()
        self.refresh()
        self.changed.emit()

    def _on_full_text(self, item_id: int) -> None:
        item = self.storage.get(item_id)
        if not item:
            return

        text = item.content or ""
        if item.type == ClipType.TEXT:
            text = self.storage.read_text(item_id) or text
        elif item.type == ClipType.FILES:
            text = "\n".join(_item_paths(item))

        TextPreviewDialog.show_text(
            text, self, f"{DEFAULT_VIEW_TITLE}（{clip_type_text(item.type)}）")

    def _focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    # ---------------------------------------------------------
    # 窗口行为
    # ---------------------------------------------------------
    def showEvent(self, e) -> None:
        # 只在第一次显示时居中，之后保留用户拖动后的位置
        if not self._centered:
            self._centered = True
            center_on_screen(self, self._anchor)
        super().showEvent(e)

    def closeEvent(self, e) -> None:
        # 关闭只隐藏，托盘还能再打开（应用退出由托盘负责）
        e.ignore()
        self.hide()

    def popup(self) -> None:
        """从托盘打开：显示并置顶"""
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()
