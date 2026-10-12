# uninstaller.py
"""
卸载器入口（独立打包成 uninstaller.exe，安装后就在程序目录里）。

为什么要有这个独立的 exe：
    1. 「设置 -> 应用 -> 已安装的应用」里的卸载项要指向一个东西，指向
       uninstaller.exe 比指向主程序 + 参数更像正常软件，主程序也不用再背着
       卸载界面的代码；
    2. 卸载必须在**主程序之外**跑：主程序自己删不掉自己（文件被占用），
       而且要先把正在运行的主程序请出去、把 history.db 让出来。

三个入口：
    uninstaller.exe                        图形卸载（问用户删哪些数据）
    uninstaller.exe --silent [--delete=...] 静默卸载（批量/自动化）
    uninstaller.exe --execute ...           内部：TEMP 里的副本真正干活

静默参数：
    --delete=text,image,files   要删的数据分类（all = 三类全删；不写 = 只卸程序）
    --data-dir=<目录>           指定数据目录（可重复；不写 = 自动探测）
    --install-dir=<目录>        指定安装目录（默认 exe 所在目录）
    --keep-registry / --keep-shortcuts / --keep-autostart / --no-close-running
    --report=<文件>             把结果写成 JSON

界面部分在 ui/uninstall_tk.py（tkinter），刻意不依赖 Qt：
卸载器是独立小 exe，塞一份 PySide6 要多背 40 MB。
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
if not getattr(sys, "frozen", False) and ROOT not in sys.path:
    # 只有源码运行才需要（打包后模块都在 _MEIPASS 里，插程序目录反而可能抢到同名模块）
    sys.path.insert(0, ROOT)

# 日志必须落在 TEMP：卸载时正要删掉 %APPDATA%\ClipboardHistory\app.log，
# 如果卸载器自己还开着那个文件，删不掉（占用）而且会把它重新创建出来。
os.environ.setdefault(
    "CLIPBOARD_LOG_FILE",
    os.path.join(tempfile.gettempdir(), "ClipboardHistory-uninstall.log"))

from core import applog                                     # noqa: E402
from core.data_clean import CATEGORY_KEYS, has_anything     # noqa: E402
from core.uninstall import (                                # noqa: E402
    collect_context, is_source_tree, parse_uninstall_args, run_uninstall_job,
    spawn_uninstall_job, write_report,
)
from core.version import APP_NAME, __version__              # noqa: E402

TITLE = f"卸载 {APP_NAME}"

# 需要原样透传给副本进程的开关
FORWARD_FLAGS = (
    "keep-registry", "keep-shortcuts", "keep-autostart", "no-close-running",
    "keep-data", "silent",
)


def _categories_from(args: dict) -> list[str]:
    """把 --delete= / --purge-data / --keep-data 统一成分类列表"""
    if "keep-data" in args.get("flags", set()):
        return []
    raw = args.get("delete")
    if isinstance(raw, str):
        raw = [raw]
    values: list[str] = []
    for item in (raw or []):
        values.extend(part.strip() for part in str(item).split(",") if part.strip())
    if "all" in values:
        return list(CATEGORY_KEYS)
    wanted = [key for key in CATEGORY_KEYS if key in set(values)]
    if not wanted and args.get("purge-data"):
        wanted = list(CATEGORY_KEYS)
    return wanted


def _data_dirs_from(args: dict) -> list[str]:
    raw = args.get("data-dir")
    if isinstance(raw, str):
        raw = [raw]
    out: list[str] = []
    for item in (raw or []):
        out.extend(part.strip() for part in str(item).split(",") if part.strip())
    return out


def _install_dir_from(args: dict) -> str:
    return str(args.get("install-dir") or "").strip()


def _resolve_install_dir(args: dict) -> str:
    """
    要卸载哪个目录：命令行 > 自己 exe 所在目录。

    **源码运行时必须显式给 --install-dir**：打包后「exe 所在目录」就是安装目录，
    而源码运行时那是工程目录或 Python 安装目录——默认值会删错东西。
    """
    explicit = _install_dir_from(args)
    if explicit:
        return explicit
    from core.uninstall import is_frozen

    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return ""


# ---------------------------------------------------------
# 子进程（TEMP 里的副本）：真正干活
# ---------------------------------------------------------
def _run_execute(args: dict) -> int:
    """--execute：在副本进程里执行卸载（可选进度窗口）"""
    from ui.uninstall_tk import run_with_progress

    log = applog.get("uninstaller")
    target = _install_dir_from(args)
    if not target:
        # 副本跑在 TEMP 里，install_dir() 会指向 TEMP，必须由父进程显式传入
        log.error("--execute 缺少 --install-dir，拒绝执行（避免删错目录）")
        return 2

    silent = bool(args.get("silent") or "silent" in args.get("flags", set()))
    job_args = dict(args)
    job_args["install-dir"] = target
    report_path = args.get("report") or ""
    holder: dict = {}

    if silent:
        try:
            report = run_uninstall_job(job_args)
        except Exception as e:                  # noqa: BLE001
            # 静默模式绝不能弹框（没人点，会把进程挂住）：写进报告 + 日志
            log.exception("静默卸载失败")
            report = {"ok": False, "install_dir": target,
                      "errors": [f"{type(e).__name__}: {e}"],
                      "steps": [], "warnings": []}
            report["summary"] = f"卸载失败：{type(e).__name__}: {e}"
        write_report(report, report_path)
        return 0 if report.get("ok") else 1

    def worker(progress):
        report = run_uninstall_job(job_args, progress=progress)
        holder["report"] = report
        return bool(report.get("ok")), str(report.get("summary") or "")

    code = run_with_progress(worker, title=TITLE)
    write_report(holder.get("report") or {}, report_path)
    return code


# ---------------------------------------------------------
# 父进程：图形 / 静默
# ---------------------------------------------------------
def _spawn(args: dict, categories: list[str], data_dirs: list[str],
           install_dir_path: str, quiet: bool = False) -> int:
    """把「真正干活」的副本派生出去"""
    flags = set(args.get("flags", set()))
    extra_args: dict = {flag: True for flag in FORWARD_FLAGS if flag in flags}
    if args.get("report"):
        extra_args["report"] = args["report"]

    ok, detail = spawn_uninstall_job(
        install_dir_path, data_dirs, categories,
        report_path="", extra_args=extra_args, script=__file__)
    if not ok:
        applog.get("uninstaller").error("派生卸载进程失败：%s", detail)
        if not quiet:
            from ui.uninstall_tk import MB_ICONWARNING, MB_OK, message_box

            message_box(f"无法启动卸载程序：\n\n{detail}", TITLE,
                        MB_OK | MB_ICONWARNING)
        return 1
    applog.get("uninstaller").info("已派生卸载进程（临时目录 %s）", detail or "无")
    return 0


def _run_gui(args: dict) -> int:
    from ui.uninstall_tk import (IDYES, MB_ICONQUESTION, MB_ICONWARNING, MB_OK,
                                 MB_YESNO, ask_choices, message_box)

    log = applog.get("uninstaller")
    extra = _data_dirs_from(args)
    install_dir_path = _resolve_install_dir(args)

    # 源码运行时必须显式指定安装目录，否则会指向工程目录（见 is_source_tree）
    if not install_dir_path:
        message_box(
            "源码运行时卸载器需要显式指定要卸载的目录：\n\n"
            "    python uninstaller.py --install-dir=D:\\Apps\\ClipboardHistory\n\n"
            "（这是开发/测试用途；正常卸载请用安装目录里的 uninstaller.exe）",
            TITLE, MB_OK | MB_ICONWARNING)
        return 1

    ctx = collect_context(install_dir_path, extra)
    log.info("卸载器启动：目录=%s 数据目录=%s 版本=%s",
             ctx["install_dir"], ctx["data_dirs"], __version__)

    if is_source_tree(ctx["install_dir"]):
        message_box(
            f"拒绝卸载：{ctx['install_dir']}\n"
            f"看起来是项目源码目录，不是打包后的程序目录。",
            TITLE, MB_OK | MB_ICONWARNING)
        return 1

    stats = ctx["stats"]
    choices = ask_choices(ctx["install_dir"], stats, version=__version__)
    if choices is None:
        log.info("用户取消了卸载")
        return 0

    categories = choices.get("categories") or []
    if not has_anything(stats) and not extra:
        # 没有任何数据：再确认一次就好（免得点错就卸了）
        if message_box(f"确定要卸载 {APP_NAME} 吗？\n\n"
                       f"程序目录：{ctx['install_dir']}",
                       TITLE, MB_YESNO | MB_ICONQUESTION) != IDYES:
            return 0

    log.info("用户选择删除：%s", categories or "（只卸程序，保留数据）")
    return _spawn(args, categories, ctx["data_dirs"], ctx["install_dir"])


def _run_silent(args: dict) -> int:
    """静默卸载：父进程只负责派生副本，然后立刻退出"""
    log = applog.get("uninstaller")
    install_dir_path = _resolve_install_dir(args)
    data_dirs = _data_dirs_from(args)
    categories = _categories_from(args)
    if not install_dir_path:
        # 源码运行又没给 --install-dir：默认值会是 Python 目录，绝不能这么干
        log.error("静默卸载缺少 --install-dir（源码运行时必须显式指定），已拒绝")
        return 2
    log.info("静默卸载：目录=%s 数据=%s 删除=%s",
             install_dir_path, data_dirs, categories)
    return _spawn(args, categories, data_dirs, install_dir_path, quiet=True)


# ---------------------------------------------------------
# 入口
# ---------------------------------------------------------
def wants_uninstall_silent(argv: list[str]) -> bool:
    """命令行里要求静默卸载（--silent / --uninstall-silent / --quiet …）"""
    names = {"silent", "quiet", "uninstall-silent", "uninstall-quiet",
             "silent-uninstall"}
    return any(a.lstrip("-").lower() in names for a in argv)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    applog.setup()
    log = applog.get("uninstaller")

    # 必须在建窗口之前设置 DPI 感知：否则高 DPI 下窗口会被系统整体拉伸
    # （字糊、控件位置和 tk 认为的不一致）
    from ui.uninstall_tk import make_dpi_aware

    make_dpi_aware()
    log.info("======== 卸载器启动 pid=%s argv=%s 版本=%s ========",
             os.getpid(), argv, __version__)

    args = parse_uninstall_args(argv)
    flags = args.get("flags", set())

    if "execute" in flags:
        return _run_execute(args)
    if "silent" in flags or wants_uninstall_silent(argv):
        args.setdefault("flags", set()).add("silent")
        return _run_silent(args)
    return _run_gui(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:                    # noqa: BLE001 - 崩溃也要让用户看见
        import traceback

        applog.get("uninstaller").exception("卸载器异常退出")
        # 静默模式没有人在旁边点「确定」，弹框会把进程挂住：只写日志
        if not wants_uninstall_silent(sys.argv[1:]):
            try:
                from ui.uninstall_tk import MB_ICONERROR, MB_OK, message_box

                message_box(f"卸载器出错：\n\n{type(exc).__name__}: {exc}\n\n"
                            f"{traceback.format_exc()[-1200:]}",
                            TITLE, MB_OK | MB_ICONERROR)
            except Exception:                   # noqa: BLE001
                pass
        sys.exit(1)
