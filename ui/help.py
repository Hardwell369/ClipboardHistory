# ui/help.py
"""
「帮助（使用手册）」入口。

正常情况：用系统默认程序（记事本）打开随程序发布的 docs/用户手册.txt。

兜底：手册文件缺失、或者系统不让起外部程序（安全软件拦截、关联被改坏）时，
**不能让用户点了没反应**——直接在程序里开一个只读窗口把手册显示出来，
并提供「复制全文」，同时把原因和手册该放的位置写在提示里。
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QLabel, QMessageBox,
    QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from core import applog
from core.manual import (
    MANUAL_FILE_NAME, MANUAL_SUBDIR, manual_path, open_with_editor,
    read_manual_text,
)
from ui.utils import center_on_screen

DIALOG_SIZE = (760, 560)


class ManualDialog(QDialog):
    """程序内查看手册（外部程序打不开时的兜底）"""

    def __init__(self, text: str, parent: QWidget | None = None,
                 hint: str = ""):
        super().__init__(parent)
        self.setWindowTitle("使用手册")
        self.setModal(True)
        self.resize(*DIALOG_SIZE)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        if hint:
            label = QLabel(hint, self)
            label.setObjectName("AboutDesc")
            label.setWordWrap(True)
            root.addWidget(label)

        self.editor = QPlainTextEdit(self)
        self.editor.setObjectName("TextEditor")
        self.editor.setPlainText(text)
        self.editor.setReadOnly(True)
        self.editor.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        root.addWidget(self.editor, 1)

        buttons = QDialogButtonBox(self)
        btn_copy = QPushButton("复制全文", self)
        btn_copy.setObjectName("HistoryToolButton")
        btn_copy.setCursor(Qt.PointingHandCursor)
        btn_copy.clicked.connect(
            lambda: QApplication.clipboard().setText(self.editor.toPlainText()))
        btn_close = QPushButton("关闭", self)
        btn_close.setObjectName("HistoryToolButton")
        btn_close.setCursor(Qt.PointingHandCursor)
        buttons.addButton(btn_copy, QDialogButtonBox.ActionRole)
        buttons.addButton(btn_close, QDialogButtonBox.RejectRole)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setWindowModality(Qt.WindowModal)
        center_on_screen(self, parent)


def open_manual(parent: QWidget | None = None) -> bool:
    """
    打开用户手册。返回是否给出了手册内容（外部打开成功或在程序内显示了）。
    """
    log = applog.get("manual")
    path = manual_path()
    reason = ""

    if os.path.isfile(path):
        ok, detail = open_with_editor(path)
        if ok:
            return True
        reason = detail
    else:
        reason = f"手册文件不存在：{path}"
        log.error("%s", reason)

    text = read_manual_text()
    if text:
        hint = (f"没能用系统程序打开手册（{reason}），已改为在程序内显示。\n"
                f"手册文件：{path}")
        ManualDialog(text, parent, hint).exec()
        return True

    _show_missing(path, reason, parent)
    return False


def _show_missing(path: str, reason: str, parent: QWidget | None) -> None:
    """手册既打不开也读不到：告诉用户文件该放哪，并给「复制路径」兜底"""
    box = QMessageBox(parent)
    box.setWindowTitle("使用手册")
    box.setIcon(QMessageBox.Warning)
    box.setText("没有找到用户手册。")
    box.setInformativeText(
        f"原因：{reason}\n\n"
        f"手册文件应该在：{path}\n"
        f"（也可以放在程序目录下的 {MANUAL_SUBDIR}\\{MANUAL_FILE_NAME}）\n\n"
        f"重装程序或在发布包里找一份 {MANUAL_FILE_NAME} 放到上述位置即可。"
    )
    btn_copy = box.addButton("复制路径", QMessageBox.ActionRole)
    box.addButton("关闭", QMessageBox.AcceptRole)
    box.setWindowFlags(box.windowFlags() | Qt.WindowStaysOnTopHint)
    box.setWindowModality(Qt.WindowModal)
    box.adjustSize()
    center_on_screen(box, parent)
    box.exec()
    if box.clickedButton() is btn_copy:
        QApplication.clipboard().setText(path)
