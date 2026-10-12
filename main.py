# main.py
from __future__ import annotations

import os
import ctypes
import subprocess
import sys
import threading
import time

from PySide6.QtCore import Qt, QObject, QPoint, QProcess, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from core import applog
from core.settings import Settings, default_settings_path
from core.single_instance import SingleInstance, notify_existing_instance
from core.storage import Storage, StorageLockedError
from core import capture as capture_mod
from core.capture import capture_once
from core.clipboard_listener import ClipboardListener
from core.hotkey import HotkeyManager
from core.paths import app_root, resource
from core.version import APP_NAME, APP_NAME_EN, ORG_NAME, __version__

from ui.about_dialog import AboutDialog
from ui.main_window import MainWindow, EDGE_SNAP_THRESHOLD
from ui.history_window import HistoryWindow
from ui.settings_dialog import SettingsDialog
from ui.tray import Tray
from ui.utils import center_on_screen, screen_for_widget


ICON_PATH = resource("assets", "icon.ico")
QSS_PATH = resource("ui", "style.qss")
APP_DIR = app_root()
RESTART_EXIT_WAIT_S = 0.4   # 退出前等这么久，确保文件句柄（单实例锁）真的被系统收掉
SINGLE_INSTANCE_WAIT_S = 6.0    # 启动时最多等这么久，用来跨过「重启」的交接窗口
EXPLORER_PROBE_S = 2.0      # 起 explorer.exe 后观察这么久，判断它是真起来了还是死在加载器
QUIT_WATCHDOG_S = 4.0       # 退出看门狗：释放资源超过这么久就直接结束进程
RESTART_WATCHDOG_S = 6.0    # 重启看门狗（比退出宽一点：还要留出拉起新进程的时间）
EMPTY_RETRY_DELAYS_MS = (100, 250, 500, 1000, 2000)   # 剪贴板读空后的补读时刻（累计约 3.9 秒）

# 让新进程脱离本进程（重启用）：PyInstaller 打包后父子进程一起被结束会很尴尬
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


