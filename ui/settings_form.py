# ui/settings_form.py
"""
设置项表单（界面 + 取值），设置对话框和安装向导共用同一份定义。

为什么要抽出来：安装向导要「引导用户完成设置里的所有配置」，
如果和 ui/settings_dialog.py 各写一遍，新增一个设置项时极容易只改一处，
用户看到的就是「安装时能配，装完在设置里找不到」（或者反过来，默认值还不一致）。

这里只负责**界面与取值**，是否校验、存到哪里由调用方决定：
- 设置对话框：取值 -> Settings.save()；
- 安装向导  ：取值 -> 写进要生成的 settings.json。
"""
from __future__ import annotations

import os
from typing import Any, Mapping

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QFormLayout, QHBoxLayout, QKeySequenceEdit,
    QLineEdit, QPushButton, QSpinBox, QWidget,
)

from src.settings import DEFAULT_SETTINGS

# 本表单负责的配置键（与 src/settings.py 的 DEFAULT_SETTINGS 一一对应）
FORM_KEYS = (
    "storage_dir",
    "max_size_mb",
    "text_disk_threshold_kb",
    "max_items",
    "expand_count",
    "edge_strip_width",
    "global_hotkey",
    "next_item_hotkey",
    "exclude_password",
    "always_on_top",
    "auto_start",
)


