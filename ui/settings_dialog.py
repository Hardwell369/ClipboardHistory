# ui/settings_dialog.py
"""
设置对话框。

控件与取值逻辑都在 ui/settings_form.py 里（安装向导用的是同一份定义），
这里只负责：载入 -> 校验 -> 保存 -> 报告错误。
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QMessageBox, QVBoxLayout,
)

from core.settings import Settings
from ui.settings_form import SettingsForm
from ui.utils import center_on_screen


class SettingsDialog(QDialog):
    """设置对话框"""

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings

        self.setWindowTitle("设置")
        self.setModal(True)
        self.setMinimumWidth(420)

        self._build_ui()
        self._load_values()
        self.adjustSize()
        center_on_screen(self, parent)

    # ---------------------------------------------------------
    # UI
    # ---------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self.form = SettingsForm(self)
        root.addWidget(self.form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # ---------------------------------------------------------
    # 载入 / 保存
    # ---------------------------------------------------------
    def _load_values(self) -> None:
        self.form.load_settings(self.settings)

    def _on_accept(self) -> None:
        ok, error = self.form.validate()
        if not ok:
            QMessageBox.warning(self, "路径无效", error)
            return

        self.settings.update(self.form.values())
        self.settings.save()
        self.accept()
