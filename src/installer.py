# src/installer.py
"""
安装器核心逻辑（不含界面）。

界面（installer.py 里的向导）只负责收集选项，真正的活全在这里：

    复制程序文件 -> 写配置(settings.json) -> 写安装信息(install.json)
    -> 建桌面/开始菜单快捷方式 -> 注册「应用和功能」卸载项 -> 处理开机自启

为什么要和界面分开：
1. 每一步都必须能单独记日志、单独失败（安装失败时用户看到的只会是
   「点了没反应」，没有日志就查不出来）；
2. 这一层不依赖 Qt，可以脱离界面直接跑（也支持 --install-silent 静默安装）。

约定：所有函数都不抛异常，失败信息写进 InstallResult.errors。
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import time
import winreg
from dataclasses import dataclass, field

from src import applog
from src.settings import Settings, default_settings_path
from src.version import (
    APP_NAME, APP_NAME_EN, AUTHOR, AUTHOR_EMAIL, __version__,
)

# install.json / 卸载项与 src/uninstall.py 共用同一份定义（免得两边不一致）
from src.uninstall import INSTALL_INFO_FILE, UNINSTALLER_EXE_NAME, uninstall_key

EXE_NAME = "ClipboardHistory.exe"
SETUP_EXE_NAME = "ClipboardHistory-Setup.exe"

# 打包时 Setup.exe 把整个程序目录放在这个子目录里（见 build/installer.spec）
PAYLOAD_SUBDIR = "payload"

# 默认数据目录就是安装目录下的这个子目录（安装时自动跟着安装目录走）
DATA_SUBDIR = "data"

# 复制时跳过的东西：这些是「本机运行痕迹」或安装器自己，不该带进安装目录
SKIP_FILE_NAMES = {
    INSTALL_INFO_FILE,
    "settings.json",
    "app.log",
    "history.db",
    ".lock",
    SETUP_EXE_NAME,
    "unins000.exe",
    "unins000.dat",
}
SKIP_DIR_NAMES = {"files", "__pycache__"}


# ---------------------------------------------------------
# 选项 / 结果
# ---------------------------------------------------------
@dataclass
class InstallOptions:
    """安装选项（界面收集到的全部内容）"""

    target_dir: str
    settings: dict = field(default_factory=dict)    # 见 ui/settings_form.py 的 FORM_KEYS
    desktop_shortcut: bool = True
    start_menu_shortcut: bool = True
    auto_start: bool = False
    launch_after: bool = False
    payload_dir: str = ""                           # 源目录，留空 = 自动探测
    exe_name: str = EXE_NAME                        # 主程序文件名（测试可替换）


@dataclass
class InstallResult:
    ok: bool = False
    target_dir: str = ""
    exe_path: str = ""
    payload_dir: str = ""
    data_dir: str = ""
    settings_path: str = ""
    desktop_link: str = ""
    start_menu_link: str = ""
    copied_files: int = 0
    copied_bytes: int = 0
    auto_start_detail: str = ""
    uninstaller_path: str = ""                  # 程序目录里的 uninstaller.exe
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"安装目录：{self.target_dir}",
                 f"主程序：{self.exe_path}",
                 f"数据目录：{self.data_dir}",
                 f"配置文件：{self.settings_path}",
                 f"复制文件：{self.copied_files} 个"
                 f"（{self.copied_bytes / (1024 * 1024):.1f} MB）"]
        if self.uninstaller_path:
            lines.append(f"卸载程序：{self.uninstaller_path}")
        if self.desktop_link:
            lines.append(f"桌面快捷方式：{self.desktop_link}")
        if self.start_menu_link:
            lines.append(f"开始菜单快捷方式：{self.start_menu_link}")
        if self.auto_start_detail:
            lines.append(f"开机自启：{self.auto_start_detail}")
        for note in self.notes:
            lines.append(f"说明：{note}")
        for err in self.errors:
            lines.append(f"问题：{err}")
        return "\n".join(lines)


# ---------------------------------------------------------
# 路径
# ---------------------------------------------------------
def is_frozen() -> bool:
    """当前是不是打包后的 exe（PyInstaller 会把 sys.frozen 设为 True）"""
    return bool(getattr(sys, "frozen", False))


def program_dir() -> str:
    """安装器自身所在的目录"""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def payload_candidates() -> list[str]:
    """程序文件可能的来源目录（按优先级）"""
    out: list[str] = []
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        out.append(os.path.join(meipass, PAYLOAD_SUBDIR))
    out.append(program_dir())
    return out


def payload_dir() -> str:
    """
    找到「要安装的程序目录」。

    打包成 Setup.exe（onefile）时程序文件被塞在 _MEIPASS/payload 下；
    直接在绿色版目录里运行 --install 时，就是 exe 自己所在目录。
    判定标准：目录里要有主程序 exe。
    """
    candidates = payload_candidates()
    for path in candidates:
        if os.path.isfile(os.path.join(path, EXE_NAME)):
            return path
    for path in candidates:
        if os.path.isdir(path):
            return path
    return candidates[0]


def default_install_dir() -> str:
    """
    默认安装目录。

    有管理员权限时装到 Program Files；没有就装到当前用户的
    %LOCALAPPDATA%\\Programs（那里不需要提权，也能正常创建快捷方式）。
    已经装过的话沿用上次的位置（升级覆盖）。
    """
    previous = existing_install().get("install_dir", "")
    if previous and os.path.isdir(previous):
        return previous

    from src.autostart import is_elevated
    if is_elevated():
        base = os.environ.get("ProgramFiles") or r"C:\Program Files"
    else:
        base = os.path.join(os.environ.get("LOCALAPPDATA")
                            or os.path.expanduser("~"), "Programs")
    return os.path.join(base, APP_NAME_EN)


def data_dir_for_install(install_dir: str) -> str:
    """
    某个安装目录对应的默认数据目录：**安装目录\\data**。

    安装时数据目录默认跟着安装目录走（用户改了安装位置，存储路径就跟着变），
    没有这个文件夹就创建出来。用户也可以在向导里改成别的路径。
    """
    return os.path.join(os.path.abspath(install_dir), DATA_SUBDIR)


def default_data_dir() -> str:
    """
    兜底的数据目录：%APPDATA%\\ClipboardHistory。

    只在「既没配存储路径、又不知道安装目录」时用（例如绿色版直接跑，
    或者源码运行）；正常安装走 data_dir_for_install()。
    """
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "ClipboardHistory")


def resolve_data_dir(settings: dict, target_dir: str = "") -> str:
    """
    按设置项推导实际数据目录：

    1. settings 里填了 storage_dir -> 用它；
    2. 没填但有安装目录 -> 安装目录\\data（安装时的默认）；
    3. 都没有 -> %APPDATA%\\ClipboardHistory。
    """
    path = str((settings or {}).get("storage_dir") or "").strip()
    if path:
        return path
    if target_dir:
        return data_dir_for_install(target_dir)
    return default_data_dir()


# ---------------------------------------------------------
# 已有安装
# ---------------------------------------------------------
def _registry_value(name: str) -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, uninstall_key(), 0,
                            winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, name)
            return str(value)
    except OSError:
        return ""


def existing_install() -> dict:
    """已安装信息：注册表优先，其次 exe 旁边的 install.json（绿色版）"""
    info: dict = {}
    try:
        from src.uninstall import load_install_info
        info.update({k: v for k, v in load_install_info().items() if v})
    except Exception:                           # noqa: BLE001 - 读不到就当没装过
        pass

    install_location = _registry_value("InstallLocation")
    if install_location and os.path.isdir(install_location):
        info["install_dir"] = install_location
    version = _registry_value("DisplayVersion")
    if version:
        info["version"] = version
    return info


def is_installed() -> bool:
    info = existing_install()
    install_dir = info.get("install_dir", "")
    if install_dir and os.path.isfile(os.path.join(install_dir, EXE_NAME)):
        return True
    return bool(_registry_value("UninstallString"))


# ---------------------------------------------------------
# 复制文件
# ---------------------------------------------------------
def _should_skip(name: str) -> bool:
    if name in SKIP_FILE_NAMES or name in SKIP_DIR_NAMES:
        return True
    if name.startswith("app.log."):             # 轮转出来的日志
        return True
    return name.endswith(".tmp")


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def is_file_locked(path: str) -> bool:
    """
    文件是否被别的进程占着（正在运行的 exe 是锁着的）。

    覆盖安装最常见的失败原因就是「程序还开着」：exe 被占用时复制会失败，
    与其让用户看到一个 PermissionError，不如提前给出人话提示。
    """
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "r+b"):
            return False
    except OSError:
        return True


def _collect_files(src: str) -> list[tuple[str, str, int]]:
    """列出要复制的 (源文件, 相对路径, 大小)"""
    out: list[tuple[str, str, int]] = []
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if not _should_skip(d)]
        for name in files:
            if _should_skip(name):
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, src)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            out.append((full, rel, size))
    return out


def _copy_payload(src: str, dst: str, result: InstallResult,
                  progress=None) -> bool:
    """把程序目录复制到安装目录；返回是否成功"""
    log = applog.get("installer")

    if _same_path(src, dst):
        result.notes.append("目标目录就是当前程序目录，跳过文件复制（就地安装）")
        result.copied_files = 0
        return True

    files = _collect_files(src)
    total = max(1, len(files))
    for index, (full, rel, size) in enumerate(files):
        target = os.path.join(dst, rel)
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(full, target)
        except OSError as e:
            msg = f"复制文件失败：{rel}（{e}）"
            log.error("%s", msg)
            result.errors.append(msg)
            return False
        result.copied_files += 1
        result.copied_bytes += size
        if progress is not None and (index % 20 == 0 or index == len(files) - 1):
            # 复制占安装过程的大部分时间，进度条主要看这里
            progress(int(5 + 73 * (index + 1) / total),
                     f"正在复制文件…（{index + 1}/{len(files)}）{rel}")
    return True


# ---------------------------------------------------------
# 写配置 / 安装信息
# ---------------------------------------------------------
def _write_settings(options: InstallOptions, result: InstallResult,
                    progress=None) -> None:
    """
    把向导里的设置写进 %APPDATA%\\ClipboardHistory\\settings.json。

    用 Settings 类写：它会与已有配置合并（保住窗口位置等内部字段），
    写不进去还会自动退到数据目录里，并保证不抛异常。
    数据目录（默认 <安装目录>\\data）在这里顺手创建出来。
    """
    log = applog.get("installer")
    if progress is not None:
        progress(80, "正在写入配置…")

    # 顺手把数据目录建出来：用户装完就能在资源管理器里看到它
    try:
        os.makedirs(result.data_dir, exist_ok=True)
    except OSError as e:
        result.errors.append(f"创建数据目录失败：{result.data_dir}（{e}）")

    path = default_settings_path()
    result.settings_path = path
    try:
        settings = Settings(path)
        settings.update(options.settings or {})
        if not settings.save():
            msg = f"配置写入失败：{path}"
            log.error("%s", msg)
            result.errors.append(msg)
            return
        log.info("配置已写入：%s", path)
    except Exception as e:                      # noqa: BLE001 - 安装不该被配置拖死
        log.exception("写配置出错")
        result.errors.append(f"写配置出错：{type(e).__name__}: {e}")


def _write_install_info(options: InstallOptions, result: InstallResult,
                        progress=None) -> None:
    """写 install.json：卸载程序按它删干净（键名与 src/uninstall.py 对齐）"""
    import json

    if progress is not None:
        progress(88, "正在写入安装信息…")

    from src.autostart import RUN_VALUE, TASK_NAME
    info = {
        "app_name": APP_NAME,
        "app_name_en": APP_NAME_EN,
        "version": __version__,
        "publisher": AUTHOR,
        "author_email": AUTHOR_EMAIL,
        "install_dir": result.target_dir,
        "exe": result.exe_path,
        "uninstaller": result.uninstaller_path,   # 独立卸载器（设置->应用 里的卸载项）
        "data_dir": result.data_dir,
        "settings_path": result.settings_path,
        "desktop_link": result.desktop_link,
        "start_menu_link": result.start_menu_link,
        "auto_start_value": RUN_VALUE,          # 卸载时顺带清 Run 键
        "auto_start_task": TASK_NAME,           # 卸载时顺带删计划任务
        "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": result.payload_dir,
    }
    path = os.path.join(result.target_dir, INSTALL_INFO_FILE)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2)
        applog.get("installer").info("安装信息已写入：%s", path)
    except OSError as e:
        msg = f"安装信息写入失败：{path}（{e}）"
        applog.get("installer").error("%s", msg)
        result.errors.append(msg)


# ---------------------------------------------------------
# 快捷方式
# ---------------------------------------------------------
def _make_shortcuts(options: InstallOptions, result: InstallResult,
                    progress=None) -> None:
    from src.shortcut import (
        create_shortcut, default_desktop_dir, default_start_menu_dir,
    )

    if progress is not None:
        progress(85, "正在创建快捷方式…")

    icon = result.exe_path
    if options.desktop_shortcut:
        link = os.path.join(default_desktop_dir(), f"{APP_NAME}.lnk")
        ok, detail = create_shortcut(
            link, result.exe_path, working_dir=result.target_dir,
            icon=icon, description=f"{APP_NAME} {__version__}")
        if ok:
            result.desktop_link = link
        else:
            result.errors.append(f"创建桌面快捷方式失败：{detail}")

    if options.start_menu_shortcut:
        link = os.path.join(default_start_menu_dir(), f"{APP_NAME}.lnk")
        ok, detail = create_shortcut(
            link, result.exe_path, working_dir=result.target_dir,
            icon=icon, description=f"{APP_NAME} {__version__}")
        if ok:
            result.start_menu_link = link
        else:
            result.errors.append(f"创建开始菜单快捷方式失败：{detail}")


# ---------------------------------------------------------
# 注册「应用和功能」里的卸载项
# ---------------------------------------------------------
def _register_uninstall(options: InstallOptions, result: InstallResult,
                        progress=None) -> None:
    """
    写 HKCU\\...\\Uninstall\\ClipboardHistory。

    用 HKCU 而不是 HKLM：src/uninstall.py 删的就是 HKCU 这一份，
    两边必须一致；而且 HKCU 的卸载项会显示在当前用户的
    「设置 -> 应用 -> 已安装的应用」里，按用户安装/卸载更符合本程序的使用方式。

    卸载项指向程序目录里的 uninstaller.exe（独立卸载器，可以分别选择删除
    文本 / 图片 / 文件）；万一那个文件不在（只打了主程序），退回
    「主程序 --uninstall」的老方式。
    """
    if progress is not None:
        progress(92, "正在注册卸载信息…")

    log = applog.get("installer")
    key_path = uninstall_key()
    try:
        key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0,
                                 winreg.KEY_SET_VALUE)
    except OSError as e:
        msg = f"注册卸载信息失败：{e}"
        log.error("%s", msg)
        result.errors.append(msg)
        return

    uninstaller = result.uninstaller_path or os.path.join(
        result.target_dir, UNINSTALLER_EXE_NAME)
    if os.path.isfile(uninstaller):
        uninstall_string = f'"{uninstaller}"'
        quiet_string = f'"{uninstaller}" --silent'
        result.uninstaller_path = uninstaller
    else:
        log.warning("安装目录里没有 %s，卸载项退回主程序 --uninstall",
                    UNINSTALLER_EXE_NAME)
        result.notes.append(
            f"安装包里没有卸载器 {UNINSTALLER_EXE_NAME}，"
            f"卸载将使用主程序内置流程（只提供「删除全部数据 / 保留数据」）")
        uninstall_string = f'"{result.exe_path}" --uninstall'
        quiet_string = f'"{result.exe_path}" --uninstall-now --pid=0'

    values = {
        "DisplayName": APP_NAME,
        "DisplayVersion": __version__,
        "Publisher": AUTHOR,
        "DisplayIcon": result.exe_path,
        "InstallLocation": result.target_dir,
        "UninstallString": uninstall_string,
        "QuietUninstallString": quiet_string,
        "NoModify": 1,
        "NoRepair": 1,
        "EstimatedSize": max(1, int(result.copied_bytes / 1024)),
    }
    try:
        for name, value in values.items():
            kind = winreg.REG_DWORD if isinstance(value, int) else winreg.REG_SZ
            winreg.SetValueEx(key, name, 0, kind, value)
        log.info("卸载信息已注册：HKCU\\%s -> %s", key_path, uninstall_string)
    except OSError as e:
        msg = f"写入卸载信息失败：{e}"
        log.error("%s", msg)
        result.errors.append(msg)
    finally:
        try:
            winreg.CloseKey(key)
        except OSError:
            pass


# ---------------------------------------------------------
# 开机自启
# ---------------------------------------------------------
def _apply_auto_start(options: InstallOptions, result: InstallResult,
                      progress=None) -> None:
    if progress is not None:
        progress(96, "正在设置开机自启…")

    from src.autostart import apply as apply_autostart
    try:
        # 安装器统一用「登录时 + 最高权限」的计划任务：安装后的 exe 需要管理员权限，
        # Run 键拉起的进程没有提权，会被安全软件拦成残废实例（详见 src/autostart.py）
        detail = apply_autostart(bool(options.auto_start), prefer_task=True,
                                 program=result.exe_path)
        result.auto_start_detail = detail
    except Exception as e:                      # noqa: BLE001 - 自启失败不影响安装
        applog.get("installer").exception("设置开机自启出错")
        result.errors.append(f"设置开机自启出错：{type(e).__name__}: {e}")


# ---------------------------------------------------------
# 主流程
# ---------------------------------------------------------
def install(options: InstallOptions, progress=None) -> InstallResult:
    """
    执行安装。progress(percent:int, message:str) 用于界面进度条。

    返回 InstallResult：ok 为 False 表示**程序文件没能装好**（致命）；
    其余步骤（快捷方式、自启、卸载项）失败只记 errors，安装本身仍然算成功——
    主程序能用才是第一位的。
    """
    log = applog.get("installer")
    result = InstallResult(target_dir=os.path.abspath(options.target_dir or ""))
    if not result.target_dir:
        result.errors.append("没有指定安装目录")
        return result

    src = options.payload_dir or payload_dir()
    result.payload_dir = src
    result.exe_path = os.path.join(result.target_dir, options.exe_name)
    result.data_dir = resolve_data_dir(options.settings, result.target_dir)
    # 把「最后算出来的数据目录」显式写进配置：否则静默安装时配置里留空，
    # 运行时又会按默认位置去找，出现「安装报告说 A、程序实际用 B」
    options.settings = dict(options.settings or {})
    options.settings["storage_dir"] = result.data_dir

    log.info("======== 开始安装：源=%s 目标=%s 数据=%s ========",
             src, result.target_dir, result.data_dir)

    def report(percent: int, message: str) -> None:
        if progress is not None:
            progress(max(0, min(100, percent)), message)

    # ---- 1. 目标目录 ----
    report(2, "正在准备安装目录…")
    try:
        os.makedirs(result.target_dir, exist_ok=True)
    except OSError as e:
        msg = f"无法创建安装目录：{result.target_dir}（{e}）"
        log.error("%s", msg)
        result.errors.append(msg)
        return result

    # ---- 2. 复制程序文件 ----
    if not os.path.isdir(src):
        msg = f"找不到要安装的程序文件：{src}"
        log.error("%s", msg)
        result.errors.append(msg)
        return result
    if not os.path.isfile(os.path.join(src, options.exe_name)):
        # 源码运行时会出现这种情况：这一层是「安装打包好的程序」，
        # 不能把整个源码目录（含 .venv、缓存）复制到安装目录去
        msg = (f"源目录里没有主程序 {options.exe_name}：{src}\n"
               f"（当前是源码运行，请先用 PyInstaller 打包，"
               f"再运行打包产物里的 ClipboardHistory.exe --install）")
        log.error("%s", msg.replace("\n", " "))
        result.errors.append(msg)
        return result

    # 覆盖安装时程序还开着 -> exe 被占用，复制必然失败，提前说清楚
    if is_file_locked(result.exe_path):
        msg = (f"安装目录里的程序正在运行，无法覆盖：\n{result.exe_path}\n\n"
               f"请先退出程序（托盘图标右键 -> 退出）再安装。")
        log.error("安装目录里的程序正在运行：%s", result.exe_path)
        result.errors.append(msg)
        return result

    if not _copy_payload(src, result.target_dir, result, progress=report):
        return result                                  # 致命：复制失败就别往下走了

    # 卸载器是 payload 的一部分，复制完就能确定它在不在。
    # 必须在这里就定下来：install.json（_write_install_info）先写、注册表
    # （_register_uninstall）后写，以前是注册表那一步才赋值，结果 install.json
    # 里的 "uninstaller" 永远是空串。
    candidate = os.path.join(result.target_dir, UNINSTALLER_EXE_NAME)
    if os.path.isfile(candidate):
        result.uninstaller_path = candidate

    # ---- 3. 配置 ----
    _write_settings(options, result, progress=report)

    # ---- 4. 安装信息（要放在快捷方式之后才知道链接路径，所以先建链接）----
    _make_shortcuts(options, result, progress=report)
    _write_install_info(options, result, progress=report)
    _register_uninstall(options, result, progress=report)
    _apply_auto_start(options, result, progress=report)

    report(100, "安装完成")
    result.ok = os.path.isfile(result.exe_path)
    if not result.ok:
        result.errors.append(f"安装目录里没有找到主程序：{result.exe_path}")

    log.info("======== 安装结束：ok=%s 文件=%d 错误=%d ========",
             result.ok, result.copied_files, len(result.errors))
    for line in result.errors:
        log.error("安装问题：%s", line)
    return result


# ---------------------------------------------------------
# 安装后启动程序
# ---------------------------------------------------------
def launch_installed(exe_path: str, workdir: str = "") -> tuple[bool, str]:
    """
    启动装好的程序。

    注意：主程序清单要求管理员权限，**非提权进程直接 CreateProcess 会失败
    （WinError 740）**，所以失败时改用 ShellExecuteW("runas") 让系统弹 UAC。
    安装器自己是提权的，正常情况走第一条路，用户不会再看到 UAC。
    """
    log = applog.get("installer")
    if not os.path.isfile(exe_path):
        return False, f"找不到程序：{exe_path}"
    cwd = workdir or os.path.dirname(exe_path)

    try:
        subprocess.Popen([exe_path], cwd=cwd, close_fds=True)
        log.info("已启动装好的程序：%s", exe_path)
        return True, "已启动"
    except OSError as e:
        log.warning("直接启动失败（%s），改用 ShellExecute runas", e)

    try:
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", exe_path, None, cwd, 1)
        if int(rc) > 32:
            return True, "已通过 UAC 启动"
        return False, f"ShellExecuteW 返回 {rc}"
    except Exception as e:                      # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


# ---------------------------------------------------------
# 静默安装（命令行，供自动化/批量部署使用）
# ---------------------------------------------------------
def parse_silent_args(argv: list[str]) -> InstallOptions:
    """
    解析 --install-silent 的参数：

        --dir=安装目录        --data-dir=数据目录（默认 <安装目录>\\data）
        --no-desktop          不建桌面快捷方式
        --no-start-menu       不建开始菜单快捷方式
        --auto-start=1|0      开机自启（默认关）
        --launch=1|0          装完是否启动（默认关）
        --max-items=300       其余设置项按需覆盖（见下面 SETTING_ARGS）

    没给 --dir 时用默认安装目录（有管理员权限 = Program Files）；
    没给 --data-dir 时数据目录 = <安装目录>\\data。
    """
    from src.settings import DEFAULT_SETTINGS

    args: dict[str, str] = {}
    flags: set[str] = set()
    for arg in argv:
        if not arg.startswith("--"):
            continue
        body = arg[2:]
        if "=" in body:
            key, value = body.split("=", 1)
            args[key.lower()] = value
        else:
            # 不带值的开关，例如 --no-desktop / --no-start-menu
            flags.add(body.lower())

    settings = dict(DEFAULT_SETTINGS)
    # 与设置项同名的参数直接覆盖（键名用短横线形式，例如 --max-items=500）
    for key in DEFAULT_SETTINGS:
        short = key.replace("_", "-")
        if short in args:
            settings[key] = _coerce(DEFAULT_SETTINGS[key], args[short])

    target = args.get("dir") or default_install_dir()
    if "data-dir" in args:
        settings["storage_dir"] = args["data-dir"]

    return InstallOptions(
        target_dir=target,
        settings=settings,
        desktop_shortcut="no-desktop" not in flags,
        start_menu_shortcut="no-start-menu" not in flags,
        auto_start=_flag(args.get("auto-start", "0")),
        launch_after=_flag(args.get("launch", "0")),
    )


def _flag(value: str) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on", "y")


def _coerce(default, value: str):
    if isinstance(default, bool):
        return _flag(value)
    if isinstance(default, int):
        try:
            return int(value)
        except ValueError:
            return default
    return value
