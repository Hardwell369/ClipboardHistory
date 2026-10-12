# src/diag.py
"""
自检模式：ClipboardHistory.exe --diag

为什么需要它：打包成 exe 之后没有控制台，用户机器上的环境（权限、安全软件、
托盘、文件关联、ShellExecute）跟我们这边完全可能不一样，靠猜没有意义。
这个模式只做「只读检查 + 一次打开文件夹的实测」，然后把结论写成一份文本报告：

    - 弹窗显示（可选中复制）
    - 复制到剪贴板
    - 落盘一份：exe 同目录（写不了就退到 TEMP）

报告里包含日志文件到底落在哪、托盘能不能用、explorer.exe 能不能起来等关键事实，
用户直接把它发回来就能定位问题。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

from src import applog
from src.paths import app_root, resource

REPORT_NAME = "ClipboardHistory-diag.txt"
_PROBE_TIMEOUT_S = 3.0


# ---------------------------------------------------------
# 小工具
# ---------------------------------------------------------
def _line(label: str, value) -> str:
    return f"{label}: {value}"


def _is_elevated() -> str:
    """当前进程是否以管理员身份运行（打包版清单要求提权，这里是核对用）"""
    try:
        from src.autostart import is_elevated
        if is_elevated():
            return "是（已提权）"
        return "否（普通权限）—— 打包版清单要求管理员，出现“否”说明被绕过了"
    except Exception as e:                      # noqa: BLE001
        return f"检查失败：{e}"


def _probe_write(path: str) -> str:
    """在某条路径上做一次写测试（能建就删），返回人话结论"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except OSError as e:
        return f"失败（建目录：{e}）"
    tmp = path + f".probe{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("x")
        os.remove(tmp)
        return "可写"
    except OSError as e:
        return f"不可写（{e}）"


def _dir_info(path: str) -> str:
    try:
        if not os.path.exists(path):
            return "不存在"
        if not os.path.isdir(path):
            return "存在但不是目录（是文件）"
        try:
            n = len(os.listdir(path))
        except OSError as e:
            return f"是目录，但列不出来（{e}）"
        return f"是目录，{n} 个条目"
    except OSError as e:
        return f"检查失败（{e}）"


# ---------------------------------------------------------
# 报告
# ---------------------------------------------------------
def build_report() -> str:
    from src.version import APP_NAME, __version__

    frozen = bool(getattr(sys, "frozen", False))
    lines: list[str] = [
        f"{APP_NAME} 自检报告",
        f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        _line("版本", __version__),
        _line("运行方式", "打包版（exe）" if frozen else "源码运行"),
        _line("pid", os.getpid()),
        _line("用户名", os.environ.get("USERNAME") or os.environ.get("USER") or "?"),
        _line("USERPROFILE", os.environ.get("USERPROFILE", "(未设置)")),
        _line("sys.executable", sys.executable),
        _line("sys._MEIPASS", getattr(sys, "_MEIPASS", "(未设置)")),
        _line("资源目录 app_root()", app_root()),
        "",
        "---- 资源文件 ----",
    ]
    for name, rel in (("icon.ico", ("assets", "icon.ico")),
                      ("style.qss", ("ui", "style.qss"))):
        p = resource(*rel)
        lines.append(_line(name, f"{'存在' if os.path.exists(p) else '缺失'}  {p}"))

    lines += ["", "---- 日志 ----"]
    for p in applog.candidate_paths():
        lines.append(_line("候选", f"{p}  ->  {_probe_write(p)}"))
    lines.append(_line("本次实际使用", applog.log_file_path()))

    lines += ["", "---- 配置 ----"]
    try:
        from src.settings import Settings, default_settings_path
        sp = default_settings_path()
        lines.append(_line("settings.json", sp))
        lines.append(_line("是否可写", _probe_write(sp)))
        try:
            st = Settings(sp)
            lines.append(_line("读取", "成功"))
            lines.append(_line("存储目录（配置值）", st.resolve_storage_dir()))
            storage_dir = st.resolve_storage_dir()
        except Exception as e:                  # noqa: BLE001
            lines.append(_line("读取", f"失败：{type(e).__name__}: {e}"))
            storage_dir = ""
    except Exception as e:                      # noqa: BLE001
        lines.append(_line("配置", f"检查失败：{type(e).__name__}: {e}"))
        storage_dir = ""

    lines += ["", "---- 存储目录 ----"]
    if storage_dir:
        lines.append(_line("路径", storage_dir))
        lines.append(_line("状态", _dir_info(storage_dir)))
        lines.append(_line("写测试", _probe_write(os.path.join(storage_dir, "probe.txt"))))
        lines.append(_line("锁文件", os.path.join(storage_dir, ".lock")))
    else:
        lines.append("(拿不到存储目录)")

    lines += ["", "---- 单实例锁 ----"]
    try:
        from src.single_instance import lock_path
        lp = lock_path()
        lines.append(_line("app.lock", lp))
        lines.append(_line("状态", _dir_info(lp)))
        lines.append(_line("写测试", _probe_write(lp)))
    except Exception as e:                      # noqa: BLE001
        lines.append(_line("检查失败", e))

    lines += ["", "---- 开机自启 ----"]
    try:
        from src.autostart import status as autostart_status, task_exists
        lines.append(autostart_status())
        lines.append(_line("是否管理员运行", _is_elevated()))
        if task_exists():
            from src.autostart import describe_task
            lines.append(_line("任务详情", describe_task() or "(查不到)"))
    except Exception as e:                      # noqa: BLE001
        lines.append(_line("检查失败", f"{type(e).__name__}: {e}"))

    lines += ["", "---- 环境变量 ----"]
    for key in ("APPDATA", "LOCALAPPDATA", "TEMP", "USERPROFILE", "SystemRoot"):
        lines.append(_line(key, os.environ.get(key, "(未设置)")))
    path_env = os.environ.get("PATH", "")
    lines.append(_line("PATH(前 240 字)", path_env[:240]))

    lines += ["", "---- 打开文件夹实测 ----"]
    lines.append(_line("%SystemRoot%\\explorer.exe",
                       "存在" if os.path.exists(_explorer_path()) else "缺失"))
    lines.append(_launch_probe(storage_dir))
    lines.append(_desktop_services_probe(storage_dir))
    lines.append(_shellexecute_probe(storage_dir))
    return "\n".join(lines)


