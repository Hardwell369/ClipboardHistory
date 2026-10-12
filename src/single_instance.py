# src/single_instance.py
"""
单实例保护（Qt 层）。

双击两次 exe（或快捷键 + 双击）时，第二个进程不该再起一份：
否则会同时打开同一个 history.db、注册同一套全局快捷键、多挂一条剪贴板监听链。

实现方式：**进程级文件锁 + 命令文件**（底层在 src/lockfile.py，不依赖 Qt，
卸载器也复用同一份）：
    %APPDATA%\\ClipboardHistory\\app.lock     第一个进程独占锁住它
    %APPDATA%\\ClipboardHistory\\app.cmd      其它进程往里写 "show" / "quit"

为什么不用 QLocalServer / 命名管道：
Qt 的本地套接字在受限环境（沙箱、部分安全软件、某些域的 AppContainer）里
listen/connect 会直接 Access denied，锁不住就白搭；文件锁由系统在进程退出时
自动释放（强杀也不会留死锁），跨进程通知用命令文件同样够用。

锁文件用固定路径（不跟存储目录走）：换存储目录也是同一个程序，只允许一份。
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

from src import applog
from src.lockfile import (                 # noqa: F401 - 兼容旧的导入路径
    CMD_FILE_NAME, CMD_QUIT, CMD_SHOW, LOCK_DIR_NAME, LOCK_FILE_NAME,
    LOCK_TIMEOUT_S, RETRY_INTERVAL_S, base_dir, cmd_path, is_running, lock_path,
    notify_existing_instance, read_command, release, remove_files,
    set_warn_hook, try_acquire, unlock, wait_until_free, write_command,
)

POLL_INTERVAL_MS = 400      # 主实例查命令文件的周期


def _warn_to_log(msg: str) -> None:
    """锁文件不可用时写日志（打包后 stderr 是没人看的）"""
    applog.get("single_instance").warning(msg)


set_warn_hook(_warn_to_log)


class SingleInstance(QObject):
    """
    主实例侧：持有锁 + 轮询命令文件。

    用法：
        guard = SingleInstance()
        if not guard.start():
            sys.exit(0)          # 已有实例，别再起
    """

    activated = Signal()          # 另一个实例启动了，请求唤出窗口
    quit_requested = Signal()     # 另一个进程（卸载器 / 命令行）请求优雅退出

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._lock = None
        self._timer: QTimer | None = None

    # ---------------------------------------------------------
    def start(self, timeout_s: float = 0.0) -> bool:
        """
        抢锁。成功返回 True；返回 False 表示已有实例在运行，本进程应尽快退出。

        timeout_s > 0 用于「重启」：旧的自己刚退出，锁可能还要几百毫秒才放开。
        """
        if self._lock is not None:
            return True

        lock = try_acquire(timeout_s)
        if lock is None:
            return False
        self._lock = lock

        # 自己开机时残留的命令文件（上次异常退出）先清掉
        read_command()

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self.poll)
        self._timer.start()
        return True

    def stop(self) -> None:
        """放锁（重启流程必须调用，否则新进程要等到本进程真正退出才拿得到锁）"""
        if self._timer is not None:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None
        release(self._lock)
        self._lock = None
        read_command()          # 顺手清掉没来得及处理的命令文件

    @property
    def has_lock(self) -> bool:
        """是否真的锁上了（锁文件建不出来时是 False，只是允许启动而已）"""
        return self._lock is not None and getattr(self._lock, "is_real", True)

    # ---------------------------------------------------------
    def poll(self) -> None:
        """定时器回调：有别的进程叫我们就亮个相 / 请我们退出"""
        command = read_command()
        if command == CMD_SHOW:
            self.activated.emit()
        elif command == CMD_QUIT:
            self.quit_requested.emit()
