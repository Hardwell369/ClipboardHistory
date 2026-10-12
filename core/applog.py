# core/applog.py
"""
统一日志。

打包成 exe 之后 console=False：既没有控制台，stdio 也基本是废的。
这时候如果还用 print 报错，用户和我们都拿不到任何线索——
「点了没反应」这种问题就永远查不出来。

所以规则是：
- 日志一律落盘：%APPDATA%\\ClipboardHistory\\app.log（自动轮转，最多 3 个文件）
- 日志目录写不进去（权限 / 只读盘 / 安全软件拦截）时静默降级，
  **绝不能因为写日志让程序起不来**
- 可用环境变量 CLIPBOARD_LOG_FILE 指定日志文件（排查 / 自动化测试用）

用法：
    from core import applog
    applog.setup()                      # 入口处调一次
    applog.get("tray").warning("...")   # 各模块按名字取 logger
"""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

ROOT_NAME = "clipboard"
LOG_FILE_NAME = "app.log"
MAX_BYTES = 256 * 1024
BACKUP_COUNT = 2

_configured = False
_active_path: str | None = None      # 实际写成功的日志文件（可能不是首选路径）


def default_log_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "ClipboardHistory")


def candidate_paths() -> list[str]:
    """
    依次尝试的日志路径。

    首选 %APPDATA%\\ClipboardHistory\\app.log；那里写不进去（权限/只读/安全软件
    拦截）时退到 LOCALAPPDATA，再退到 TEMP——**日志一定要落在某个能拿到的地方**，
    否则「打包后没有控制台」就等于彻底没有线索。
    """
    out: list[str] = []
    override = (os.environ.get("CLIPBOARD_LOG_FILE") or "").strip()
    if override:
        out.append(override)

    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    out.append(os.path.join(appdata, "ClipboardHistory", LOG_FILE_NAME))

    local = os.environ.get("LOCALAPPDATA")
    if local:
        out.append(os.path.join(local, "ClipboardHistory", LOG_FILE_NAME))

    # exe 自己所在目录：安全软件把用户配置目录整个拦掉时（实测会遇到），
    # 这里往往是唯一还写得进去的地方——日志必须落在某个能拿到的地方
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        if exe_dir:
            out.append(os.path.join(exe_dir, LOG_FILE_NAME))

    temp = os.environ.get("TEMP") or os.environ.get("TMP")
    if temp:
        out.append(os.path.join(temp, "ClipboardHistory-app.log"))

    # 去重但保持顺序
    seen: set[str] = set()
    uniq: list[str] = []
    for p in out:
        key = os.path.normcase(os.path.abspath(p))
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def log_file_path() -> str:
    """当前实际使用的日志路径（还没 setup() 过就返回首选项）"""
    if _active_path:
        return _active_path
    paths = candidate_paths()
    return paths[0] if paths else os.path.join(default_log_dir(), LOG_FILE_NAME)


def setup(level: int = logging.INFO) -> logging.Logger:
    """配置根 logger（重复调用只生效一次），返回它"""
    global _configured, _active_path
    logger = logging.getLogger(ROOT_NAME)
    if _configured:
        return logger
    _configured = True
    logger.setLevel(level)
    logger.propagate = False

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    # 1) 文件：打包后唯一可靠的出口，逐个候选路径试
    for path in candidate_paths():
        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            handler = RotatingFileHandler(
                path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8")
            handler.setFormatter(fmt)
            logger.addHandler(handler)
            _active_path = path
            break
        except (OSError, ValueError):
            continue            # 这个位置写不了，换下一个

    # 2) stderr：源码运行 / 调试时直接能看到（窗口程序里可能是 None）
    stream = getattr(sys, "stderr", None)
    if stream is not None and hasattr(stream, "write"):
        try:
            sh = logging.StreamHandler(stream)
            sh.setFormatter(fmt)
            logger.addHandler(sh)
        except Exception:       # noqa: BLE001 - 加不上就只落盘
            pass

    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
    return logger


def get(name: str) -> logging.Logger:
    """按模块取 logger，例如 applog.get("tray")"""
    return logging.getLogger(f"{ROOT_NAME}.{name}")