class AppController(QObject):
    """把监听、存储、界面、托盘串起来"""

    def __init__(self, app: QApplication, guard: SingleInstance | None = None):
        super().__init__()
        self.app = app
        self.guard = guard
        self.app.setQuitOnLastWindowClosed(False)
        self._restarting = False
        self._spawned = False           # 重启时新进程是否已经拉起来（看门狗用）
        self._empty_retries = 0         # 剪贴板读空后的补读次数

        # ---- 配置 ----
        self.settings = Settings(default_settings_path())

        # ---- 存储 ----
        # lock=True：同一个存储目录只允许一个进程写（单实例保护的第二道防线）
        self.storage = Storage(
            base_dir=self.settings.resolve_storage_dir(),
            max_items=int(self.settings.get("max_items", 300)),
            max_size=self.settings.max_size_bytes,
            text_disk_threshold=self.settings.text_disk_threshold_bytes,
        )

        # ---- 主窗口 ----
        self.window = MainWindow(self.storage, self.settings.as_dict())
        self._apply_window_flags()
        self._restore_window_geometry()

        # ---- 全部历史窗口（独立窗口，按需显示） ----
        self.history = HistoryWindow(self.storage, self.window)
        self.history.changed.connect(self.window.refresh)

        # ---- 托盘 ----
        self.tray = Tray()
        # ensure_shown() 内部会检查“托盘是否可用”并重试注册：
        # 直接 show() 在壳没准备好时会静默失败，用户看到的就是“托盘里没有图标”
        self.tray.ensure_shown()
        self.tray.show_window.connect(self._show_window)
        self.tray.open_history.connect(self._open_history)
        self.tray.open_storage.connect(self._open_storage_dir)
        self.tray.restart_app.connect(self._restart)
        self.tray.toggle_pause.connect(self._on_pause_toggled)
        self.tray.open_settings.connect(self._open_settings)
        self.tray.open_help.connect(self._show_help)
        self.tray.about_app.connect(self._show_about)
        self.tray.quit_app.connect(self._quit)

        # 主窗口空白处右键 -> 与托盘一致的菜单
        self.window.context_menu_requested.connect(self._show_window_menu)
        self.tray.hide_window.connect(self.window.hide)
        # 点「−」不是真最小化，而是收进托盘（真最小化会被 Windows 缩到屏幕
        # 左下角留一个小窗），这里给用户一句反馈，免得以为程序关了
        self.window.minimized_to_tray.connect(self._on_minimized_to_tray)

        # ---- 监听 ----
        self.listener = ClipboardListener()
        self.listener.changed.connect(self._on_clipboard_changed)

        # ---- 全局快捷键 ----
        self.hotkey = HotkeyManager(self)
        self.hotkey.triggered.connect(self._on_hotkey)
        self._apply_hotkeys()

        # ---- 启动 ----
        self.window.show()
        self.listener.start()

        # ---- 开机自启 ----
        self._apply_auto_start()

        # ---- 单实例：别人又启动了一次 -> 亮出窗口 ----
        # 放在最后连：界面都建好了，这时候被叫醒才有窗口可以显示
        if self.guard is not None:
            self.guard.activated.connect(self._show_window)
            # 卸载器（uninstaller.exe）卸载前会请我们优雅退出：
            # 走的就是托盘「退出」同一条流程，配置照样写盘、数据库照样关闭
            self.guard.quit_requested.connect(self._quit)

        # 启动自述：出问题时这份日志就是唯一的线索（打包后没有控制台）
        applog.get("app").info(
            "启动完成：frozen=%s 运行方式=%s 资源目录=%s 配置=%s 存储=%s "
            "托盘可用=%s 托盘图标为空=%s 单实例锁=%s",
            bool(getattr(sys, "frozen", False)),
            "打包版" if getattr(sys, "frozen", False) else "源码",
            APP_DIR, self.settings.path, self.storage.base_dir,
            self.tray.is_available(), self.tray.icon_is_null(),
            getattr(self.guard, "has_lock", None),
        )

    # ---------------------------------------------------------
    # 窗口
    # ---------------------------------------------------------
    def _apply_window_flags(self) -> None:
        on_top = bool(self.settings.get("always_on_top", True))
        flags = self.window.windowFlags()
        if on_top:
            flags |= Qt.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowStaysOnTopHint
        self.window.setWindowFlags(flags)
        self.window.show()

    def _on_hotkey(self, name: str) -> None:
        if name == "show":
            self._show_window()
        elif name == "next":
            self.window.copy_next_item()

    def _apply_hotkeys(self) -> None:
        # 唤出窗口
        seq_show = self.settings.get("global_hotkey") or ""
        ok1 = self.hotkey.register("show", seq_show)
        if seq_show and not ok1:
            applog.get("hotkey").warning("无法注册全局快捷键 %s", seq_show)
            self.tray.show_message(
                "快捷键注册失败",
                f"无法注册 {seq_show}，可能被其他程序占用",
            )

        # 复制上一条
        seq_next = self.settings.get("next_item_hotkey") or ""
        ok2 = self.hotkey.register("next", seq_next)
        if seq_next and not ok2:
            applog.get("hotkey").warning("无法注册全局快捷键 %s", seq_next)
            self.tray.show_message(
                "快捷键注册失败",
                f"无法注册 {seq_next}，可能被其他程序占用",
            )

    def _on_copy_next(self) -> None:
        self.window.copy_next_item()

    def _restore_window_geometry(self) -> None:
        pos = self.settings.get("window_pos")
        size = int(self.settings.get("window_size", 300))
        if pos and isinstance(pos, (list, tuple)) and len(pos) == 2:
            self.window.move(int(pos[0]), int(pos[1]))
        self.window.resize(size, self.window.height())

        # 根据位置自动判断是否贴边。
        # 按窗口所在的屏算：上次是在副屏贴的边，这次还得贴回副屏那条边。
        screen = screen_for_widget(self.window)
        if screen is None:
            return
        area = screen.availableGeometry()
        g = self.window.frameGeometry()
        if g.left() <= area.left() + EDGE_SNAP_THRESHOLD:
            self.window._snap("left")
        elif g.right() >= area.right() - EDGE_SNAP_THRESHOLD:
            self.window._snap("right")

    def _save_window_geometry(self) -> None:
        self.settings.set("window_pos", [self.window.x(), self.window.y()])
        # 细条态的宽度不是用户想要的宽度，别把它写进配置
        if not self.window.is_snapped:
            self.settings.set("window_size", self.window.width())
        self.settings.save()

    def _show_window(self) -> None:
        # 万一窗口还带着最小化状态（被系统快捷键最小化、或老版本留下的），
        # 光 show() 是唤不回来的：Qt::Tool 窗口会一直缩在屏幕左下角。
        # 先 showNormal() 恢复正常状态，再显示。
        if self.window.isMinimized():
            self.window.showNormal()
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def _open_history(self) -> None:
        """打开独立的全部历史窗口"""
        self.history.refresh()
        self.history.popup()

    def _show_window_menu(self, global_pos: QPoint) -> None:
        """在主窗口里右键：弹出与托盘右键相同的菜单"""
        menu = self.tray.build_menu(self.window, in_window=True)
        menu.exec(global_pos)
        menu.deleteLater()

    # ---------------------------------------------------------
    # 剪贴板事件
    # ---------------------------------------------------------
    def _on_clipboard_changed(self) -> None:
        """监听线程通知有新内容（新事件，补读计数清零）"""
        self._empty_retries = 0
        self._collect_clipboard()

    def _retry_collect(self) -> None:
        """补读（不是新事件，别清计数）"""
        self._collect_clipboard()

    def _collect_clipboard(self) -> None:
        """
        采集一次并入历史。

        **任何一次「没记录」都要在 app.log 里留下原因**：
        以前这些分支全是裸 return，用户看到的就是「我明明复制了，历史里没有」，
        而日志里一个字都没有，只能靠猜。
        """
        if self.tray.is_paused():
            return

        log = applog.get("capture")
        try:
            cap = capture_once(
                self.storage,
                exclude_password=bool(self.settings.get("exclude_password", True)),
            )
        except Exception:               # noqa: BLE001 - 采集出问题也绝不能让槽函数炸掉
            log.exception("采集剪贴板出错（本次未记录）")
            return

        if not cap.saved:
            # 补读过程中的「还是空的」只写 DEBUG，免得日志被刷屏；
            # 真正值得看的是第一次「未记录」和后面那次「已记录」。
            if cap.reason == capture_mod.REASON_EMPTY and self._empty_retries:
                log.debug("补读仍读不到内容：%s", cap.describe())
            else:
                log.info("未记录：%s", cap.describe())
            # 读不到任何格式：多半是对方还在写（EmptyClipboard 与 SetClipboardData
            # 之间各发一次通知；有的程序写多个格式，会一直占着剪贴板不放）。
            # 按递增的间隔补读几次，别把这类复制白白丢掉。
            if cap.reason == capture_mod.REASON_EMPTY:
                if self._empty_retries < len(EMPTY_RETRY_DELAYS_MS):
                    delay = EMPTY_RETRY_DELAYS_MS[self._empty_retries]
                    self._empty_retries += 1
                    log.debug("稍后补读一次（第 %s 次，%s 毫秒后）",
                              self._empty_retries, delay)
                    QTimer.singleShot(delay, self._retry_collect)
            return

        log.info("已记录：类型=%s 大小=%s 字节 剪贴板格式=%s",
                 cap.type_name, cap.size,
                 ",".join(cap.formats) if cap.formats else "(无)")

        self.window.refresh()
        # 历史窗口开着的时候同步刷新（不可见就不做无用功）
        if self.history.isVisible():
            self.history.refresh()

    # ---------------------------------------------------------
    # 托盘回调
    # ---------------------------------------------------------
    def _on_pause_toggled(self, paused: bool) -> None:
        msg = "已暂停监听" if paused else "已恢复监听"
        self.tray.show_message("剪贴板历史", msg)

    def _on_minimized_to_tray(self) -> None:
        """点「−」= 收进托盘（不是真最小化，见 MainWindow.changeEvent）"""
        self.tray.show_message(
            "剪贴板历史", "已收进托盘：点托盘图标（或按快捷键）就能再打开")

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self.settings, self.window)
        if dlg.exec():
            self.window.apply_settings(self.settings.as_dict())
            self._apply_window_flags()
            self._apply_auto_start()
            self._apply_hotkeys()

            msg = QMessageBox(self.window)
            msg.setWindowTitle("设置已保存")
            msg.setText("部分设置（如存储路径、最大条数）将在重启后生效。")
            msg.setIcon(QMessageBox.Information)
            msg.setStandardButtons(QMessageBox.Ok)
            # 关键：置顶 + 主窗口模态
            msg.setWindowFlags(msg.windowFlags() | Qt.WindowStaysOnTopHint)
            msg.setWindowModality(Qt.WindowModal)
            msg.adjustSize()
            center_on_screen(msg, self.window) 
            msg.exec()

    def _show_about(self) -> None:
        """关于：版本号、作者、运行方式、数据目录、记录条数"""
        AboutDialog.show_about(storage=self.storage, parent=self.window)

    def _show_help(self) -> None:
        """
        帮助（使用手册）：用记事本打开随程序发布的用户手册。

        打开失败会在程序内兜底显示全文（见 ui/help.py），
        所以这里不需要再考虑「点了没反应」的情况。
        """
        from ui.help import open_manual
        open_manual(self.window)

    # ---------------------------------------------------------
    # 开机自启
    # ---------------------------------------------------------
    def _apply_auto_start(self) -> None:
        """
        开机自启。

        打包版走「登录时 + 最高权限」的计划任务：exe 清单里要求管理员权限，
        Run 键拉起的进程不带提权，安全软件又会拦非提权进程，自启起来等于残废。
        源码运行仍然用 Run 键（python.exe 不挑权限）。
        细节见 core/autostart.py。
        """
        enable = bool(self.settings.get("auto_start", False))
        try:
            from core.autostart import apply as apply_autostart
            detail = apply_autostart(enable)
            applog.get("app").info("开机自启设置：%s（%s）",
                                   "开启" if enable else "关闭", detail)
        except Exception as e:                  # noqa: BLE001 - 自启失败不影响使用
            applog.get("app").warning("设置开机自启失败（enable=%s）：%s", enable, e)

    # ---------------------------------------------------------
    # 存储目录 / 重启
    # ---------------------------------------------------------
    def _open_storage_dir(self) -> None:
        """
        在资源管理器里打开存储目录。

        三条路，顺序有讲究：
        1. **起 explorer.exe**（%SystemRoot% 下的绝对路径）：绕开被改坏的文件关联，
           而且能通过退出码判断它是真起来了还是死在加载器；
        2. QDesktopServices.openUrl / os.startfile（ShellExecute）：常规做法；
        3. 都不行就弹一个**看得见的**对话框，并提供「复制路径」兜底。

        旧实现只有第 2 条，失败时只发托盘气泡——托盘图标看不见时等于零提示，
        用户看到的就是「点了没反应」。
        （曾经试过 shell32 的 SHOpenFolderAndSelectItems：那个 API 的语义是
        「在所在文件夹里选中这一项」，传目录会打开它的**上一级**，所以不能用。）
        """
        log = applog.get("storage")
        path = self.storage.base_dir

        try:
            os.makedirs(path, exist_ok=True)
        except OSError as e:
            self._report_open_failure(path, f"创建目录失败：{e}")
            return

        errors: list[str] = []

        # 1) 起 explorer.exe（绝对路径），并且**检查它有没有真的起来**：
        #    被安全软件拦住 / 系统文件损坏时，explorer.exe 会在加载器阶段就挂掉
        #    （退出码 0xC0000142、0xC0000428 之类），Windows 还会弹
        #    「应用程序无法正常启动」。这种情况绝不能当成成功。
        explorer = os.path.join(os.environ.get("SystemRoot") or r"C:\Windows",
                                "explorer.exe")
        if os.path.exists(explorer):
            ok, detail = self._launch_explorer(explorer, path)
            if ok:
                log.info("已用 %s 打开存储目录：%s", explorer, path)
                return
            errors.append(detail)
        else:
            errors.append(f"{explorer} 不存在")

        # 2) Qt 的桌面服务（内部走 ShellExecute）
        try:
            if QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
                log.info("已用 QDesktopServices 打开存储目录：%s", path)
                return
            errors.append("QDesktopServices.openUrl 返回 False")
        except Exception as e:                  # noqa: BLE001
            errors.append(f"QDesktopServices：{e}")

        # 3) os.startfile（Windows 原生）
        try:
            os.startfile(path)                  # type: ignore[attr-defined]
            log.info("已用 os.startfile 打开存储目录：%s", path)
            return
        except OSError as e:
            errors.append(f"os.startfile：{e}")

        self._report_open_failure(path, "；".join(errors))

    @staticmethod
    def _launch_explorer(explorer: str, path: str) -> tuple[bool, str]:
        """
        起 explorer.exe 打开目录，并判断它**真的起来了**。

        为什么要等一小会儿看退出码：explorer.exe 被拦/损坏时死在加载器阶段，
        退出码是 0xC0000142、0xC0000428 这类（>= 0xC0000000）；而正常打开
        「已有窗口」时 explorer.exe 会很快退出并返回 1——这是官方行为，算成功。
        """
        log = applog.get("storage")
        try:
            # SEM_FAILCRITICALERRORS：子进程加载失败时不要弹系统的
            # 「应用程序无法正常启动(0x…)」——我们自己会记日志 + 弹自己的提示，
            # 系统的那个框只会让用户一头雾水（而且他也不知道是谁起不来了）
            kernel32 = ctypes.windll.kernel32
            old_mode = kernel32.SetErrorMode(0x0001)
        except Exception:                       # noqa: BLE001 - 非 Windows / 调用失败就算了
            kernel32, old_mode = None, None

        try:
            proc = subprocess.Popen([explorer, os.path.normpath(path)], close_fds=True)
        except OSError as e:
            return False, f"explorer.exe 启动失败：{e}"
        finally:
            if kernel32 is not None and old_mode is not None:
                try:
                    kernel32.SetErrorMode(old_mode)
                except Exception:               # noqa: BLE001
                    pass

        deadline = time.monotonic() + EXPLORER_PROBE_S
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                code = proc.returncode & 0xFFFFFFFF
                if code >= 0xC0000000:
                    msg = (f"explorer.exe 加载失败，退出码 0x{code:08X}"
                           f"（应用程序无法正常启动），通常是安全软件拦截或系统文件异常")
                    log.error("%s", msg)
                    return False, msg
                log.info("explorer.exe 退出码 %s（正常行为）", code)
                return True, f"explorer.exe 退出码 {code}"
            time.sleep(0.05)
        return True, "explorer.exe 仍在运行"

    def _report_open_failure(self, path: str, reason: str) -> None:
        """打开目录失败：日志 + **对话框** + 气泡（并提供「复制路径」兜底）

        顺序很关键：先把对话框弹出来（这是用户唯一一定看得见的东西），
        气泡放在对话框关掉之后再发——气泡要发给壳，壳不正常时那个调用会阻塞，
        不能让「看得见的提示」排在「可能卡住的调用」后面。
        """
        applog.get("storage").error("打开存储目录失败：%s（%s）", path, reason)

        box = QMessageBox(self.window)
        box.setWindowTitle("打开存储文件夹失败")
        box.setIcon(QMessageBox.Warning)
        box.setText("没能在资源管理器里打开存储目录。")
        box.setInformativeText(
            f"目录：{path}\n\n原因：{reason}\n\n"
            f"点「复制路径」后粘贴到资源管理器地址栏也能打开；\n"
            f"详细信息见日志：{applog.log_file_path()}"
        )
        btn_copy = box.addButton("复制路径", QMessageBox.ActionRole)
        box.addButton("关闭", QMessageBox.AcceptRole)
        box.setWindowFlags(box.windowFlags() | Qt.WindowStaysOnTopHint)
        box.setWindowModality(Qt.WindowModal)
        box.adjustSize()
        center_on_screen(box, self.window)
        box.exec()
        if box.clickedButton() is btn_copy:
            QApplication.clipboard().setText(path)
            applog.get("storage").info("已把存储目录复制到剪贴板：%s", path)

        # 对话框关了再发气泡（show_message 内部是延后发送的，不会挡住这里）
        self.tray.show_message("打开存储文件夹失败", f"{path}\n{reason}")

    def _restart(self) -> None:
        """
        重启应用。

        顺序很重要：
        1. 现在就把快捷键、剪贴板监听、数据库、单实例锁都放掉，
           否则新进程注册全局快捷键会被本进程占着而失败；
        2. 等本进程真正退出（app.aboutToQuit）之后才拉起新进程。
           单实例锁是「本进程还活着」时一直占着的，如果在这里就 spawn，
           新进程会在旧进程退出的那几十毫秒里抢到锁 -> 开库时发现存储还被
           占着 -> 直接弹“程序已经在运行”。等退出后再 spawn 就没这个缝。

        注意：_release_resources() 内部已经保证不抛异常，所以后面的
        app.quit() 一定执行得到（以前只要有一步出错，重启就完全没反应）。
        """
        if self._restarting:            # 连点两次也只重启一次
            return
        self._restarting = True
        self._spawned = False
        applog.get("lifecycle").info("收到重启请求")
        self._arm_exit_watchdog(RESTART_WATCHDOG_S, restart=True)
        self._release_resources()
        self.app.aboutToQuit.connect(self._spawn_new_instance)
        self.app.quit()

    def _spawn_new_instance(self) -> None:
        """按当前运行方式（源码 / 打包）拉起一个新进程（本进程此时正在退出）"""
        log = applog.get("lifecycle")
        # 让操作系统把本进程的文件句柄（含单实例锁）彻底收掉再拉起新进程
        time.sleep(RESTART_EXIT_WAIT_S)

        if getattr(sys, "frozen", False):
            program, args = sys.executable, []
            workdir = os.path.dirname(sys.executable)
        else:
            program = sys.executable
            args = [os.path.abspath(__file__)]
            workdir = APP_DIR

        errors: list[str] = []

        # 1) Qt 的 startDetached
        try:
            result = QProcess.startDetached(program, args, workdir)
            # PySide 把 (bool, pid) 作为元组返回，而元组恒为真，必须显式取第一项
            ok = result[0] if isinstance(result, tuple) else bool(result)
            if ok:
                self._spawned = True
                log.info("重启：QProcess 已拉起新进程 %s %s", program, args)
                return
            errors.append("QProcess.startDetached 返回失败")
        except Exception as e:                  # noqa: BLE001 - 换下一条路
            errors.append(f"QProcess.startDetached：{e}")

        # 2) subprocess 兜底：Qt 的启动被拦时，直接 CreateProcess 往往能过
        try:
            flags = (DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
                     if os.name == "nt" else 0)
            subprocess.Popen(
                [program, *args], cwd=workdir or None,
                creationflags=flags, close_fds=True,
            )
            self._spawned = True
            log.info("重启：subprocess 已拉起新进程 %s", program)
            return
        except Exception as e:                  # noqa: BLE001
            errors.append(f"subprocess.Popen：{e}")

        reason = "；".join(errors)
        log.error("重启失败：%s", reason)
        self._warn_restart_failed(program, reason)

    def _warn_restart_failed(self, program: str, reason: str) -> None:
        """两条路都没起来：别让用户面对一个「自己消失了的程序」还不明所以"""
        box = QMessageBox()
        box.setWindowTitle("重启失败")
        box.setIcon(QMessageBox.Warning)
        box.setText("无法启动新的进程，请手动重新打开程序。")
        box.setInformativeText(
            f"程序：{program}\n\n原因：{reason}\n\n"
            f"详细信息见日志：\n{applog.log_file_path()}"
        )
        box.setStandardButtons(QMessageBox.Ok)
        box.setWindowFlags(box.windowFlags() | Qt.WindowStaysOnTopHint)
        box.adjustSize()
        center_on_screen(box)
        box.exec()

    # ---------------------------------------------------------
    # 退出
    # ---------------------------------------------------------
    def _release_resources(self) -> None:
        """释放全局资源：单实例锁 / 快捷键 / 监听 / 窗口 / 数据库 / 托盘

        顺序有讲究：先放掉单实例锁，否则「重启」时新进程会把它当成
        “已经有实例在跑”而自己退出（见 _restart 的说明）。

        每一步都单独 try：**任何一环出错都不能挡住退出**。
        以前没有这层保护，只要某一步抛异常（例如配置写盘被安全软件拦住、
        存储目录不可写），后面的 app.quit() 就永远执行不到——
        用户看到的现象正是「点退出 / 重启没反应」。
        """
        log = applog.get("lifecycle")

        def step(name: str, action) -> None:
            try:
                action()
            except Exception as e:              # noqa: BLE001 - 退出流程必须走完
                log.exception("释放资源失败：%s（%s）", name, e)

        guard = getattr(self.app, "_single_instance", None)
        if guard is not None:
            step("单实例锁", guard.stop)

        step("保存窗口位置", self._save_window_geometry)
        step("注销全局快捷键", self.hotkey.unregister_all)
        step("停止剪贴板监听", self.listener.stop)
        step("隐藏历史窗口", self.history.hide)
        step("隐藏主窗口", self.window.hide)
        step("关闭存储", self.storage.close)
        step("隐藏托盘图标", self.tray.hide)

    def _quit(self) -> None:
        applog.get("lifecycle").info("收到退出请求")
        self._arm_exit_watchdog(QUIT_WATCHDOG_S)
        self._release_resources()
        self.app.quit()

    def _arm_exit_watchdog(self, timeout_s: float, restart: bool = False) -> None:
        """
        退出看门狗。

        前一层保护（每步单独 try）解决的是「某一步抛异常」，这一层解决的是
        「某一步卡住」——比如 pynput 的监听线程、SQLite 关闭被拖住。
        那种情况下事件循环根本轮不到跑，QTimer 兜底也救不了，所以用一个
        独立的守护线程：到点就把进程结束掉。用户点的是「退出/重启」，
        结果必须是「程序真的退出了」。
        """
        def run() -> None:
            time.sleep(timeout_s)
            log = applog.get("lifecycle")
            log.warning("退出流程超过 %.1fs 仍未结束，强制结束进程（restart=%s）",
                        timeout_s, restart)
            if restart and not self._spawned:
                try:
                    self._spawn_new_instance()      # 兜底：先把新进程拉起来再硬退
                except Exception as e:              # noqa: BLE001
                    log.error("兜底拉起新进程失败：%s", e)
            os._exit(0)

        threading.Thread(target=run, daemon=True, name="exit-watchdog").start()


