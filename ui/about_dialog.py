# ui/about_dialog.py
"""
关于对话框。

内容全部来自 core/version.py（唯一版本号来源），
所以发版时只需要改 core/version.py 里的 __version__。
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
    QPushButton, QVBoxLayout, QWidget,
)

from core import applog
from core.paths import resource
from core.version import (
    APP_NAME, APP_NAME_EN, AUTHOR, AUTHOR_EMAIL, COPYRIGHT, DESCRIPTION,
    __version__, runtime_text, version_string,
)
from ui.utils import center_on_screen


ICON_PATH = resource("assets", "icon.ico")

# 仓库地址：没有正式仓库时留空，关于框会自动隐藏这一行
PROJECT_URL = ""

DIALOG_WIDTH = 420


class AboutDialog(QDialog):
    """关于：程序名、版本、数据目录、记录条数、运行方式"""

    def __init__(self, storage=None, parent: QWidget | None = None):
        super().__init__(parent)
        self.storage = storage

        # 标题就带上版本号：截图 / 任务栏看窗口标题也能认出是哪一版
        self.setWindowTitle(f"关于 {APP_NAME} {version_string()}")
        self.setModal(True)
        self.setMinimumWidth(DIALOG_WIDTH)

        self._build_ui()
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setWindowModality(Qt.WindowModal)
        self.adjustSize()
        center_on_screen(self, parent)

    # ---------------------------------------------------------
    # UI
    # ---------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(10)

        root.addLayout(self._build_header())
        root.addWidget(self._build_separator())
        root.addWidget(self._build_details())
        root.addStretch(1)
        root.addWidget(self._build_buttons())

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)

        icon_label = QLabel(self)
        icon_label.setObjectName("AboutIcon")
        icon_label.setFixedSize(48, 48)
        if os.path.exists(ICON_PATH):
            icon_label.setPixmap(QIcon(ICON_PATH).pixmap(48, 48))
        row.addWidget(icon_label, 0, Qt.AlignTop)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)

        name_label = QLabel(f"{APP_NAME}（{APP_NAME_EN}）", self)
        name_label.setObjectName("AboutTitle")
        text_col.addWidget(name_label)

        ver_label = QLabel(version_string(), self)
        ver_label.setObjectName("AboutVersion")
        text_col.addWidget(ver_label)

        desc_label = QLabel(DESCRIPTION, self)
        desc_label.setObjectName("AboutDesc")
        desc_label.setWordWrap(True)
        text_col.addWidget(desc_label)

        row.addLayout(text_col, 1)
        return row

    @staticmethod
    def _build_separator() -> QFrame:
        line = QFrame()
        line.setObjectName("AboutSeparator")
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Plain)
        return line

    def _build_details(self) -> QLabel:
        # 和 _info_lines() 共用同一份数据，界面上看到的和“复制版本信息”里的一致
        html_lines = []
        for line in self._info_lines():
            if line.startswith("项目主页："):
                url = line.split("：", 1)[1]
                html_lines.append(f'项目主页：<a href="{url}">{url}</a>')
            elif line.startswith("联系邮箱："):
                # 做成 mailto 链接：点一下就能发邮件反馈
                mail = line.split("：", 1)[1]
                html_lines.append(f'联系邮箱：<a href="mailto:{mail}">{mail}</a>')
            else:
                html_lines.append(line)

        label = QLabel("<br>".join(html_lines), self)
        label.setObjectName("AboutMeta")
        label.setTextFormat(Qt.RichText)
        label.setOpenExternalLinks(True)
        label.setWordWrap(True)
        label.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        return label

    def _build_buttons(self) -> QDialogButtonBox:
        box = QDialogButtonBox(self)

        btn_copy = QPushButton("复制版本信息", self)
        btn_copy.setObjectName("HistoryToolButton")
        btn_copy.setCursor(Qt.PointingHandCursor)
        btn_copy.clicked.connect(self._copy_info)

        btn_close = QPushButton("关闭", self)
        btn_close.setObjectName("HistoryToolButton")
        btn_close.setCursor(Qt.PointingHandCursor)

        box.addButton(btn_copy, QDialogButtonBox.ActionRole)
        box.addButton(btn_close, QDialogButtonBox.RejectRole)
        box.rejected.connect(self.reject)
        return box

    # ---------------------------------------------------------
    # 交互
    # ---------------------------------------------------------
    def _info_lines(self) -> list[str]:
        """关于框展示的所有信息行（界面和复制共用，免得两边说法不一致）"""
        lines = [
            f"版本号：{__version__}",
            f"运行方式：{runtime_text()}",
            f"作者：{AUTHOR}",
            f"联系邮箱：{AUTHOR_EMAIL}",
        ]

        if self.storage is not None:
            lines.append(f"数据目录：{self.storage.base_dir}")
            try:
                lines.append(f"已存记录：{self.storage.count()} 条")
            except Exception:               # 数据库异常不影响关于框显示
                pass

        if PROJECT_URL:
            lines.append(f"项目主页：{PROJECT_URL}")

        # 日志路径：出问题时用户直接把这份文件发来就能查（打包后没有控制台）
        lines.append(f"日志文件：{applog.log_file_path()}")

        lines.append(COPYRIGHT)
        return lines

    def info_text(self) -> str:
        """纯文本版版本信息，方便反馈问题时粘贴"""
        return "\n".join(
            [f"{APP_NAME} {version_string()}"] + self._info_lines())

    def _copy_info(self) -> None:
        QApplication.clipboard().setText(self.info_text())

    # ---------------------------------------------------------
    # 便捷入口
    # ---------------------------------------------------------
    @classmethod
    def show_about(cls, storage=None, parent: QWidget | None = None) -> None:
        cls(storage, parent).exec()