def _desktop_services_probe(storage_dir: str) -> str:
    """QDesktopServices（内部 ShellExecute），主程序里也会用到这条路"""
    if not storage_dir or not os.path.isdir(storage_dir):
        return "QDesktopServices 实测: 跳过（存储目录不是目录）"
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        ok = QDesktopServices.openUrl(QUrl.fromLocalFile(storage_dir))
        return f"QDesktopServices 实测: {'成功' if ok else '返回 False（被拦或关联异常）'}"
    except Exception as e:                      # noqa: BLE001
        return f"QDesktopServices 实测: 异常 {type(e).__name__}: {e}"


def _explorer_path() -> str:
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    return os.path.join(root, "explorer.exe")


def _launch_probe(storage_dir: str) -> str:
    """直接起 explorer.exe（我们现在的首选方式），把子进程退出码也记下来"""
    if not storage_dir or not os.path.isdir(storage_dir):
        return "explorer.exe 实测: 跳过（存储目录不是目录）"
    exe = _explorer_path()
    if not os.path.exists(exe):
        return f"explorer.exe 实测: 跳过（{exe} 不存在）"
    try:
        proc = subprocess.Popen([exe, os.path.normpath(storage_dir)], close_fds=True)
    except OSError as e:
        return f"explorer.exe 实测: 启动失败 {type(e).__name__}: {e}"
    deadline = time.monotonic() + _PROBE_TIMEOUT_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            code = proc.returncode
            note = "（explorer 打开已有窗口时返回 1 属正常）" if code == 1 else ""
            return f"explorer.exe 实测: 已退出，退出码 {code} (0x{code & 0xFFFFFFFF:08X}){note}"
        time.sleep(0.1)
    return "explorer.exe 实测: 仍在运行（通常说明已经打开了）"


def _shellexecute_probe(storage_dir: str) -> str:
    """os.startfile / ShellExecute 是不是被拦（错误码 5 = 拒绝访问）"""
    if not storage_dir or not os.path.isdir(storage_dir):
        return "os.startfile 实测: 跳过"
    try:
        os.startfile(storage_dir)               # type: ignore[attr-defined]
        return "os.startfile 实测: 调用成功（无异常）"
    except OSError as e:
        return f"os.startfile 实测: 失败 {type(e).__name__}: {e}"


# ---------------------------------------------------------
# 托盘自检
# ---------------------------------------------------------
_tray_ref = None            # 别让 QSystemTrayIcon 被回收