def _elevated_text() -> str:
    """日志里用：当前进程是不是管理员权限（打包版清单要求提权，用于核对）"""
    try:
        from core.autostart import is_elevated
        return "是" if is_elevated() else "否"
    except Exception:                           # noqa: BLE001
        return "?"


def load_stylesheet(app: QApplication) -> None:
    if os.path.exists(QSS_PATH):
        try:
            with open(QSS_PATH, "r", encoding="utf-8") as f:
                app.setStyleSheet(f.read())
        except OSError as e:
            applog.get("app").warning("样式表读取失败：%s（%s）", QSS_PATH, e)
    else:
        # 打包后要是出现这行，说明 datas 没带上 ui/style.qss（资源路径问题）
        applog.get("app").warning("样式表不存在：%s", QSS_PATH)


def show_message_box(title: str, text: str, info: str = "",
                     icon=QMessageBox.Warning) -> None:
    """统一的可见错误框（一律置顶，免得被别的窗口压在后面）"""
    box = QMessageBox()
    box.setWindowTitle(title)
    box.setIcon(icon)
    box.setText(text)
    if info:
        box.setInformativeText(info)
    box.setStandardButtons(QMessageBox.Ok)
    box.setWindowFlags(box.windowFlags() | Qt.WindowStaysOnTopHint)
    box.adjustSize()
    center_on_screen(box)
    box.exec()


