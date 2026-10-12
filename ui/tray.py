# ui/tray.py
from __future__ import annotations

import os

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QIcon, QAction
from PySide6.QtWidgets import QSystemTrayIcon, QMenu

from core import applog
from core.paths import resource
from core.version import APP_NAME, version_string


ICON_PATH = resource("assets", "icon.ico")

# 托盘图标重试：壳（explorer）刚起来时 Shell_NotifyIcon 可能失败，Qt 不会自己重试
SHOW_RETRY_ATTEMPTS = 3
SHOW_RETRY_INTERVAL_MS = 1500


class Tray(QObject):
    """
    系统托盘。
    - 左键单击 -> 唤出主窗口
    - 右键菜单 -> 显示/隐藏、全部历史、暂停监听、设置、
                  打开存储文件夹、重启、退出
      （主窗口里右键弹出的就是同一份菜单，见 build_menu）
    """

    show_window = Signal()
    hide_window = Signal()
    open_history = Signal()
    open_storage = Signal()
    restart_app = Signal()
    toggle_pause = Signal(bool)     # True = 暂停
    open_settings = Signal()
    open_help = Signal()
    about_app = Signal()
    quit_app = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._paused = False

        icon = QIcon(ICON_PATH) if os.path.exists(ICON_PATH) else QIcon()
        self.tray = QSystemTrayIcon(icon)
        # 提示里带上版本号：鼠标移上去就能确认跑的是哪一版
        self.tray.setToolTip(f"{APP_NAME} {version_string()}")

        self._menu: QMenu | None = None
        self._retry_left = 0
        self._retry_timer = QTimer(self)
        self._retry_timer.setInterval(SHOW_RETRY_INTERVAL_MS)
        self._retry_timer.timeout.connect(self._retry_show)

        self._build_menu()
        self.tray.activated.connect(self._on_activated)

    # ---------------------------------------------------------
    # 菜单
    # ---------------------------------------------------------
    def _build_menu(self) -> None:
        # 必须自己留一份引用：setContextMenu() 不接管 QMenu 的所有权，
        # 局部变量一被回收，托盘右键菜单就会跟着 C++ 对象一起消失
        # （表现就是「右键托盘没反应」）。
        self._menu = self.build_menu()
        self.tray.setContextMenu(self._menu)

    def build_menu(self, parent=None, in_window: bool = False) -> QMenu:
        """
        构建右键菜单。托盘和主窗口共用这一份定义，免得两边慢慢长歪。

        in_window=True 用于在“主窗口里右键”：此时窗口本来就在眼前，
        第一项由「显示窗口」换成「隐藏窗口」。
        """
        menu = QMenu(parent)

        if in_window:
            act_window = QAction("隐藏窗口", menu)
            act_window.triggered.connect(self.hide_window.emit)
        else:
            act_window = QAction("显示窗口", menu)
            act_window.triggered.connect(self.show_window.emit)
        menu.addAction(act_window)

        act_history = QAction("查看全部历史…", menu)
        act_history.triggered.connect(self.open_history.emit)
        menu.addAction(act_history)

        act_pause = QAction("恢复监听" if self._paused else "暂停监听", menu)
        act_pause.setCheckable(True)
        act_pause.setChecked(self._paused)      # 先定状态再连信号，免得构建时就触发一次
        act_pause.toggled.connect(self._on_pause_toggled)
        menu.addAction(act_pause)
        if not in_window:
            # 托盘那份菜单是常驻的，留个引用用于同步勾选状态
            self.act_pause = act_pause

        menu.addSeparator()

        act_settings = QAction("设置…", menu)
        act_settings.triggered.connect(self.open_settings.emit)
        menu.addAction(act_settings)

        act_storage = QAction("打开存储文件夹", menu)
        act_storage.triggered.connect(self.open_storage.emit)
        menu.addAction(act_storage)

        # 帮助排在「关于」上面一条
        act_help = QAction("帮助（使用手册）…", menu)
        act_help.triggered.connect(self.open_help.emit)
        menu.addAction(act_help)

        act_about = QAction(f"关于 {APP_NAME} ({version_string()})…", menu)
        act_about.triggered.connect(self.about_app.emit)
        menu.addAction(act_about)

        menu.addSeparator()

        act_restart = QAction("重启", menu)
        act_restart.triggered.connect(self.restart_app.emit)
        menu.addAction(act_restart)

        act_quit = QAction("退出", menu)
        act_quit.triggered.connect(self.quit_app.emit)
        menu.addAction(act_quit)

        return menu

    # ---------------------------------------------------------
    # 事件
    # ---------------------------------------------------------
    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:      # 左键单击
            self.show_window.emit()
        elif reason == QSystemTrayIcon.DoubleClick:
            self.show_window.emit()

    def _on_pause_toggled(self, checked: bool) -> None:
        self._paused = checked
        act = getattr(self, "act_pause", None)
        if act is not None:
            try:
                act.setText("恢复监听" if checked else "暂停监听")
                # 从主窗口右键菜单里切换时，托盘那份菜单也要跟着变
                if act.isChecked() != checked:
                    act.blockSignals(True)
                    act.setChecked(checked)
                    act.blockSignals(False)
            except RuntimeError:
                # 菜单被重建/回收过，这里的 QAction 可能已经没了：
                # 状态以 self._paused 为准，下一份菜单会按它重建
                applog.get("tray").warning("暂停菜单项已失效，忽略这次同步")
        self.toggle_pause.emit(checked)

    # ---------------------------------------------------------
    # 外部接口
    # ---------------------------------------------------------
    def is_available(self) -> bool:
        """系统是否支持托盘（explorer 没起来 / 被策略禁用时为 False）"""
        return QSystemTrayIcon.isSystemTrayAvailable()

    def is_shown(self) -> bool:
        return self.tray.isVisible()

    def icon_is_null(self) -> bool:
        return self.tray.icon().isNull()

    def ensure_shown(self, attempts: int = SHOW_RETRY_ATTEMPTS) -> None:
        """
        把托盘图标挂上，并在失败时自己重试几次。

        为什么要重试：Shell_NotifyIcon 在壳还没准备好时会失败，而 Qt **不会**
        自动重试，也不报错——现象就是「程序在跑，但托盘里没有图标」。
        hide()+show() 会重新走一次 NIM_DELETE/NIM_ADD，是最省事的重试方式。
        """
        log = applog.get("tray")
        if not self.is_available():
            log.warning("系统托盘不可用（isSystemTrayAvailable=False），图标不会显示")
            return
        if self.icon_is_null():
            log.warning("托盘图标为空（icon.isNull()），Windows 会显示成空白槽位")

        self.tray.show()
        log.info("托盘图标已请求显示：available=%s visible=%s iconNull=%s icon=%s",
                 self.is_available(), self.is_shown(), self.icon_is_null(), ICON_PATH)

        self._retry_left = max(0, int(attempts) - 1)
        if self._retry_left > 0:
            self._retry_timer.start()

    def _retry_show(self) -> None:
        """兜底重试：重新注册一次托盘图标（壳刚重启时最常见）"""
        log = applog.get("tray")
        self._retry_left -= 1
        if self.is_available():
            try:
                self.tray.hide()
                self.tray.show()
                log.info("重新注册托盘图标：visible=%s（剩余重试 %d）",
                         self.is_shown(), max(0, self._retry_left))
            except Exception as e:              # noqa: BLE001 - 重试失败不致命
                log.warning("重新注册托盘图标失败：%s", e)
        else:
            log.warning("系统托盘仍不可用，放弃重试")
            self._retry_left = 0
        if self._retry_left <= 0:
            self._retry_timer.stop()

    def show(self) -> None:
        self.tray.show()

    def hide(self) -> None:
        self.tray.hide()

    def show_message(self, title: str, msg: str) -> None:
        """
        气泡通知。

        注意两点：
        1. 气泡是挂在托盘图标上的，托盘图标没显示出来时气泡**也看不见**，
           所以这里一律写日志，真正的错误必须由调用方用对话框兜底；
        2. 气泡是发给壳（explorer）的，壳被安全软件拖住时这个调用会**阻塞主线程**
           （实测：受限环境里点「打开存储文件夹」后主线程卡了 20 多秒，
           连退出都要等它）。所以延到事件循环里再发，绝不挡住调用方。
        """
        log = applog.get("tray")
        log.info("通知：%s | %s", title, msg)
        if not self.is_available():
            return

        def _pop() -> None:
            try:
                self.tray.showMessage(title, msg, QSystemTrayIcon.Information, 2000)
                log.info("气泡已发送：%s", title)
            except Exception as e:              # noqa: BLE001 - 气泡失败不影响主流程
                log.warning("气泡发送失败：%s（%s）", title, e)

        QTimer.singleShot(0, _pop)

    def is_paused(self) -> bool:
        return self._paused

    def set_paused(self, paused: bool) -> None:
        act = getattr(self, "act_pause", None)
        if act is None:
            return
        try:
            act.setChecked(paused)
        except RuntimeError:
            applog.get("tray").warning("暂停菜单项已失效，忽略 set_paused")