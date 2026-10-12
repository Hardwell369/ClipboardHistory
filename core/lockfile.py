# core/lockfile.py
"""
单实例所需的「文件锁 + 命令文件」底层实现（**不依赖 Qt**）。

为什么要单独拆出来：
    主程序用它做单实例保护；卸载器（uninstaller.exe）也要用它——
    卸载前得知道「程序是不是正在运行」，正在运行就先请它退出，
    否则安装目录里的 exe 被占用、history.db 也写不进去。
    卸载器是个独立的 exe，不想为了这点功能把 PySide6 也打进去，
    所以这些只用标准库的代码放在这里，Qt 那一层留在 core/single_instance.py。

约定：
    %APPDATA%\\ClipboardHistory\\app.lock   第一个进程独占锁住它
    %APPDATA%\\ClipboardHistory\\app.cmd    其它进程往里写命令（show / quit）
锁文件用固定路径（不跟存储目录走）：换存储目录也是同一个程序，只允许一份。
可用环境变量 CLIPBOARD_LOCK_DIR 覆盖目录（自动化测试用）。
"""
from __future__ import annotations

import os
import time

LOCK_DIR_NAME = "ClipboardHistory"
LOCK_FILE_NAME = "app.lock"
CMD_FILE_NAME = "app.cmd"

LOCK_TIMEOUT_S = 6.0        # 等锁最长时间（要盖住「重启」时旧进程退出的那几百毫秒）
RETRY_INTERVAL_S = 0.15

# 命令文件里能写的命令
CMD_SHOW = "show"           # 把窗口唤出来
CMD_QUIT = "quit"           # 优雅退出（卸载前用它，别直接杀进程）

try:
    import msvcrt
except ImportError:                     # 非 Windows
    msvcrt = None                       # type: ignore[assignment]
    try:
        import fcntl
    except ImportError:
        fcntl = None                    # type: ignore[assignment]


# ---------------------------------------------------------
# 路径
# ---------------------------------------------------------
def base_dir() -> str:
    """
    锁/命令文件所在目录。

    允许用环境变量覆盖：自动化测试要跑多组互不干扰的实例，
    真实 APPDATA 在受限环境里也可能写不进去。
    """
    override = (os.environ.get("CLIPBOARD_LOCK_DIR") or "").strip()
    if override:
        return override
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(appdata, LOCK_DIR_NAME)


def lock_path() -> str:
    return os.path.join(base_dir(), LOCK_FILE_NAME)


def cmd_path() -> str:
    return os.path.join(base_dir(), CMD_FILE_NAME)


# ---------------------------------------------------------
# 文件锁
# ---------------------------------------------------------
def _lock_once(f) -> None:
    """尝试加一次锁；被占用时抛 OSError"""
    f.seek(0)
    if msvcrt is not None:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)          # type: ignore[union-attr]
    elif fcntl is not None:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[union-attr]
    else:
        raise OSError("no file locking available")


def unlock(f) -> None:
    try:
        f.seek(0)
        if msvcrt is not None:
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)      # type: ignore[union-attr]
        elif fcntl is not None:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)              # type: ignore[union-attr]
    except OSError:
        pass


class _FakeLock:
    """
    “没有锁能力”的占位对象。

    它是 try_acquire() 三个返回值里唯一表示“可以启动”的失败态，
    所以必须能安全地过一遍 release()/unlock()，不能缺方法。
    """

    is_real = False                 # 便于判断“其实没锁上”

    def seek(self, *args) -> int:
        return 0

    def fileno(self) -> int:
        return -1

    def close(self) -> None:
        pass


# 锁文件建不出来时只提示一次，别在轮询里刷屏
_warned = False
_warn_hook = None                   # 由调用方注入（主程序接到 applog 上）


def set_warn_hook(hook) -> None:
    """注入「锁文件不可用」的提示方式（主程序用它写日志）"""
    global _warn_hook
    _warn_hook = hook


