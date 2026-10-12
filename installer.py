# installer.py
"""
安装向导（图形界面）。

三种入口，都走 core/installer.py 里同一套安装逻辑：

    ClipboardHistory-Setup.exe                        图形向导（推荐）
    ClipboardHistory-Setup.exe --install-silent ...   静默安装（批量部署）
    ClipboardHistory.exe --install                    绿色版目录里也能起向导

向导会引导用户把**设置里的所有配置项**过一遍（每一项都预填了默认值），
然后复制程序文件、写配置、建快捷方式、注册「应用和功能」卸载项、
按选择设置开机自启。

静默安装参数（都带默认值，可只给需要的）：
    --dir=D:\\Apps\\ClipboardHistory   安装目录（默认 Program Files 或
                                       %LOCALAPPDATA%\\Programs）
    --data-dir=D:\\ClipData            数据目录（默认 <安装目录>\\data）
    --no-desktop / --no-start-menu     不创建对应快捷方式
    --auto-start=1|0                   开机自启（默认 0）
    --launch=1|0                       安装完成后启动（默认 0）
    --max-items=500 --max-size-mb=200 ... 其余设置项同名覆盖，见
                                       core/settings.py 的 DEFAULT_SETTINGS

数据目录默认跟着安装目录走：装到 D:\\Apps\\ClipboardHistory，数据就在
D:\\Apps\\ClipboardHistory\\data（安装时自动创建），--data-dir 可以改掉它。
"""
from __future__ import annotations

import os
import sys

from core import applog
from core.installer import install, launch_installed, parse_silent_args
from core.paths import resource
from core.version import APP_NAME, __version__

QSS_PATH = resource("ui", "style.qss")


# ---------------------------------------------------------
# 命令行
# ---------------------------------------------------------
def wants_install(argv: list[str]) -> bool:
    return any(a.lower() in ("--install", "-install", "/install", "--setup")
               for a in argv)


def wants_silent_install(argv: list[str]) -> bool:
    return any(a.lower() in ("--install-silent", "--silent-install",
                             "--install-quiet")
               for a in argv)


# ---------------------------------------------------------
# 静默安装
# ---------------------------------------------------------
def run_silent_install(argv: list[str]) -> int:
    """没有任何界面：只写日志 + 返回退出码（0 成功）"""
    log = applog.get("installer")
    options = parse_silent_args(argv)
    log.info("静默安装：目标=%s 数据=%s 桌面=%s 开始菜单=%s 自启=%s 启动=%s",
             options.target_dir, options.settings.get("storage_dir") or "(默认)",
             options.desktop_shortcut, options.start_menu_shortcut,
             options.auto_start, options.launch_after)

    result = install(options)
    log.info("静默安装结果：ok=%s\n%s", result.ok, result.summary())
    if result.ok and options.launch_after:
        launch_installed(result.exe_path, result.target_dir)
    return 0 if result.ok else 1


# ---------------------------------------------------------
# 图形向导
# ---------------------------------------------------------
def _load_stylesheet(app) -> None:
    if os.path.exists(QSS_PATH):
        try:
            with open(QSS_PATH, "r", encoding="utf-8") as f:
                app.setStyleSheet(f.read())
        except OSError:
            pass


def run_installer_ui(argv: list[str] | None = None) -> int:
    """图形安装向导。返回进程退出码。"""
    from PySide6.QtWidgets import QApplication

    argv = list(sys.argv if argv is None else argv)
    app = QApplication.instance()
    if app is None:
        app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    _load_stylesheet(app)

    from ui.install_wizard import InstallWizard
    wizard = InstallWizard()
    wizard.show()
    return wizard.exec()


# ---------------------------------------------------------
# 统一入口
# ---------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    applog.setup()
    log = applog.get("installer")
    log.info("======== 安装器启动 pid=%s argv=%s 版本=%s ========",
             os.getpid(), argv[1:], __version__)
    if wants_silent_install(argv):
        return run_silent_install(argv)
    return run_installer_ui(argv)


if __name__ == "__main__":
    sys.exit(main())