def tray_probe() -> str:
    """真的建一个托盘图标并显示，同时发一条气泡，让用户肉眼确认"""
    global _tray_ref
    lines = ["", "---- 托盘 ----"]
    try:
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QSystemTrayIcon

        icon_path = resource("assets", "icon.ico")
        icon = QIcon(icon_path)
        lines.append(_line("图标文件", f"{'存在' if os.path.exists(icon_path) else '缺失'}  {icon_path}"))
        lines.append(_line("图标是否为空", icon.isNull()))
        lines.append(_line("尺寸", [f"{s.width()}x{s.height()}" for s in icon.availableSizes()]))
        lines.append(_line("系统托盘可用", QSystemTrayIcon.isSystemTrayAvailable()))
        lines.append(_line("支持气泡通知", QSystemTrayIcon.supportsMessages()))

        tray = QSystemTrayIcon(icon)
        _tray_ref = tray
        tray.setToolTip("剪贴板历史 自检")
        tray.show()
        lines.append(_line("show() 后 isVisible", tray.isVisible()))
        lines.append(_line("注册是否成功", "Shell_NotifyIcon 已被调用（无返回值可查）"))
        lines.append("提示: 如果下面这条气泡你能看到，说明托盘图标已经成功注册——"
                     "看不到图标只是被 Windows 折叠进「隐藏的图标」了")
        try:
            tray.showMessage("剪贴板历史 自检",
                             "看到这条气泡 = 托盘图标已注册成功（图标可能被系统折叠）",
                             QSystemTrayIcon.Information, 8000)
        except Exception as e:                  # noqa: BLE001
            lines.append(_line("气泡通知", f"失败：{e}"))
    except Exception as e:                      # noqa: BLE001
        lines.append(_line("托盘检查失败", f"{type(e).__name__}: {e}"))
    return "\n".join(lines)


# ---------------------------------------------------------
# 报告输出
# ---------------------------------------------------------
def write_report(text: str) -> str:
    """优先写到 exe 同目录，写不了就退到 TEMP；返回实际路径（失败返回空串）"""
    candidates: list[str] = []
    if getattr(sys, "frozen", False):
        candidates.append(os.path.join(os.path.dirname(sys.executable), REPORT_NAME))
    candidates.append(os.path.join(app_root(), REPORT_NAME))
    temp = os.environ.get("TEMP") or os.environ.get("TMP")
    if temp:
        candidates.append(os.path.join(temp, REPORT_NAME))

    for path in candidates:
        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            return path
        except OSError:
            continue
    return ""


def show_report(text: str, path: str) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox

    box = QMessageBox()
    box.setWindowTitle("剪贴板历史 自检报告")
    box.setIcon(QMessageBox.Information)
    box.setText("自检完成，报告如下（可直接复制）。")
    box.setInformativeText(
        f"报告文件：{path or '(写文件失败，请手动复制本窗口内容)'}\n"
        f"日志文件：{applog.log_file_path()}\n\n"
        "如果托盘图标看不到、或打开文件夹报错，请把这份报告发回来。"
    )
    box.setDetailedText(text)
    btn_copy = box.addButton("复制报告", QMessageBox.ActionRole)
    box.addButton("关闭", QMessageBox.AcceptRole)
    box.setWindowFlags(box.windowFlags() | Qt.WindowStaysOnTopHint)
    box.exec()
    if box.clickedButton() is btn_copy:
        QApplication.clipboard().setText(text)


def run_diag() -> int:
    """--diag 入口：不建主窗口、不碰剪贴板监听，只做检查"""
    applog.setup()
    log = applog.get("diag")

    # 托盘实测必须有 QApplication，所以先建（建不起来也要把其余检查做完）
    app = None
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)
    except Exception as e:                      # noqa: BLE001
        log.exception("自检：QApplication 创建失败")

    try:
        text = build_report()
        if app is not None:
            text += "\n" + tray_probe()
    except Exception as e:                      # noqa: BLE001 - 自检本身绝不能崩
        log.exception("自检失败")
        text = f"自检过程中出错：{type(e).__name__}: {e}\n"

    path = write_report(text)
    log.info("自检报告已写入：%s", path or "(写文件失败)")

    if app is None:
        # 连 Qt 都起不来：至少把报告打到 stderr（源码运行时能看到）
        try:
            print(text)
        except Exception:                       # noqa: BLE001
            pass
        return 1

    try:
        # 让气泡先弹出来，再显示报告
        for _ in range(12):
            app.processEvents()
            time.sleep(0.05)
        show_report(text, path)
        app.processEvents()
    except Exception as e:                      # noqa: BLE001
        log.exception("显示自检报告失败")
        try:
            print(text)
        except Exception:                       # noqa: BLE001
            pass
        return 1
    return 0


def wants_diag(argv: list[str]) -> bool:
    return any(a.lower() in ("--diag", "-diag", "/diag") for a in argv)