def _warn_no_lock(err: OSError) -> None:
    global _warned
    if _warned:
        return
    _warned = True
    msg = (f"无法使用单实例锁文件 {lock_path()}：{err}；"
           f"本次会跳过单实例检查，仍由存储目录锁兜底")
    if _warn_hook is not None:
        try:
            _warn_hook(msg)
            return
        except Exception:                   # noqa: BLE001 - 提示失败不能影响主流程
            pass
    try:
        import sys
        print(f"[lockfile] {msg}", file=sys.stderr)
    except Exception:                       # noqa: BLE001 - stderr 不可用时别炸
        pass


def try_acquire(timeout_s: float = 0.0):
    """
    尝试独占锁文件。

    返回：
        - 文件对象   拿到锁，必须一直持有（别 GC），退出时交给 release()
        - None       锁被别的实例占着（等够了 timeout_s 还是拿不到）
        - _FakeLock  锁文件根本建不出来（目录不可写 / 只读盘）：这时**不能**
                     当成“已经有实例在跑”（否则程序永远起不来），而是放行启动，
                     只是失去单实例保护；此时还有存储目录那把锁兜底。
    """
    path = lock_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # "a+" 既能锁又能写，且不会像 "w" 那样把别的进程正在用的文件截断
        f = open(path, "a+", encoding="utf-8")
        if os.path.getsize(path) == 0:
            f.write(" ")
            f.flush()
    except OSError as e:
        _warn_no_lock(e)
        return _FakeLock()

    deadline = time.monotonic() + max(0.0, timeout_s)
    while True:
        try:
            _lock_once(f)
            break
        except OSError:
            if time.monotonic() >= deadline:
                f.close()
                return None
            time.sleep(RETRY_INTERVAL_S)

    try:
        f.seek(0)
        f.truncate()
        f.write(f"{os.getpid()}\n")
        f.flush()
    except OSError:
        pass
    return f


def release(lock) -> None:
    """释放锁；None / 占位对象都安全"""
    if lock is None:
        return
    try:
        unlock(lock)
    finally:
        try:
            lock.close()
        except OSError:
            pass


# ---------------------------------------------------------
# 命令行辅助（测试 / 卸载器用）
# ---------------------------------------------------------
def is_running() -> bool:
    """
    有没有实例正持有锁（= 程序正在运行）。

    锁文件建不出来（_FakeLock）时无法判断，按「没在跑」处理：
    卸载器这时应当在删除失败时把原因报出来，而不是拦着不让卸载。
    """
    lock = try_acquire(0.0)
    if lock is None:
        return True                     # 锁被别人占着
    release(lock)
    return False


def wait_until_free(timeout_s: float = 5.0,
                    interval_s: float = RETRY_INTERVAL_S) -> bool:
    """等锁被放开（= 程序退出）。返回 True 表示已经空闲。"""
    deadline = time.monotonic() + max(0.0, timeout_s)
    while True:
        if not is_running():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval_s)


# ---------------------------------------------------------
# 命令文件（另一个进程 -> 主实例）
# ---------------------------------------------------------
def write_command(command: str) -> bool:
    """
    给已在运行的主实例留一条命令。返回是否写成功。

    先原子写临时文件再 replace，主实例不会读到半截内容。
    """
    path = cmd_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(command + "\n")
        os.replace(tmp, path)       # 原子替换
    except OSError:
        return False
    return True


def notify_existing_instance(command: str = CMD_SHOW) -> bool:
    """
    给已在运行的主实例留一条命令（通常是“把窗口显示出来”）。

    返回 True 表示“看上去确实有主实例”，调用方应当直接退出。
    先试锁：锁被占着才写命令文件，免得单开一个进程时凭空留下垃圾文件。
    """
    if not is_running():
        return False
    write_command(command)
    return True


def read_command() -> str:
    """主实例读命令文件（读完即删，避免重复触发）"""
    path = cmd_path()
    # PermissionError 是 OSError 的子类，这里一并吞掉：读不到就当没有命令
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = f.read().strip()
    except OSError:
        return ""
    try:
        os.remove(path)
    except OSError:
        pass
    return data


def remove_files() -> None:
    """删掉锁/命令文件（卸载时用；删不掉不算错）"""
    for path in (lock_path(), cmd_path()):
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
