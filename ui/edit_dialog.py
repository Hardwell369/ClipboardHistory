# ui/edit_dialog.py
"""
文本对话框。

主窗口和历史窗口都要“看全文 / 改这一条文本”，原来各自写了一遍
QInputDialog / QPlainTextEdit 的搭建代码，这里收成一处。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QLabel, QPlainTextEdit,
    QPushButton, QVBoxLayout, QWidget,
)

from ui.utils import center_on_screen


DEFAULT_EDIT_TITLE = "修改内容"
DEFAULT_VIEW_TITLE = "完整内容"
_DIALOG_SIZE = (680, 460)


class _TextDialogBase(QDialog):
    """编辑框 + 底部按钮的公共部分"""

    def __init__(self, text: str, parent: QWidget | None = None,
                 title: str = "", hint: str = "", read_only: bool = False):
        super().__init__(parent)
        self.setWindowTitle(title or DEFAULT_VIEW_TITLE)
        self.setModal(True)
        self.resize(*_DIALOG_SIZE)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        if hint:
            root.addWidget(QLabel(hint, self))

        self.editor = QPlainTextEdit(self)
        self.editor.setObjectName("TextEditor")
        self.editor.setPlainText(text)
        self.editor.setReadOnly(read_only)
        self.editor.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        root.addWidget(self.editor, 1)

        self.buttons = QDialogButtonBox(self)
        root.addWidget(self.buttons)

        # 主窗口本身是置顶的，对话框不置顶就会被压在下面
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setWindowModality(Qt.WindowModal)

    def _finish(self, parent: QWidget | None) -> None:
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        center_on_screen(self, parent)
        self.editor.setFocus()

    def text(self) -> str:
        return self.editor.toPlainText()


class EditTextDialog(_TextDialogBase):
    """修改一段文本：确定返回新文本，取消返回 None"""

    def __init__(self, text: str, parent: QWidget | None = None,
                 title: str = DEFAULT_EDIT_TITLE, hint: str = "文本："):
        super().__init__(text, parent, title, hint, read_only=False)

        self.buttons.setStandardButtons(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("保存")
        self.buttons.button(QDialogButtonBox.Cancel).setText("取消")
        self._finish(parent)

    @classmethod
    def get_text(cls, text: str, parent: QWidget | None = None,
                 title: str = DEFAULT_EDIT_TITLE) -> str | None:
        """弹出对话框；点“保存”返回新文本，点“取消”/关闭返回 None"""
        dlg = cls(text, parent, title)
        if dlg.exec() != QDialog.Accepted:
            return None
        return dlg.text()


class TextPreviewDialog(_TextDialogBase):
    """只读查看完整内容，附带“复制全文”"""

    def __init__(self, text: str, parent: QWidget | None = None,
                 title: str = DEFAULT_VIEW_TITLE):
        super().__init__(text, parent, title, hint="", read_only=True)

        btn_copy = QPushButton("复制全文", self)
        btn_copy.setObjectName("HistoryToolButton")
        btn_copy.setCursor(Qt.PointingHandCursor)
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(text))
        btn_close = QPushButton("关闭", self)
        btn_close.setObjectName("HistoryToolButton")
        btn_close.setCursor(Qt.PointingHandCursor)

        self.buttons.addButton(btn_copy, QDialogButtonBox.ActionRole)
        self.buttons.addButton(btn_close, QDialogButtonBox.RejectRole)
        self._finish(parent)

    @classmethod
    def show_text(cls, text: str, parent: QWidget | None = None,
                  title: str = DEFAULT_VIEW_TITLE) -> None:
        cls(text, parent, title).exec()