def show_storage_locked_error(exc: StorageLockedError) -> None:
    """存储目录被别的实例占着：说清楚原因，别再默默起第二份"""
    show_message_box(
        APP_NAME,
        "程序已经在运行了。",
        f"这个数据目录正被另一个 {APP_NAME} 进程使用：\n{exc.path}\n\n"
        "请使用右下角托盘图标里的窗口，或先退出那个进程。",
    )


def main() -> int:
    # ---- 日志：打包后没有控制台，一切线索只能靠这个文件 ----
    applog.setup()
    log = applog.get("main")
    log.info("======== 程序启动 pid=%s argv=%s 版本=%s ========",
             os.getpid(), sys.argv[1:], __version__)
    log.info("环境：user=%s APPDATA=%s USERPROFILE=%s frozen=%s 提权=%s 日志=%s",
             os.environ.get("USERNAME") or os.environ.get("USER") or "?",
             os.environ.get("APPDATA", "(未设置)"),
             os.environ.get("USERPROFILE", "(未设置)"),
             bool(getattr(sys, "frozen", False)),
             _elevated_text(), applog.log_file_path())

    # ---- 自检模式：ClipboardHistory.exe --diag ----
    # 只做检查（含托盘实测），不建主窗口、不碰监听，报告落盘并弹窗
    from core.diag import run_diag, wants_diag
    if wants_diag(sys.argv):
        return run_diag()

    # ---- 卸载模式：必须在 QApplication 之前处理 ----
    # 这两条分支会在建窗口/开数据库之前退出，卸载时不会因为占着文件而删不掉。
    from core.uninstall import (
        find_uninstaller, parse_uninstall_args, run_uninstall_now,
        run_uninstall_ui, wants_uninstall, wants_uninstall_now,
    )
    if wants_uninstall_now(sys.argv):
        return run_uninstall_now(parse_uninstall_args(sys.argv))
    if wants_uninstall(sys.argv):
        # 装了独立卸载器（uninstaller.exe）就交给它：界面更细（可以分别选择
        # 删文本 / 图片 / 文件），也不需要主程序一直背着卸载界面的代码。
        # 本进程是提权的，拉起同样要求管理员权限的卸载器不会再弹一次 UAC。
        uninstaller = find_uninstaller()
        if uninstaller:
            log.info("转交卸载器：%s", uninstaller)
            try:
                subprocess.Popen([uninstaller], close_fds=True)
                return 0
            except OSError as e:
                log.warning("启动卸载器失败（%s），改用内置卸载流程", e)
        return run_uninstall_ui()

    # ---- 安装模式：ClipboardHistory.exe --install ----
    # 整包发布时用它起安装向导（单独打包的 ClipboardHistory-Setup.exe
    # 是同一个向导，只是把程序文件带在自己身上，见 build/installer.spec）
    from installer import run_installer_ui, wants_install
    if wants_install(sys.argv):
        return run_installer_ui(sys.argv)

    app = QApplication(sys.argv)
    # 版本号 / 组织名统一从 core/version.py 取（关于框也是同一份数据）
    app.setApplicationName(APP_NAME_EN)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setApplicationVersion(__version__)
    if os.path.exists(ICON_PATH):
        app.setWindowIcon(QIcon(ICON_PATH))
    else:
        log.warning("程序图标不存在：%s", ICON_PATH)
    load_stylesheet(app)

    # ---- 单实例保护 ----
    # 双击第二次时，第二个进程只负责把已有实例的窗口唤出来，然后自己退出。
    # 必须放在建托盘/监听之前，否则第二次启动会先把快捷键抢一遍再退出。
    if notify_existing_instance():
        log.info("已有实例在运行，已唤出原有窗口，本进程退出")
        return 0

    # 等一下再抢锁：托盘里的「重启」是新进程先起来、旧进程随后才退出，
    # 锁要几百毫秒后才放开，直接抢会误判成“已有实例”。
    guard = SingleInstance(app)
    if not guard.start(SINGLE_INSTANCE_WAIT_S):
        # 等了这么久还没等到：确实有实例在跑，把窗口叫出来然后自己退
        notify_existing_instance()
        log.info("已有实例在运行（等锁超时），本进程退出")
        return 0
    app._single_instance = guard        # 防 GC，退出时由 _release_resources 释放

    try:
        controller = AppController(app, guard)
    except StorageLockedError as e:
        # 理论上单实例锁已经拦住了；但万一锁文件那个目录不可写（此时会退化成
        # “允许启动”）而存储目录又被占着，这里必须拦住：绝不能让两个进程
        # 同时写同一个 SQLite。
        guard.stop()
        log.warning("存储目录被占用：%s", e.path)
        show_storage_locked_error(e)
        return 1
    except Exception as e:                  # noqa: BLE001 - 启动失败必须让用户看见原因
        # 打包后只写日志是没人知道的：给一个看得懂的框 + 日志路径
        guard.stop()
        log.exception("启动失败")
        show_message_box(
            APP_NAME,
            "程序启动失败。",
            f"原因：{type(e).__name__}: {e}\n\n"
            f"详细信息见日志：\n{applog.log_file_path()}",
        )
        return 1
    app._controller = controller        # 防止被 GC

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())