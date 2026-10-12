# core/autostart.py
"""
开机自启。

两种实现方式，按运行方式挑：

- **打包版**（exe 清单里要求管理员权限）：用「登录时运行 + 以最高权限运行」的计划任务。
  为什么不继续用 `HKCU\\...\\Run`：Run 键拉起的进程**不带提权**，而安全软件恰恰
  只拦非提权进程（实测：非提权时 `%APPDATA%` 写入、`HKCU\\Run` 写入、
  启动 explorer.exe、托盘图标全部被拦）。那样自启起来的是一个残废实例——
  所以自启必须走计划任务。

- **源码运行**：`python.exe` 不挑权限，直接用 Run 键最省事。

开关都做成幂等的：开的时候顺手清掉另一种方式留下的痕迹（避免起两份），
关的时候两种都清。所有函数都**不抛异常**，失败只记日志（自启失败不该影响使用）。
"""
from __future__ import annotations

import os
import subprocess
import sys
import winreg

from core import applog

TASK_NAME = "ClipboardHistory"
RUN_VALUE = "ClipboardHistory"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
CREATE_NO_WINDOW = 0x08000000
CMD_TIMEOUT_S = 30


# ---------------------------------------------------------
# 启动命令
# ---------------------------------------------------------
def program_and_args(program: str | None = None) -> tuple[str, list[str]]:
    """
    (程序, 参数)：打包版就是 exe 自己，源码版是 python + main.py。

    program 显式给定时就用它（安装器要在自启里指向**装好的那个 exe**，
    而不是正在运行的安装程序本身）。
    """
    if program:
        return os.path.abspath(program), []
    if getattr(sys, "frozen", False):
        return os.path.abspath(sys.executable), []
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return sys.executable, [os.path.join(root, "main.py")]


def command_line(program: str | None = None) -> str:
    """带引号的完整命令行（路径里有空格也能用）"""
    exe, args = program_and_args(program)
    parts = [f'"{exe}"'] + [f'"{a}"' for a in args]
    return " ".join(parts)


# ---------------------------------------------------------
# 计划任务
# ---------------------------------------------------------
def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, errors="replace",
            creationflags=CREATE_NO_WINDOW, timeout=CMD_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return -1, str(e)
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    # 中文系统上 schtasks 输出是 GBK，这里只用来判断成败/打日志，不做解析。
    # errors="replace" 很关键：一旦解释器按 UTF-8 解（PYTHONUTF8=1，或者系统开了
    # 「Beta: 使用 UTF-8 提供全球语言支持」），GBK 输出会抛 UnicodeDecodeError，
    # 而那不是 OSError/SubprocessError，会直接穿透出去把开关逻辑打断。
    return proc.returncode, out


def task_exists() -> bool:
    code, _ = _run(["schtasks", "/Query", "/TN", TASK_NAME])
    return code == 0


def describe_task() -> str:
    """任务的任务行 / 运行级别，写日志用（拿不到就返回空串）"""
    code, out = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/V", "/FO", "LIST"])
    if code != 0:
        return ""
    keep = ("task to run", "run as user", "logon mode", "schedule type", "run level",
            "要运行的任务", "运行方式", "登录模式", "计划类型", "以最高权限")
    lines = [ln.strip() for ln in out.splitlines()
             if any(k in ln.lower() for k in keep)]
    return " | ".join(lines)


def create_task(program: str | None = None) -> tuple[bool, str]:
    """
    创建「登录时 + 最高权限」的计划任务。

    不带 /RU：默认就是当前用户、交互式令牌（只在用户登录后跑）——
    这正好让任务在用户会话里起 GUI，而且因为带 HIGHEST，**不会**再弹 UAC。
    建这个任务需要管理员权限，所以打包版（已经提权）才走这条路。

    program：要自启的程序（安装器指到装好的 exe；留空 = 当前进程/exe）。
    """
    code, out = _run([
        "schtasks", "/Create", "/TN", TASK_NAME,
        "/TR", command_line(program),
        "/SC", "ONLOGON", "/RL", "HIGHEST", "/F",
    ])
    return code == 0, out


