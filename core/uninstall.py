# core/uninstall.py
"""
卸载支持。

三条入口，真正的活都在 run_uninstall_job() 里：

    uninstaller.exe                    独立卸载器（安装后就在程序目录里，推荐）
    ClipboardHistory.exe --uninstall   绿色版 / 老版本用；装了卸载器时会转交给它
    ClipboardHistory.exe --uninstall-now --pid=N    真正干活的副本进程（内部用）

为什么要派生一个副本：
正在运行的 exe 自己删不掉自己（文件被占用）。所以确认之后把自身复制到 TEMP，
用 DETACHED_PROCESS 起一个不受父进程影响的副本，本进程立刻退出；
副本等本进程（以及正在运行的主程序）退出后，再删安装目录、快捷方式、
注册表项和用户选择要删的数据，最后删掉自己的临时文件。

删哪些数据由 core/data_clean.py 决定：文本、图片、文件三类分开，
用户勾哪类删哪类；没勾的绝不动（配置/日志只在数据清空后才一起删）。

安装目录里的 install.json 由安装器写入；卸载时按它找到安装目录、数据目录、
快捷方式路径；缺失时按默认布局探测，绝不因为读不到就删错东西。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import winreg

from core import applog, lockfile
from core.autostart import RUN_VALUE, TASK_NAME
from core.data_clean import (
    CATEGORY_KEYS, candidate_dirs, delete_data, format_size, inspect,
    remove_session_files, summarize,
)
from core.version import APP_NAME, APP_NAME_EN

INSTALL_INFO_FILE = "install.json"
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_NAME_EN}"

UNINSTALLER_EXE_NAME = "uninstaller.exe"
MAIN_EXE_NAME = "ClipboardHistory.exe"
SETUP_EXE_NAME = "ClipboardHistory-Setup.exe"

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000

MB_OK = 0x00000000
MB_YESNO = 0x00000004
MB_ICONQUESTION = 0x00000020
MB_ICONINFORMATION = 0x00000040
MB_ICONWARNING = 0x00000030
MB_ICONERROR = 0x00000010
IDYES = 6


# ---------------------------------------------------------
# 小工具
# ---------------------------------------------------------
def _msgbox(text: str, title: str, flags: int) -> int:
    """原生消息框（此时代码跑在 Qt 之外，用不了 QMessageBox）"""
    try:
        user32 = ctypes.windll.user32
        user32.MessageBoxW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.UINT]
        user32.MessageBoxW.restype = ctypes.c_int
        return int(user32.MessageBoxW(None, text, title, flags))
    except Exception:                       # noqa: BLE001
        return IDYES


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def uninstall_key() -> str:
    """
    「应用和功能」里的卸载项路径。

    允许用环境变量覆盖：自动化测试要跑多组互不干扰的用例，
    绝不能碰到用户真实安装的那一项。
    """
    return (os.environ.get("CLIPBOARD_UNINSTALL_KEY") or "").strip() or UNINSTALL_KEY


def program_and_args() -> tuple[str, list[str]]:
    """
    返回 (可执行文件, 追加参数)：用它来派生卸载副本。

    - 打包后：复制自身 exe（PyInstaller onefile 的 exe 是自包含的，拷到哪都能跑）
    - 源码运行：拿 python 解释器跑本项目的 main.py
    """
    if is_frozen():
        return sys.executable, []
    return sys.executable, [os.path.abspath(
        os.path.join(os.path.dirname(__file__), os.pardir, "main.py"))]


def install_dir() -> str:
    """当前安装目录（打包后就是 exe 所在目录）"""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def find_uninstaller(directory: str = "") -> str:
    """安装目录里的 uninstaller.exe（没有就返回空串）"""
    base = directory or install_dir()
    path = os.path.join(base, UNINSTALLER_EXE_NAME)
    return path if os.path.isfile(path) else ""


def load_install_info(install_dir_path: str = "") -> dict:
    path = os.path.join(install_dir_path or install_dir(), INSTALL_INFO_FILE)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def default_data_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "ClipboardHistory")


def run_key_value() -> str:
    """开机自启在 HKCU\\...\\Run 里的值名"""
    return RUN_VALUE


# ---------------------------------------------------------
# 注册表 / 快捷方式 / 自启
# ---------------------------------------------------------
def _remove_registry(key_path: str, value_name: str | None = None) -> None:
    try:
        if value_name is None:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
        else:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                                winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, value_name)
    except OSError:
        pass


def _remove_tree(path: str) -> None:
    if os.path.isdir(path):
        shutil.rmtree(path, onerror=_on_rm_error)
    elif os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


def _on_rm_error(func, path, exc_info):
    """只读文件/目录先去掉只读再删"""
    try:
        os.chmod(path, 0o700)
        func(path)
    except OSError:
        pass


def _remove_file(path: str) -> None:
    if path and os.path.exists(path):
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass
        _remove_tree(path)
        # 快捷方式可能被固定到任务栏/开始菜单，删不掉就算了
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


def _remove_auto_start(args: dict, report: dict, say) -> None:
    """删掉开机自启：Run 键值 + 「登录时以最高权限运行」计划任务"""
    if args.get("keep-autostart"):
        return

    value = (args.get("auto-start-value")
             or load_install_info(args.get("install-dir", "")).get("auto_start_value")
             or run_key_value())
    if value:
        _remove_registry(r"Software\Microsoft\Windows\CurrentVersion\Run", value)
        say(f"已清理注册表启动项：{value}")

    task = (args.get("auto-start-task")
            or load_install_info(args.get("install-dir", "")).get("auto_start_task")
            or TASK_NAME)
    try:
        from core.autostart import delete_task

        ok, detail = delete_task(task)
        say(f"开机自启计划任务 {task}：{'已删除' if ok else '未删除'}（{detail}）")
        if not ok:
            report["warnings"].append(f"计划任务 {task} 未删除：{detail}")
    except Exception as e:                      # noqa: BLE001 - 卸载不该被它挡住
        report["warnings"].append(f"删除自启计划任务出错：{type(e).__name__}: {e}")


def _default_links() -> tuple[str, str]:
    """按默认布局猜快捷方式位置（install.json 缺失时的兜底）"""
    try:
        from core.shortcut import default_desktop_dir, default_start_menu_dir

        desktop = os.path.join(default_desktop_dir(), f"{APP_NAME}.lnk")
        start_menu = os.path.join(default_start_menu_dir(), f"{APP_NAME}.lnk")
        return desktop, start_menu
    except Exception:                           # noqa: BLE001
        home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
        appdata = os.environ.get("APPDATA") or home
        return (os.path.join(home, "Desktop", f"{APP_NAME}.lnk"),
                os.path.join(appdata, "Microsoft", "Windows", "Start Menu",
                             "Programs", f"{APP_NAME}.lnk"))


def _remove_shortcuts(args: dict, report: dict, say) -> None:
    if args.get("keep-shortcuts"):
        return
    info = load_install_info(args.get("install-dir", ""))
    desktop_default, start_default = _default_links()
    links = [
        args.get("desktop-link") or info.get("desktop_link") or desktop_default,
        args.get("start-menu-link") or info.get("start_menu_link") or start_default,
    ]
    removed = 0
    for link in links:
        if link and os.path.exists(link):
            _remove_file(link)
            if not os.path.exists(link):
                removed += 1
                say(f"已删除快捷方式：{link}")
            else:
                report["warnings"].append(f"快捷方式删不掉（可能被固定到任务栏）：{link}")
    if removed == 0:
        say("没有找到需要删除的快捷方式")


def _taskkill_main_process(exe_name: str, report: dict, say) -> None:
    """实在叫不动就结束进程（卸载器是提权的，能在用户会话里结束它）"""
    system_root = os.environ.get("SystemRoot") or r"C:\Windows"
    taskkill = os.path.join(system_root, "System32", "taskkill.exe")
    exe = taskkill if os.path.isfile(taskkill) else "taskkill"
    try:
        proc = subprocess.run(
            [exe, "/IM", exe_name, "/F"],
            creationflags=CREATE_NO_WINDOW, capture_output=True, text=True,
            errors="replace", timeout=20)
        say(f"结束进程 {exe_name}：退出码 {proc.returncode}")
    except Exception as e:                      # noqa: BLE001
        report["warnings"].append(f"结束进程失败：{type(e).__name__}: {e}")


def _close_running_app(args: dict, report: dict, say) -> None:
    """
    卸载前把正在运行的程序关掉。

    优先「优雅退出」：往命令文件里写 quit（core/single_instance.py 的
    quit_requested 会走正常的释放流程）；叫不动才 taskkill。
    程序不关掉的话：安装目录里的 exe 被占用、history.db 也写不进去。
    """
    if args.get("no-close-running"):
        return
    if not lockfile.is_running():
        say("程序当前没有在运行")
        return

    say("检测到程序正在运行，正在请求它退出…")
    lockfile.write_command(lockfile.CMD_QUIT)
    if lockfile.wait_until_free(6.0):
        say("程序已退出")
        return

    say("程序没有响应退出请求，正在结束进程…")
    _taskkill_main_process(args.get("exe-name") or MAIN_EXE_NAME, report, say)
    if not lockfile.wait_until_free(3.0):
        report["warnings"].append("程序似乎仍在运行，数据可能删不干净")


# ---------------------------------------------------------
# 派生副本 / 报告
# ---------------------------------------------------------
def _wait_for_pid(pid: int, timeout_s: float = 30.0) -> None:
    """等主进程退出：它退出后安装目录里的文件才不会被占用"""
    if pid <= 0:
        return
    kernel32 = ctypes.windll.kernel32
    SYNCHRONIZE = 0x00100000
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        time.sleep(1.0)
        return
    try:
        kernel32.WaitForSingleObject(handle, int(timeout_s * 1000))
    finally:
        kernel32.CloseHandle(handle)
    time.sleep(0.6)                          # 再给文件系统一点时间收句柄


def spawn_job_copy(argv: list[str], program: str = "",
                   add_tmpdir_arg: bool = True,
                   script: str = "") -> tuple[bool, str]:
    """
    派生「真正干活」的副本进程。

    - 打包后：把自身 exe 复制到 TEMP 再启动（正在运行的 exe 删不掉自己，
      所以必须换一个位置跑），返回 (True, 临时目录)；
    - 源码运行：直接用同一个解释器跑脚本，**不复制**（复制出来的 python.exe
      找不到标准库），返回 (True, "")。

    参数：
        program        指定要复制的程序（默认复制自身）
        add_tmpdir_arg 是否自动追加 --tmpdir=（副本用它清理自己）
        script         源码运行时跑哪个脚本（默认 main.py）
    """
    if is_frozen() or program:
        source, extra = (program, []) if program else program_and_args()
        tmpdir = tempfile.mkdtemp(prefix="clip_uninst_")
        target = os.path.join(tmpdir, os.path.basename(source))
        try:
            shutil.copy2(source, target)
        except OSError as e:
            return False, f"无法创建卸载程序：{e}"
        final = list(extra) + list(argv)
        if add_tmpdir_arg:
            final.append(f"--tmpdir={tmpdir}")
        try:
            subprocess.Popen(
                [target] + final,
                creationflags=(DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
                               | CREATE_NO_WINDOW),
                close_fds=True,
                # 工作目录不能用临时副本目录：Windows 不允许删掉进程的当前
                # 工作目录，副本最后就清不掉自己那个目录了
                cwd=tempfile.gettempdir(),
            )
        except OSError as e:
            return False, f"无法启动卸载程序：{e}"
        return True, tmpdir

    target = script or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), os.pardir, "main.py")
    if not os.path.isfile(target):
        return False, f"找不到卸载脚本：{target}"
    try:
        subprocess.Popen(
            [sys.executable, os.path.abspath(target)] + list(argv),
            creationflags=(DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
                           | CREATE_NO_WINDOW),
            close_fds=True,
        )
    except OSError as e:
        return False, f"无法启动卸载程序：{e}"
    return True, ""


def write_report(report: dict, path: str) -> None:
    """把结果写成 JSON（自动化 / 排查用；写不了就算了）"""
    if not path:
        return
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


# ---------------------------------------------------------
# 真正干活：在 TEMP 里的副本进程里执行
# ---------------------------------------------------------
def run_uninstall_job(args: dict, progress=None) -> dict:
    """
    卸载主流程。args 里的键（都来自 --key=value）：

        install-dir / data-dir（可多个，逗号分隔或重复出现）/ pid / tmpdir /
        delete=text,image,files / purge-data（旧参数，等价于三类全删）/
        keep-registry / keep-shortcuts / keep-autostart / no-close-running /
        desktop-link / start-menu-link / auto-start-value / auto-start-task /
        report（结果 JSON 路径）

    返回报告 dict（ok 表示程序文件已经删掉；数据/快捷方式的问题记在 warnings/errors）。
    """
    log = applog.get("uninstall")
    target = os.path.abspath(args.get("install-dir") or install_dir())
    data_dirs = _as_list(args.get("data-dir")) or candidate_dirs(target)
    categories = categories_from_args(args)

    report: dict = {
        "ok": False,
        "install_dir": target,
        "data_dirs": data_dirs,
        "categories": categories,
        "steps": [],
        "warnings": [],
        "errors": [],
        "deleted": {},
        "freed": 0,
        "removed_extras": [],
        "install_dir_removed": False,
        "kept_data_dirs": [],
    }

    def say(message: str) -> None:
        log.info("%s", message)
        report["steps"].append(message)
        if progress is not None:
            try:
                progress(message)
            except Exception:                   # noqa: BLE001 - 进度回调不能影响卸载
                pass

    log.info("======== 开始卸载：目录=%s 数据=%s 删除=%s ========",
             target, data_dirs, categories)

    # ---- 安全阀：源码目录一律不动 ----
    if is_source_tree(target):
        message = (f"拒绝卸载：{target} 看起来是项目源码目录"
                   f"（有 main.py / requirements.txt，却没有 {MAIN_EXE_NAME}）。\n"
                   f"卸载只针对打包后的程序目录。")
        log.error("%s", message)
        report["errors"].append(message)
        report["summary"] = build_summary(report)
        write_report(report, args.get("report") or "")
        return report

    # ---- 安全阀 2：目录里连一点本程序的痕迹都没有，也不动 ----
    # （卸载会把这个目录里的东西全删掉，所以必须先验明正身；
    #   正常安装目录里至少有主程序 exe / 卸载器 / install.json 之一）
    if not looks_like_install_dir(target):
        message = (f"拒绝卸载：{target} 里没有找到本程序的任何文件"
                   f"（{MAIN_EXE_NAME} / {UNINSTALLER_EXE_NAME} / {INSTALL_INFO_FILE}）。\n"
                   f"如果确实要清理，请手动删除这个目录，或确认安装目录是否写对了。")
        log.error("%s", message)
        report["errors"].append(message)
        report["summary"] = build_summary(report)
        write_report(report, args.get("report") or "")
        return report

    # ---- 0. 等派生自己的那个进程退出 ----
    _wait_for_pid(int(args.get("pid") or 0))

    # ---- 1. 关掉正在运行的程序 ----
    try:
        _close_running_app(args, report, say)
    except Exception as e:                      # noqa: BLE001
        report["warnings"].append(f"关闭正在运行的程序时出错：{e}")

    # ---- 2. 注册表：先摘掉「应用和功能」入口，免得删到一半用户又点一次 ----
    if not args.get("keep-registry"):
        _remove_registry(uninstall_key())
        say("已移除「应用和功能」里的卸载项")

    # ---- 3. 开机自启 ----
    try:
        _remove_auto_start(args, report, say)
    except Exception as e:                      # noqa: BLE001
        report["warnings"].append(f"清理开机自启时出错：{e}")

    # ---- 4. 快捷方式 ----
    try:
        _remove_shortcuts(args, report, say)
    except Exception as e:                      # noqa: BLE001
        report["warnings"].append(f"删除快捷方式时出错：{e}")

    # ---- 5. 数据（文本 / 图片 / 文件，按用户勾选）----
    results = []
    try:
        if categories:
            say("正在删除本机保存的数据…")
            results = delete_data(data_dirs, categories, progress=progress)
            for item in results:
                for key, count in item.deleted.items():
                    report["deleted"][key] = report["deleted"].get(key, 0) + count
                report["freed"] += item.freed
                report["removed_extras"].extend(item.removed_extras)
                report["errors"].extend(item.errors)
            say(summarize(results))
        else:
            say("按你的选择保留了本机数据（只删程序）")
        removed = remove_session_files(data_dirs)
        if removed:
            say(f"已清理锁/命令文件 {len(removed)} 个")
    except Exception as e:                      # noqa: BLE001 - 数据删不掉也要把程序卸掉
        report["errors"].append(f"清理数据时出错：{type(e).__name__}: {e}")
        say(f"清理数据时出错：{e}")

    # ---- 6. 安装目录：自己在里面，删不掉整个目录，就把内容清空 ----
    if os.path.isdir(target):
        say(f"正在删除程序文件：{target}")
        me = os.path.abspath(sys.executable)
        failed: list[str] = []
        try:
            names = os.listdir(target)
        except OSError as e:
            names = []
            report["errors"].append(f"读取安装目录失败：{e}")
        # 数据目录默认就在安装目录里（<安装目录>\data）：用户选择「保留数据」时
        # 绝不能跟着程序文件一起删掉
        keep_paths = {_norm(path) for path in data_dirs if os.path.isdir(path)}
        kept: list[str] = []
        for name in names:
            full = os.path.join(target, name)
            if os.path.abspath(full).lower() == me.lower():
                continue
            if _norm(full) in keep_paths and _has_content(full):
                kept.append(full)
                say(f"按你的选择保留数据目录：{full}")
                continue
            try:
                _remove_tree(full)
            except OSError:
                pass
            if os.path.exists(full):
                failed.append(name)
        if failed:
            # 多数是「程序还开着」导致的文件占用，逐条列出来方便排查
            report["errors"].extend(
                f"未能删除：{name}" for name in sorted(set(failed))[:12])
        report["kept_data_dirs"] = kept
        report["install_dir_removed"] = _try_rmdir(target)
        if report["install_dir_removed"]:
            say("安装目录已删除" + ("（数据目录另行保留）" if kept else ""))
        else:
            left: list[str] = []
            try:
                left = os.listdir(target)
            except OSError:
                pass
            kept_names = {os.path.basename(path) for path in kept}
            if left and set(left) <= kept_names:
                # 只剩下用户要保留的数据目录：程序文件其实已经删干净了
                report["install_dir_removed"] = True
                say("程序文件已删除；数据保留在：" + "、".join(kept))
            elif left:
                say(f"安装目录还留着 {len(left)} 项（可能被占用）："
                    + "、".join(left[:6]))
            else:
                report["install_dir_removed"] = _try_rmdir(target)

    # ---- 7. 清掉自己的临时副本目录 ----
    tmpdir = args.get("tmpdir") or ""
    if tmpdir and os.path.isdir(tmpdir):
        me = os.path.abspath(sys.executable)
        # 必须先离开这个目录：Windows 不允许删除任何进程的当前工作目录
        try:
            os.chdir(tempfile.gettempdir())
        except OSError:
            pass
        if os.path.dirname(me).lower() == os.path.abspath(tmpdir).lower():
            # 正在运行的 exe 删不掉，先挪出临时目录再登记「下次重启时删除」
            parked = os.path.join(tempfile.gettempdir(),
                                  f"clip_uninst_leftover_{os.getpid()}.exe")
            try:
                os.replace(me, parked)
                schedule_delete_on_reboot(parked)
            except OSError:
                pass
        try:
            shutil.rmtree(tmpdir, onerror=_on_rm_error)
        except OSError:
            pass

    if os.path.isdir(target):
        try:
            left_after = os.listdir(target)
        except OSError:
            left_after = ["?"]
        # 只剩「用户选择保留的数据目录」也算卸载成功（程序文件确实删干净了）
        kept_names = {os.path.basename(path)
                      for path in (report.get("kept_data_dirs") or [])}
        report["ok"] = (not left_after) or set(left_after) <= kept_names
    else:
        report["ok"] = True

    # ---- 8. 结果 ----
    report["summary"] = build_summary(report)
    log.info("======== 卸载结束：ok=%s 删除=%s 警告=%d 错误=%d ========",
             report["ok"], report["deleted"], len(report["warnings"]),
             len(report["errors"]))
    write_report(report, args.get("report") or "")
    return report


def _try_rmdir(path: str) -> bool:
    try:
        os.rmdir(path)
        return True
    except OSError:
        return False


def _norm(path: str) -> str:
    """路径比较用（大小写不敏感 + 绝对路径）"""
    return os.path.normcase(os.path.abspath(path))


def _has_content(path: str) -> bool:
    """目录里有没有实际内容（数据目录要保留时用它判断，空目录不值得留）"""
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.is_file():
                    return True
                if entry.is_dir() and _has_content(entry.path):
                    return True
    except OSError:
        return False
    return False


MOVEFILE_DELAY_UNTIL_REBOOT = 0x00000004


def schedule_delete_on_reboot(path: str) -> bool:
    """
    登记成「下次重启时删除」。

    卸载器是复制到 TEMP 里跑的，正在运行的那个副本（十几 MB 的 exe）
    在进程结束前删不掉，硬留在 %TEMP% 里不好，所以交给系统重启时清掉。
    需要管理员权限（卸载器本来就是提权运行的）；失败也无所谓，只是多留一个文件。
    """
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.MoveFileExW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, wt.DWORD]
        kernel32.MoveFileExW.restype = wt.BOOL
        return bool(kernel32.MoveFileExW(str(path), None,
                                         MOVEFILE_DELAY_UNTIL_REBOOT))
    except Exception:                           # noqa: BLE001
        return False


def _as_list(value) -> list[str]:
    """把 --data-dir 的取值（字符串 / 重复参数得到的列表）拍平成列表"""
    if not value:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    out: list[str] = []
    for item in items:
        for part in str(item).split(","):
            if part.strip():
                out.append(part.strip())
    return out


def categories_from_args(args: dict) -> list[str]:
    """
    从参数推算出要删的分类。

    --delete=text,image,files 优先；旧参数 --purge-data 等价于三类全删。
    都没给 = 保留数据（只卸程序）。
    """
    raw = _as_list(args.get("delete"))
    wanted = [key for key in CATEGORY_KEYS if key in set(raw)]
    if not wanted and args.get("purge-data"):
        wanted = list(CATEGORY_KEYS)
    return wanted


def build_summary(report: dict) -> str:
    """把报告写成给人看的几行"""
    lines: list[str] = []
    lines.append(f"程序目录：{report.get('install_dir', '')}")
    lines.append("程序文件：已删除" if report.get("install_dir_removed")
                 else "程序文件：未能完全删除（可能还有文件被占用）")
    if report.get("kept_data_dirs"):
        lines.append("数据保留在：" + "、".join(report["kept_data_dirs"]))

    deleted = report.get("deleted") or {}
    titles = {"text": "文本记录", "image": "图片", "files": "文件"}
    if report.get("categories"):
        parts = [f"{titles.get(key, key)} {deleted.get(key, 0)} 条"
                 for key in report["categories"]]
        parts.append(f"释放空间 {format_size(report.get('freed', 0))}")
        lines.append("已删除数据：" + "，".join(parts))
        if report.get("removed_extras"):
            lines.append("已一并删除配置与日志")
    else:
        lines.append("本机数据：按你的选择保留（没有删除）")

    for warning in report.get("warnings") or []:
        lines.append(f"提示：{warning}")
    for error in report.get("errors") or []:
        lines.append(f"问题：{error}")
    return "\n".join(lines)


# ---------------------------------------------------------
# 入口 1：独立卸载器（uninstaller.exe）
# ---------------------------------------------------------
def collect_context(install_dir_path: str = "",
                    extra_data_dirs: list[str] | None = None) -> dict:
    """卸载前需要知道的东西：安装目录、候选数据目录、各自的统计"""
    target = os.path.abspath(install_dir_path or install_dir())
    data_dirs = candidate_dirs(target, extra_data_dirs)
    return {
        "install_dir": target,
        "data_dirs": data_dirs,
        "stats": inspect(data_dirs),
        "installer_exists": bool(find_uninstaller(target)),
        "info": load_install_info(target),
    }


def spawn_uninstall_job(install_dir_path: str, data_dirs: list[str],
                        categories: list[str], report_path: str = "",
                        extra_args: dict | None = None,
                        script: str = "") -> tuple[bool, str]:
    """
    从当前进程派生「真正卸载」的副本（复制到 TEMP 再启动）。

    当前进程必须在调用后尽快退出：副本会等它的 pid 消失再删安装目录。
    """
    info = load_install_info(install_dir_path)
    argv = [
        "--execute",
        f"--install-dir={install_dir_path}",
        f"--pid={os.getpid()}",
        f"--delete={','.join(categories)}",
    ]
    for path in data_dirs:
        argv.append(f"--data-dir={path}")
    for key in ("auto_start_value", "auto_start_task"):
        if info.get(key):
            argv.append(f"--{key.replace('_', '-')}={info[key]}")
    for key in ("desktop_link", "start_menu_link"):
        if info.get(key):
            argv.append(f"--{key.replace('_', '-')}={info[key]}")
    if report_path:
        argv.append(f"--report={report_path}")
    for key, value in (extra_args or {}).items():
        if value is True:
            argv.append(f"--{key}")
        elif value:
            argv.append(f"--{key}={value}")
    return spawn_job_copy(argv, script=script)


# ---------------------------------------------------------
# 安全阀：绝不删源码目录
# ---------------------------------------------------------
SOURCE_TREE_MARKERS = ("main.py", "requirements.txt")
# 一个「像安装目录」的目录至少要有其中之一（卸载是删目录里的一切，必须验明正身）
INSTALL_MARKERS = (MAIN_EXE_NAME, UNINSTALLER_EXE_NAME, INSTALL_INFO_FILE)


def looks_like_install_dir(path: str) -> bool:
    """目录里有没有本程序自己的东西（主程序 / 卸载器 / install.json）"""
    if not os.path.isdir(path):
        return False
    return any(os.path.isfile(os.path.join(path, name))
               for name in INSTALL_MARKERS)


def is_source_tree(path: str) -> bool:
    """
    看起来像项目源码目录吗？

    卸载是「删程序目录里的一切」，所以必须有个安全阀：源码目录里没有
    ClipboardHistory.exe，却有 main.py / requirements.txt——这种情况一律拒绝，
    免得开发时手滑把自己的工程删了。
    """
    if not os.path.isdir(path):
        return False
    if looks_like_install_dir(path):
        return False                # 有主程序 exe = 打包产物，正常
    return all(os.path.isfile(os.path.join(path, name))
               for name in SOURCE_TREE_MARKERS)


# ---------------------------------------------------------
# 入口 2：用户可见的卸载（绿色版 / 没有 uninstaller.exe 时）
# ---------------------------------------------------------
def run_uninstall_ui() -> int:
    """
    旧的确认流程（原生消息框）。

    装了独立卸载器时不会再走到这里（main.py 会把 --uninstall 转交给
    uninstaller.exe）；保留它是为了绿色版和「卸载器被删掉了」的情况。
    """
    info = load_install_info()
    data_dir = info.get("data_dir") or default_data_dir()

    if _msgbox(
        f"确定要卸载 {APP_NAME} 吗？\n\n"
        f"程序目录：{install_dir()}\n"
        f"数据目录：{data_dir}（默认保留，不会删除）",
        f"卸载 {APP_NAME}", MB_YESNO | MB_ICONQUESTION,
    ) != IDYES:
        return 0

    purge = _msgbox(
        "要同时删除剪贴板历史数据吗？\n\n"
        f"数据目录：{data_dir}\n\n"
        "选「是」= 一并删除全部文本、图片、文件（不可恢复）\n"
        "选「否」= 保留历史记录，以后重装还能用",
        f"卸载 {APP_NAME}", MB_YESNO | MB_ICONWARNING,
    ) == IDYES

    return spawn_uninstaller(purge)


def spawn_uninstaller(purge: bool) -> int:
    """旧流程用的派生方式（参数与 run_uninstall_now 对齐）"""
    info = load_install_info()
    data_dir = info.get("data_dir") or default_data_dir()
    argv = [
        "--uninstall-now",
        f"--pid={os.getpid()}",
        f"--install-dir={install_dir()}",
        f"--data-dir={data_dir}",
    ]
    if purge:
        argv.append("--purge-data")
    for name in ("start_menu_link", "desktop_link", "auto_start_value"):
        value = info.get(name)
        if value:
            argv.append(f"--{name.replace('_', '-')}={value}")

    ok, detail = spawn_job_copy(argv)
    if not ok:
        _msgbox(detail, f"卸载 {APP_NAME}", MB_ICONWARNING)
        return 1
    return 0


# ---------------------------------------------------------
# 入口 3：副本进程（--uninstall-now，老注册表项还在用）
# ---------------------------------------------------------
def run_uninstall_now(args: dict) -> int:
    """
    在副本进程里执行卸载并弹一个结果框。

    参数与旧版本兼容：pid / install-dir / data-dir / purge-data /
    start-menu-link / desktop-link / auto-start-value / tmpdir。
    """
    report = run_uninstall_job(args)
    text = report.get("summary") or build_summary(report)
    if report.get("errors"):
        _msgbox(f"{APP_NAME} 卸载完成，但有以下问题：\n\n{text}",
                f"卸载 {APP_NAME}", MB_ICONWARNING)
    else:
        _msgbox(f"{APP_NAME} 已卸载。\n\n{text}\n\n",
                f"卸载 {APP_NAME}", MB_ICONINFORMATION)
    return 0


# ---------------------------------------------------------
# 命令行解析
# ---------------------------------------------------------
def wants_uninstall(argv: list[str]) -> bool:
    return any(a.lower() in ("--uninstall", "-uninstall", "/uninstall")
               for a in argv)


def wants_uninstall_now(argv: list[str]) -> bool:
    return any(a.lower() == "--uninstall-now" for a in argv)


def parse_uninstall_args(argv: list[str]) -> dict:
    """
    把 --key=value 拆成 dict（key 用短横线形式）；不带 = 的参数进 flags 集合。

    同名参数出现多次时合并成列表（--data-dir 可能给多个目录）。
    """
    out: dict = {}
    flags: set[str] = set()
    for arg in argv:
        if not arg.startswith("--"):
            continue
        body = arg[2:]
        if "=" not in body:
            flags.add(body.lower())
            continue
        key, value = body.split("=", 1)
        key = key.lower()
        if key in out:
            existing = out[key]
            if isinstance(existing, list):
                existing.append(value)
            else:
                out[key] = [existing, value]
        else:
            out[key] = value
    out["flags"] = flags
    return out
