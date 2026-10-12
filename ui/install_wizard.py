# ui/install_wizard.py
"""
安装向导界面（QWizard）。

四页：
1. 欢迎       —— 说明装到哪、权限情况、是否覆盖安装
2. 安装位置   —— 目标目录 + 快捷方式 / 装完是否运行
3. 设置       —— 程序「设置」里的全部选项（预填默认值，可一键恢复默认）
4. 安装       —— 进度、结果、打开安装目录

真正的安装动作都在 src/installer.py 里（不依赖 Qt，可单独测试），
这里只负责收集选项、显示进度、把失败原因原样摆给用户看。
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout,
    QWizard, QWizardPage,
)

from src import applog
from src.installer import (
    DATA_SUBDIR, InstallOptions, data_dir_for_install, default_install_dir,
    existing_install, install, is_installed, launch_installed, payload_dir,
)
from src.paths import resource
from src.settings import DEFAULT_SETTINGS, Settings, default_settings_path
from src.version import APP_NAME, AUTHOR, AUTHOR_EMAIL, __version__
from ui.settings_form import SettingsForm

PAGE_WELCOME = 0
PAGE_OPTIONS = 1
PAGE_SETTINGS = 2
PAGE_PROGRESS = 3

WIZARD_SIZE = (760, 580)
ICON_PATH = resource("assets", "icon.ico")


# ---------------------------------------------------------
# 第 1 页：欢迎
# ---------------------------------------------------------
class WelcomePage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle(f"欢迎使用 {APP_NAME} 安装向导")
        self.setSubTitle("向导会先把程序里的所有可配置项过一遍（每项都填好了默认值），"
                         "然后开始安装。")

        self.info = QLabel(self)
        self.info.setObjectName("AboutMeta")
        self.info.setWordWrap(True)
        self.info.setTextFormat(Qt.PlainText)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.addWidget(self.info)
        layout.addStretch(1)

    def initializePage(self) -> None:
        self.info.setText(self._describe())

    @staticmethod
    def _describe() -> str:
        from src.autostart import is_elevated

        lines = [
            f"版本：{__version__}        作者：{AUTHOR}",
            f"联系邮箱：{AUTHOR_EMAIL}",
            "",
            f"计划安装到：{default_install_dir()}",
        ]

        if is_elevated():
            lines.append("权限：已获得管理员权限，可以安装到 Program Files、"
                         "创建快捷方式并注册卸载信息。")
        else:
            lines.append("权限：当前没有管理员权限，将安装到当前用户目录"
                         "（%LOCALAPPDATA%\\Programs）。\n"
                         "想装到 Program Files 的话，请关掉向导，"
                         "右键安装程序选择「以管理员身份运行」。")

        if is_installed():
            info = existing_install()
            lines.append("")
            lines.append("检测到已经安装过本程序：")
            lines.append(f"    版本：{info.get('version') or '未知'}")
            lines.append(f"    位置：{info.get('install_dir') or '未知'}")
            lines.append("继续安装会覆盖程序文件；配置和历史记录都会保留。")

        lines.append("")
        lines.append("提示：如果程序正在运行，请先从托盘右键退出，"
                     "否则程序文件被占用，覆盖安装会失败。")
        return "\n".join(lines)


# ---------------------------------------------------------
# 第 2 页：安装位置与选项
# ---------------------------------------------------------
class OptionsPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("安装位置与选项")
        self.setSubTitle("选择安装目录，以及要创建哪些快捷方式。")

        self.edit_dir = QLineEdit(self)
        self.edit_dir.setText(default_install_dir())
        self.edit_dir.textChanged.connect(lambda _: self.completeChanged.emit())

        btn_browse = QPushButton("浏览…", self)
        btn_browse.clicked.connect(self._browse)

        row = QHBoxLayout()
        row.addWidget(QLabel("安装目录：", self))
        row.addWidget(self.edit_dir, 1)
        row.addWidget(btn_browse)

        self.chk_desktop = QCheckBox("创建桌面快捷方式", self)
        self.chk_desktop.setChecked(True)
        self.chk_start_menu = QCheckBox("创建开始菜单快捷方式", self)
        self.chk_start_menu.setChecked(True)
        self.chk_launch = QCheckBox("安装完成后立即运行程序", self)
        self.chk_launch.setChecked(True)

        note = QLabel(f"历史记录（数据）默认保存到「安装目录\\{DATA_SUBDIR}」，"
                      f"安装时自动创建；想放到别的地方，在下一步「设置」里改。", self)
        note.setObjectName("AboutDesc")
        note.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)
        layout.addLayout(row)
        layout.addWidget(self.chk_desktop)
        layout.addWidget(self.chk_start_menu)
        layout.addWidget(self.chk_launch)
        layout.addSpacing(6)
        layout.addWidget(note)
        layout.addStretch(1)

    # ---- 取值 ----
    def target_dir(self) -> str:
        return self.edit_dir.text().strip()

    def desktop_shortcut(self) -> bool:
        return self.chk_desktop.isChecked()

    def start_menu_shortcut(self) -> bool:
        return self.chk_start_menu.isChecked()

    def launch_after(self) -> bool:
        return self.chk_launch.isChecked()

    # ---- 交互 ----
    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择安装目录", self.target_dir())
        if d:
            self.edit_dir.setText(d)

    def isComplete(self) -> bool:                # noqa: N802
        return bool(self.target_dir())

    def validatePage(self) -> bool:              # noqa: N802
        path = self.target_dir()
        if not path:
            QMessageBox.warning(self, "安装位置", "请先选择安装目录。")
            return False
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(
                self, "安装位置",
                f"这个目录没法使用：\n{path}\n\n原因：{e}\n\n"
                f"换一个目录，或者以管理员身份重新运行安装程序。")
            return False
        return True


# ---------------------------------------------------------
# 第 3 页：设置（程序里的全部配置项）
# ---------------------------------------------------------
class SettingsPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("设置")
        self.setSubTitle("下面就是程序「设置」里的全部选项，已经填好了默认值；"
                         "不想改就直接下一步。")
        self._loaded = False

        self.form_widget = SettingsForm(self)
        self.form_widget.set_path_placeholder(f"默认 安装目录\\{DATA_SUBDIR}")

        btn_reset = QPushButton("恢复默认值", self)
        btn_reset.setObjectName("HistoryToolButton")
        btn_reset.setCursor(Qt.PointingHandCursor)
        btn_reset.clicked.connect(self._reset_defaults)

        hint = QLabel(f"提示：每一项的默认值都适合大多数人。存储路径默认是"
                      f"「安装目录\\{DATA_SUBDIR}」（跟着上一步选的安装目录走，"
                      f"没有会自动创建），留空则用 %APPDATA%\\ClipboardHistory。"
                      f"「开机自启」会创建一个登录时以最高权限运行的计划任务"
                      f"（原因见用户手册「开机自启与管理员权限」一节）。", self)
        hint.setObjectName("AboutDesc")
        hint.setWordWrap(True)

        bottom = QHBoxLayout()
        bottom.addWidget(btn_reset)
        bottom.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.form_widget)
        layout.addLayout(bottom)
        layout.addWidget(hint)
        layout.addStretch(1)

    def initializePage(self) -> None:            # noqa: N802
        # 只在第一次进入时填值：来回翻页不能把用户刚改的东西冲掉
        if not self._loaded:
            self._loaded = True
            path = default_settings_path()
            data = dict(DEFAULT_SETTINGS)
            if os.path.isfile(path):
                try:
                    data.update(Settings(path).as_dict())
                except Exception:               # noqa: BLE001 - 老配置坏了就用默认值
                    pass
            self.form_widget.load_dict(data)
        # 存储路径跟着第 2 页选的安装目录走（用户自己填过的、或那儿已经有数据的，
        # SettingsForm 会自己跳过，不会覆盖）
        self.follow_install_dir(self._install_dir())

    def _install_dir(self) -> str:
        wizard = self.wizard()
        if wizard is not None and hasattr(wizard, "options_page"):
            return wizard.options_page.target_dir()
        return default_install_dir()

    def follow_install_dir(self, install_dir: str) -> None:
        """安装目录变了：让存储路径跟着变（没有就等安装时创建）"""
        self.form_widget.path_follows_install_dir(install_dir)

    def _reset_defaults(self) -> None:
        """恢复默认值：存储路径仍然跟着安装目录（那是「默认」的一部分）"""
        self.form_widget.reset_defaults()
        self.follow_install_dir(self._install_dir())

    def storage_dir(self) -> str:
        """当前实际会用的数据目录（存储路径留空时 = 安装目录\\data）"""
        path = self.form_widget.storage_dir_input()
        return path or data_dir_for_install(self._install_dir())

    def values(self) -> dict:
        values = self.form_widget.values()
        # 存储路径留空也要给一个明确的值：<安装目录>\data（安装流程会创建它）
        values["storage_dir"] = self.storage_dir()
        return values

    def validatePage(self) -> bool:              # noqa: N802
        ok, error = self.form_widget.validate()
        if not ok:
            QMessageBox.warning(self, "存储路径无效", error)
            return False
        # 再校验一次「实际会用的数据目录」：留空时它是 安装目录\data，
        # 装到这才发现建不出来（权限/只读盘）就太晚了
        target = self.storage_dir()
        try:
            os.makedirs(target, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(
                self, "数据目录无效",
                f"没法创建数据目录：\n{target}\n\n原因：{e}\n\n"
                f"回上一步换个安装目录，或在这里自己指定一个能写的存储路径。")
            return False
        return True


# ---------------------------------------------------------
# 第 4 页：安装进度与结果
# ---------------------------------------------------------
class ProgressPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("正在安装")
        self.setSubTitle("正在复制程序文件并完成配置，请稍候。")
        self._done = False
        self._started = False
        self._result = None

        self.bar = QProgressBar(self)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)

        self.status = QLabel("准备开始…", self)
        self.status.setObjectName("AboutDesc")

        self.detail = QPlainTextEdit(self)
        self.detail.setObjectName("TextEditor")
        self.detail.setReadOnly(True)
        self.detail.setMinimumHeight(150)

        self.btn_open = QPushButton("打开安装目录", self)
        self.btn_open.setObjectName("HistoryToolButton")
        self.btn_open.setCursor(Qt.PointingHandCursor)
        self.btn_open.setEnabled(False)
        self.btn_open.clicked.connect(self._open_dir)

        row = QHBoxLayout()
        row.addWidget(self.btn_open)
        row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.bar)
        layout.addWidget(self.status)
        layout.addWidget(self.detail, 1)
        layout.addLayout(row)

    # ---- 流程 ----
    def initializePage(self) -> None:            # noqa: N802
        if self._started:
            return
        self._started = True
        QTimer.singleShot(80, self._run)         # 先把这一页画出来再干活

    def isComplete(self) -> bool:                # noqa: N802
        return self._done

    def _on_progress(self, percent: int, message: str) -> None:
        self.bar.setValue(percent)
        self.status.setText(message)
        # 安装是在主线程里跑的：不主动刷一下界面，进度条会一直定在 0
        QApplication.processEvents()

    def _run(self) -> None:
        wizard = self.wizard()
        cancel = wizard.button(QWizard.CancelButton)
        if cancel is not None:
            cancel.setEnabled(False)
        try:
            options = wizard.build_options()
            self._on_progress(1, "开始安装…")
            result = install(options, progress=self._on_progress)
            self._result = result
            self.detail.setPlainText(result.summary())
            if result.ok:
                self.bar.setValue(100)
                self.status.setText("安装完成。")
                self.setTitle("安装完成")
                self.setSubTitle(f"{APP_NAME} 已经安装好了。")
                self.btn_open.setEnabled(True)
                wizard.set_launch_target(result.exe_path, result.target_dir,
                                         options.launch_after)
            else:
                self.status.setText("安装失败，请看下面的原因。")
                self.setTitle("安装失败")
                self.setSubTitle("程序文件没能装好，原因见下方；"
                                 "详细信息也记录在日志里。")
                QMessageBox.warning(
                    self, "安装失败",
                    "安装没能完成：\n\n" + "\n".join(result.errors[:4]) +
                    "\n\n日志：" + applog.log_file_path())
        finally:
            self._done = True
            self.completeChanged.emit()
            if cancel is not None:
                cancel.setEnabled(True)

    def _open_dir(self) -> None:
        if self._result is None:
            return
        path = self._result.target_dir
        try:
            if QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
                return
        except Exception:                        # noqa: BLE001
            pass
        try:
            os.startfile(path)                   # type: ignore[attr-defined]
        except OSError as e:
            QMessageBox.warning(self, "打开失败",
                               f"没能打开目录：\n{path}\n\n原因：{e}")


# ---------------------------------------------------------
# 向导
# ---------------------------------------------------------
class InstallWizard(QWizard):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"安装 {APP_NAME} {__version__}")
        self.setWizardStyle(QWizard.ModernStyle)
        self.setOption(QWizard.NoBackButtonOnStartPage, True)
        self.setOption(QWizard.NoBackButtonOnLastPage, True)
        self.setOption(QWizard.HaveHelpButton, False)
        self.setOption(QWizard.HaveFinishButtonOnEarlyPages, False)
        self.setButtonText(QWizard.NextButton, "下一步")
        self.setButtonText(QWizard.BackButton, "上一步")
        self.setButtonText(QWizard.CancelButton, "取消")
        self.setButtonText(QWizard.FinishButton, "完成")

        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))
            self.setPixmap(QWizard.LogoPixmap,
                           QIcon(ICON_PATH).pixmap(48, 48))

        self.options_page = OptionsPage(self)
        self.settings_page = SettingsPage(self)
        self.setPage(PAGE_WELCOME, WelcomePage(self))
        self.setPage(PAGE_OPTIONS, self.options_page)
        self.setPage(PAGE_SETTINGS, self.settings_page)
        self.setPage(PAGE_PROGRESS, ProgressPage(self))

        # 第 2 页改了安装目录 -> 第 3 页的存储路径跟着变成 <安装目录>\data
        self.options_page.edit_dir.textChanged.connect(
            self._on_install_dir_changed)

        self.resize(*WIZARD_SIZE)
        self.setMinimumSize(*WIZARD_SIZE)

        self._launch_target = ""
        self._launch_workdir = ""
        self._launch_wanted = False

    # ---- 选项 ----
    def _on_install_dir_changed(self, text: str) -> None:
        """安装目录变了：存储路径跟着走（用户自己填过的不会被覆盖）"""
        self.settings_page.follow_install_dir(text.strip())

    def build_options(self) -> InstallOptions:
        settings = self.settings_page.values()
        options = InstallOptions(
            target_dir=self.options_page.target_dir(),
            settings=settings,
            desktop_shortcut=self.options_page.desktop_shortcut(),
            start_menu_shortcut=self.options_page.start_menu_shortcut(),
            # 开机自启只有「设置」页里这一处（避免两个地方说法不一致）
            auto_start=bool(settings.get("auto_start", False)),
            launch_after=self.options_page.launch_after(),
            payload_dir=payload_dir(),
        )
        applog.get("installer").info(
            "向导收集完成：目标=%s 桌面=%s 开始菜单=%s 自启=%s 装完运行=%s",
            options.target_dir, options.desktop_shortcut,
            options.start_menu_shortcut, options.auto_start,
            options.launch_after)
        return options

    def set_launch_target(self, exe_path: str, workdir: str,
                          wanted: bool) -> None:
        self._launch_target = exe_path
        self._launch_workdir = workdir
        self._launch_wanted = wanted

    # ---- 点「完成」后启动程序 ----
    def accept(self) -> None:                    # noqa: D102 - 覆盖 QWizard
        if self._launch_wanted and self._launch_target:
            ok, detail = launch_installed(self._launch_target,
                                         self._launch_workdir)
            if not ok:
                QMessageBox.warning(self, "启动失败",
                                   f"没能启动程序：\n{self._launch_target}\n\n"
                                   f"原因：{detail}")
        super().accept()