def delete_task(name: str | None = None) -> tuple[bool, str]:
    task = name or TASK_NAME
    code, _ = _run(["schtasks", "/Query", "/TN", task])
    if code != 0:
        return True, "任务不存在"
    code, out = _run(["schtasks", "/Delete", "/TN", task, "/F"])
    return code == 0, out


# ---------------------------------------------------------
# 注册表 Run 键
# ---------------------------------------------------------
def run_key_set(enable: bool, program: str | None = None) -> tuple[bool, str]:
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                             winreg.KEY_SET_VALUE)
    except OSError as e:
        return False, f"打开 Run 键失败：{e}"
    try:
        if enable:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ,
                              command_line(program))
            return True, "已写入 Run 键"
        try:
            winreg.DeleteValue(key, RUN_VALUE)
            return True, "已清除 Run 键"
        except FileNotFoundError:
            return True, "Run 键本来就没有"
    except OSError as e:
        return False, f"写 Run 键失败：{e}"
    finally:
        try:
            winreg.CloseKey(key)
        except OSError:
            pass


def run_key_value() -> str:
    """Run 键里当前的值（没有就返回空串）"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_QUERY_VALUE) as key:
            value, _ = winreg.QueryValueEx(key, RUN_VALUE)
            return str(value)
    except OSError:
        return ""


# ---------------------------------------------------------
# 对外入口
# ---------------------------------------------------------
def apply(enable: bool, prefer_task: bool | None = None,
          program: str | None = None) -> str:
    """
    打开/关闭开机自启，返回一句人话说明（同时写日志）。绝不抛异常。

    prefer_task 默认：打包版用计划任务，源码版用 Run 键。
    program：自启指向的程序（安装器用来指向装好的 exe；留空 = 当前进程/exe）。
    """
    if prefer_task is None:
        prefer_task = bool(getattr(sys, "frozen", False))

    log = applog.get("autostart")
    notes: list[str] = []

    try:
        if enable:
            done = False
            if prefer_task:
                ok, out = create_task(program)
                if ok:
                    notes.append(f"已创建计划任务 {TASK_NAME}（登录时以最高权限运行）")
                    detail = describe_task()
                    if detail:
                        notes.append(detail)
                    # 清掉旧版本/源码运行留下的 Run 键，免得起来两份
                    rok, _ = run_key_set(False)
                    if rok:
                        notes.append("已清理 Run 键")
                    done = True
                else:
                    notes.append(f"创建计划任务失败（{out}），改用 Run 键")
            if not done:
                ok, out = run_key_set(True, program)
                notes.append(out if ok else f"Run 键失败：{out}")
        else:
            ok, out = delete_task()
            notes.append(f"计划任务：{'已删除' if ok else out}")
            ok2, out2 = run_key_set(False)
            notes.append(f"Run 键：{'已清除' if ok2 else out2}")
    except Exception as e:                      # noqa: BLE001 - 自启失败不该影响使用
        log.exception("设置开机自启出错（enable=%s）", enable)
        return f"出错：{type(e).__name__}: {e}"

    msg = "；".join(notes)
    log.info("开机自启 %s：%s", "开启" if enable else "关闭", msg)
    return msg


def status() -> str:
    """当前自启状态（自检/日志用）"""
    parts = [f"计划任务 {TASK_NAME}：{'存在' if task_exists() else '不存在'}"]
    value = run_key_value()
    parts.append(f"Run 键：{value or '无'}")
    return "；".join(parts)


def is_elevated() -> bool:
    """当前进程是否以管理员身份运行（打包版清单要求提权，这里用于核对）"""
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:                           # noqa: BLE001 - 非 Windows / 调用失败
        return False
