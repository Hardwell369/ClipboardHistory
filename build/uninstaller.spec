# build/uninstaller.spec
"""
卸载程序（uninstaller.exe）的 PyInstaller 配置：单文件 exe。

用法（在项目根目录执行）：
    pyinstaller build/uninstaller.spec --clean --noconfirm

产物：
    dist/uninstaller.exe

它做什么：
    问用户「卸载时要不要删掉本机保存的文本 / 图片 / 文件」（每一类都显示
    条数和体积，可分别勾选），确认后把自己复制到 TEMP 再干活——
    正在运行的 exe 删不掉自己；副本会先请正在运行的主程序优雅退出，
    然后删数据、删快捷方式、删自启、删注册表卸载项，最后删掉安装目录。

为什么单独打包、而且**不依赖 Qt**：
    它必须能在主程序已经被删掉的情况下独立运行，所以要有自己的一份解释器；
    但界面只用标准库的 tkinter（见 ui/uninstall_tk.py），
    这样 exe 只有十几 MB —— 如果塞 PySide6 就是 40 MB 起步，
    而它会跟着安装包一起发给用户（payload 越小，Setup.exe 越小）。

清单里同样要求管理员权限（uac_admin=True）：要删 Program Files 下的安装目录、
写注册表卸载项、删「最高权限」的开机自启计划任务。
"""
import os
import sys

try:
    SPEC_DIR = SPECPATH                     # noqa: F821
except NameError:
    SPEC_DIR = os.path.dirname(os.path.abspath(__file__))

ROOT = os.path.abspath(os.path.join(SPEC_DIR, os.pardir))

# ---- exe 版本信息（属性 -> 详细信息）----
VERSION_FILE = None
try:
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    sys.path.insert(0, SPEC_DIR)
    from make_version_info import main as make_version_info   # noqa: E402

    make_version_info("uninstaller")
    VERSION_FILE = os.path.join(SPEC_DIR, "version_info.txt")
except Exception as e:                      # noqa: BLE001
    print(f"[uninstaller.spec] 生成版本信息失败，exe 将不带版本号：{e}")

datas = [
    (os.path.join(ROOT, "assets", "icon.ico"), "assets"),   # 窗口图标
]

# 一个 Qt 都不带：卸载器只用标准库（tkinter + sqlite3 + ctypes）
excludes = [
    "PySide6",
    "shiboken6",
    "PyQt5",
    "PyQt6",
    "numpy",
    "pandas",
    "matplotlib",
    "pynput",
    "win32com",
    "pythoncom",
    "tkinter.test",
    "unittest",
    "pydoc_data",
    "lib2to3",
    "setuptools",
    "pip",
]

hiddenimports = [
    "tkinter",
    "tkinter.ttk",
    "tkinter.messagebox",
    "sqlite3",
    # 这几个是在函数体里 import 的（按需延迟导入），显式列出更保险
    "ui.uninstall_tk",
    "core.uninstall",
    "core.data_clean",
    "core.lockfile",
    "core.autostart",
    "core.shortcut",
]

a = Analysis(
    [os.path.join(ROOT, "uninstaller.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="uninstaller",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                                  # 窗口程序，不要黑框
    disable_windowed_traceback=False,
    icon=os.path.join(ROOT, "assets", "icon.ico"),
    version=VERSION_FILE,
    uac_admin=True,
)
