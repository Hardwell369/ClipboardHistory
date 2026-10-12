# core/shortcut.py
"""
Windows 快捷方式（.lnk）的创建与删除。

两种实现，按可用性挑：
1. pywin32 的 WScript.Shell COM —— 项目本来就依赖 pywin32，最省事；
2. 退到 PowerShell 的 WScript.Shell COM —— 打包时万一 win32com 的动态模块
   没被收集全，这条路仍然可用（不额外依赖任何东西）。

PowerShell 那条路走 -EncodedCommand（Base64 的 UTF-16LE）：
中文路径、空格、引号都不用转义，也不会踩 PowerShell 5.1 把「无 BOM 的
UTF-8 脚本」按 GBK 解码导致中文变乱码的老问题。

所有函数都不抛异常，返回 (是否成功, 说明)，由调用方决定是记日志还是提示用户。
"""
from __future__ import annotations

import base64
import os
import subprocess

from core import applog

CREATE_NO_WINDOW = 0x08000000
PS_TIMEOUT_S = 60


# ---------------------------------------------------------
# 路径
# ---------------------------------------------------------
def default_desktop_dir() -> str:
    """桌面目录（用户自己改过桌面位置时也能跟着走）"""
    return _known_folder(r"Desktop") or os.path.join(
        os.environ.get("USERPROFILE") or os.path.expanduser("~"), "Desktop")


def default_start_menu_dir() -> str:
    """当前用户的「开始菜单\\程序」目录"""
    known = _known_folder("Programs")
    if known:
        return known
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs")


def _known_folder(name: str) -> str:
    """用 SHGetKnownFolderPath 取标准目录；失败返回空串（非 Windows / 调用失败）"""
    try:
        import ctypes

        # FOLDERID_Desktop / FOLDERID_Programs 的 GUID
        guids = {
            "Desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
            "Programs": "{A77F5D77-2E2B-44C3-A6A2-ABA601054A51}",
        }
        guid = guids.get(name)
        if not guid:
            return ""

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                        ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

        from uuid import UUID
        u = UUID(guid.strip("{}"))
        g = GUID(u.time_low, u.time_mid, u.time_hi_version,
                 (ctypes.c_ubyte * 8)(*u.bytes[8:]))
        out = ctypes.c_wchar_p()
        shell32 = ctypes.windll.shell32
        ole32 = ctypes.windll.ole32
        if shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None,
                                        ctypes.byref(out)) != 0:
            return ""
        try:
            return str(out.value or "")
        finally:
            ole32.CoTaskMemFree(out)
    except Exception:                           # noqa: BLE001 - 取不到就用环境变量兜底
        return ""


# ---------------------------------------------------------
# 创建 / 删除
# ---------------------------------------------------------
def create_shortcut(link_path: str, target: str, arguments: str = "",
                    working_dir: str = "", icon: str = "",
                    description: str = "") -> tuple[bool, str]:
    """创建 .lnk，返回 (是否成功, 说明)"""
    log = applog.get("shortcut")
    if not target:
        return False, "目标程序为空"

    try:
        os.makedirs(os.path.dirname(link_path), exist_ok=True)
    except OSError as e:
        return False, f"创建快捷方式目录失败：{e}"

    ok, detail = _with_pywin32(link_path, target, arguments, working_dir,
                               icon, description)
    if ok:
        log.info("已创建快捷方式：%s -> %s", link_path, target)
        return True, detail

    ok2, detail2 = _with_powershell(link_path, target, arguments, working_dir,
                                    icon, description)
    if ok2:
        log.info("已创建快捷方式（PowerShell）：%s -> %s", link_path, target)
        return True, detail2

    log.error("创建快捷方式失败：%s（pywin32：%s；PowerShell：%s）",
              link_path, detail, detail2)
    return False, f"pywin32：{detail}；PowerShell：{detail2}"


def delete_shortcut(link_path: str) -> tuple[bool, str]:
    if not link_path or not os.path.exists(link_path):
        return True, "不存在"
    try:
        os.remove(link_path)
        return True, "已删除"
    except OSError as e:
        return False, f"删除失败：{e}"


# ---------------------------------------------------------
# 实现 1：pywin32
# ---------------------------------------------------------
def _with_pywin32(link_path: str, target: str, arguments: str,
                  working_dir: str, icon: str,
                  description: str) -> tuple[bool, str]:
    try:
        import pythoncom
        from win32com.client import Dispatch
    except ImportError as e:
        return False, f"pywin32 不可用：{e}"

    try:
        pythoncom.CoInitialize()
    except Exception:                           # noqa: BLE001 - 已初始化过就算了
        pass
    try:
        shell = Dispatch("WScript.Shell")
        link = shell.CreateShortCut(link_path)
        link.TargetPath = target
        link.Arguments = arguments or ""
        link.WorkingDirectory = working_dir or os.path.dirname(target)
        link.Description = description or ""
        if icon:
            link.IconLocation = icon
        link.Save()
        return True, "已用 pywin32 创建"
    except Exception as e:                      # noqa: BLE001 - 换 PowerShell 再试
        return False, f"{type(e).__name__}: {e}"
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:                       # noqa: BLE001
            pass


# ---------------------------------------------------------
# 实现 2：PowerShell
# ---------------------------------------------------------
def _ps_literal(value: str) -> str:
    """PowerShell 单引号字符串：内部的 ' 要写两遍"""
    return "'" + str(value).replace("'", "''") + "'"


def _encoded_command(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _clean_ps_output(text: str) -> str:
    """
    只留最后一行有效信息。

    PowerShell 的 stderr 里经常夹着 CLIXML 形式的进度记录（#< CLIXML / <Objs ...>），
    直接塞进日志和提示框里没人看得懂。
    """
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    lines = [ln for ln in lines
             if not ln.startswith("#< CLIXML") and not ln.startswith("<Objs")]
    return (lines[-1] if lines else "")[:300]


def _with_powershell(link_path: str, target: str, arguments: str,
                     working_dir: str, icon: str,
                     description: str) -> tuple[bool, str]:
    lines = [
        "$ErrorActionPreference = 'Stop'",
        "$ProgressPreference = 'SilentlyContinue'",
        "$shell = New-Object -ComObject WScript.Shell",
        f"$link = $shell.CreateShortcut({_ps_literal(link_path)})",
        f"$link.TargetPath = {_ps_literal(target)}",
        f"$link.Arguments = {_ps_literal(arguments or '')}",
        f"$link.WorkingDirectory = {_ps_literal(working_dir or os.path.dirname(target))}",
        f"$link.Description = {_ps_literal(description or '')}",
    ]
    if icon:
        lines.append(f"$link.IconLocation = {_ps_literal(icon)}")
    lines.append("$link.Save()")
    script = "; ".join(lines)

    exe = os.path.join(os.environ.get("SystemRoot") or r"C:\Windows",
                       "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    if not os.path.isfile(exe):
        exe = "powershell.exe"
    try:
        proc = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-OutputFormat", "Text",
             "-EncodedCommand", _encoded_command(script)],
            capture_output=True, text=True, errors="replace", timeout=PS_TIMEOUT_S,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"PowerShell 启动失败：{e}"
    if proc.returncode != 0:
        out = _clean_ps_output((proc.stdout or "") + "\n" + (proc.stderr or ""))
        return False, f"PowerShell 退出码 {proc.returncode}：{out}"
    if not os.path.exists(link_path):
        return False, "PowerShell 返回成功但没有生成文件"
    return True, "已用 PowerShell 创建"