class SettingsForm(QWidget):
    """所有可配置项的输入控件"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        # 存储路径的「自动跟随安装目录」状态：
        #   _storage_user_edited  本次会话里用户自己改过 -> 再也不自动动它
        #   _auto_storage         上一次自动算出来的值（用来判断现在这个值是不是自动生成的）
        self._storage_user_edited = False
        self._auto_storage = ""
        self._build_ui()

    # ---------------------------------------------------------
    # UI
    # ---------------------------------------------------------
    def _build_ui(self) -> None:
        form = QFormLayout(self)
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(8)
        self.form = form

        # ---- 存储路径 ----
        path_row = QHBoxLayout()
        self.edit_path = QLineEdit(self)
        self.edit_path.setPlaceholderText("默认 %APPDATA%\\ClipboardHistory")
        self.edit_path.setMinimumWidth(260)
        # textEdited 只在「用户手动敲」时触发，setText 不会：正好用来区分
        # 「用户自己填的路径」和「我们自动填的路径」
        self.edit_path.textEdited.connect(self._on_path_edited)
        btn_browse = QPushButton("浏览…", self)
        btn_browse.clicked.connect(self._browse_dir)
        path_row.addWidget(self.edit_path, 1)
        path_row.addWidget(btn_browse)
        form.addRow("存储路径：", path_row)

        # ---- 单条上限 ----
        self.spin_max_size = QSpinBox(self)
        self.spin_max_size.setRange(1, 2048)
        self.spin_max_size.setSuffix(" MB")
        form.addRow("单条大小上限：", self.spin_max_size)

        # ---- 文本落盘阈值 ----
        self.spin_text_threshold = QSpinBox(self)
        self.spin_text_threshold.setRange(1, 10240)
        self.spin_text_threshold.setSuffix(" KB")
        form.addRow("文本落盘阈值：", self.spin_text_threshold)

        # ---- 最大记录数 ----
        self.spin_max_items = QSpinBox(self)
        self.spin_max_items.setRange(10, 10000)
        form.addRow("最大记录数：", self.spin_max_items)

        # ---- 展开显示条数 ----
        self.spin_expand_count = QSpinBox(self)
        self.spin_expand_count.setRange(1, 100)
        form.addRow("展开显示条数：", self.spin_expand_count)

        # ---- 贴边细条宽度 ----
        self.spin_strip_width = QSpinBox(self)
        self.spin_strip_width.setRange(1, 20)
        self.spin_strip_width.setSuffix(" px")
        form.addRow("贴边细条宽度：", self.spin_strip_width)

        # ---- 唤出主窗口 ----
        self.edit_hotkey = QKeySequenceEdit(self)
        self.edit_hotkey.setClearButtonEnabled(True)
        form.addRow("唤醒主窗口：", self.edit_hotkey)

        # ---- 复制上一条的快捷键 ----
        self.edit_next_hotkey = QKeySequenceEdit(self)
        self.edit_next_hotkey.setClearButtonEnabled(True)
        form.addRow("复制上一条记录：", self.edit_next_hotkey)

        # ---- 开关 ----
        self.chk_exclude_pwd = QCheckBox("排除密码类内容（推荐）", self)
        # 说清判定口径：用户遇到「复制了却没记录」时，第一反应就是这里
        self.chk_exclude_pwd.setToolTip(
            "只拦两种：① 来源窗口标题像密码框/密码管理器；\n"
            "② 正文是「password: xxx」「密码=xxx」这类赋值，且值本身像密码。\n"
            "整篇文档里只是提到「密码」两个字，不会被拦。\n"
            "每一次没记录都会写进日志 app.log（含原因）。")
        form.addRow("", self.chk_exclude_pwd)

        self.chk_on_top = QCheckBox("窗口置顶", self)
        form.addRow("", self.chk_on_top)

        self.chk_auto_start = QCheckBox("开机自启", self)
        form.addRow("", self.chk_auto_start)

    def _browse_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self, "选择存储目录", self.edit_path.text() or "")
        if d:
            self._storage_user_edited = True
            self.edit_path.setText(d)

    def _on_path_edited(self, _text: str) -> None:
        self._storage_user_edited = True

    # ---------------------------------------------------------
    # 存储路径跟随安装目录（安装向导用）
    # ---------------------------------------------------------
    def set_path_placeholder(self, text: str) -> None:
        """存储路径输入框的灰字提示（向导与设置对话框说法不同）"""
        self.edit_path.setPlaceholderText(text)

    def path_follows_install_dir(self, install_dir: str) -> None:
        """
        安装目录变了 -> 存储路径跟着变成 <安装目录>\\data（没有就由安装流程创建）。

        只有在「这个值看起来是自动生成的」时候才改，下面三种情况一律不碰：
        1. 用户在本次会话里自己改过（敲过字或点过浏览）；
        2. 当前路径下已经有 history.db（那是真数据，改了等于让用户丢历史）；
        3. 当前路径不是以 data 结尾（是用户自定义的目录名）。
        """
        auto = self._data_dir_for(install_dir)
        current = self.edit_path.text().strip()
        if self._storage_user_edited:
            return
        if current and (self._looks_custom(current)):
            return
        self.edit_path.setText(auto)
        self._auto_storage = auto

    def _data_dir_for(self, install_dir: str) -> str:
        from src.installer import DATA_SUBDIR

        return os.path.join(str(install_dir).strip(), DATA_SUBDIR) if str(
            install_dir).strip() else ""

    def _looks_custom(self, path: str) -> bool:
        """这个路径像不像「用户自己指定的」"""
        from src.installer import DATA_SUBDIR

        norm = os.path.normpath(path)
        if os.path.basename(norm).lower() != DATA_SUBDIR:
            return True                                  # 目录名不是 data
        if os.path.isfile(os.path.join(norm, "history.db")):
            return True                                  # 那儿已经有数据了
        return False

    # ---------------------------------------------------------
    # 载入
    # ---------------------------------------------------------
    def load_dict(self, data: Mapping[str, Any]) -> None:
        """按配置字典填值（缺项用默认值，保证新增项也有合理默认）"""
        def value(key: str) -> Any:
            v = data.get(key, DEFAULT_SETTINGS.get(key))
            return DEFAULT_SETTINGS.get(key) if v is None else v

        # 载入的值不算「用户自己改的」：安装目录变了它还能跟着走（见
        # path_follows_install_dir 的三条保护规则）
        self._storage_user_edited = False
        self.edit_path.setText(str(value("storage_dir") or ""))
        self.spin_max_size.setValue(self._as_int(value("max_size_mb"), 100))
        self.spin_text_threshold.setValue(
            self._as_int(value("text_disk_threshold_kb"), 100))
        self.spin_max_items.setValue(self._as_int(value("max_items"), 300))
        self.spin_expand_count.setValue(self._as_int(value("expand_count"), 10))
        self.spin_strip_width.setValue(
            self._as_int(value("edge_strip_width"), 5))
        self.chk_exclude_pwd.setChecked(bool(value("exclude_password")))
        self.chk_on_top.setChecked(bool(value("always_on_top")))
        self.chk_auto_start.setChecked(bool(value("auto_start")))

        self.edit_hotkey.setKeySequence(
            self._as_sequence(value("global_hotkey")))
        self.edit_next_hotkey.setKeySequence(
            self._as_sequence(value("next_item_hotkey")))

    def load_settings(self, settings) -> None:
        """从 src.settings.Settings 载入"""
        self.load_dict(settings.as_dict())

    def reset_defaults(self) -> None:
        """恢复默认值（安装向导用：用户拿不准就点它）"""
        self.load_dict(DEFAULT_SETTINGS)

    @staticmethod
    def _as_int(value: Any, fallback: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _as_sequence(value: Any) -> QKeySequence:
        text = str(value or "").strip()
        return QKeySequence(text) if text else QKeySequence()

    # ---------------------------------------------------------
    # 取值 / 校验
    # ---------------------------------------------------------
    def values(self) -> dict[str, Any]:
        """表单里所有配置项（键名与 src/settings.py 一致）"""
        return {
            "storage_dir": self.edit_path.text().strip(),
            "max_size_mb": self.spin_max_size.value(),
            "text_disk_threshold_kb": self.spin_text_threshold.value(),
            "max_items": self.spin_max_items.value(),
            "expand_count": self.spin_expand_count.value(),
            "edge_strip_width": self.spin_strip_width.value(),
            "global_hotkey": self.edit_hotkey.keySequence().toString(),
            "next_item_hotkey": self.edit_next_hotkey.keySequence().toString(),
            "exclude_password": self.chk_exclude_pwd.isChecked(),
            "always_on_top": self.chk_on_top.isChecked(),
            "auto_start": self.chk_auto_start.isChecked(),
        }

    def storage_dir_input(self) -> str:
        return self.edit_path.text().strip()

    def validate(self) -> tuple[bool, str]:
        """
        校验并顺手把存储目录建出来。返回 (是否通过, 错误信息)。

        存储路径填了就试着创建：等到程序运行时才发现目录建不出来，
        用户是不知道去哪改的。
        """
        path = self.storage_dir_input()
        if not path:
            return True, ""
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as e:
            return False, f"无法创建目录：\n{e}"
        return True, ""
