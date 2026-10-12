# build/build.spec
"""
PyInstaller 打包配置。

用法（在项目根目录执行）：
    pip install pyinstaller
    pyinstaller build/build.spec --noconfirm

产物：
    dist/ClipboardHistory/ClipboardHistory.exe     （onedir，启动快，推荐）

打全套（主程序 + 卸载器 + 安装程序，顺序有讲究）用：
    python build/build_all.py
（卸载器、安装程序是另外两个 spec：build/uninstaller.spec、build/installer.spec）

关于资源路径：应用通过 core/paths.py 的 resource() 取资源，
打包后指向 sys._MEIPASS，所以下面的 datas 必须保持
    assets/...        和        ui/style.qss
这样的相对结构，否则打包后图标、样式会找不到。

关于版本号：版本号只在 core/version.py 里写一次。打包时这里会自动调用
build/make_version_info.py 生成 version_info.txt，写进 exe 的
「属性 -> 详细信息」，所以关于框和 exe 属性永远对得上。
"""
import os
import sys

# PyInstaller 执行 spec 时会注入 SPECPATH（本文件所在目录）
try:
    SPEC_DIR = SPECPATH                     # noqa: F821
except NameError:                           # 直接 python build.spec 时兜底
    SPEC_DIR = os.path.dirname(os.path.abspath(__file__))

ROOT = os.path.abspath(os.path.join(SPEC_DIR, os.pardir))

# ---- 生成 exe 版本信息（文件版本/产品名/版权），失败也不该挡住打包 ----
VERSION_FILE = None
try:
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    sys.path.insert(0, SPEC_DIR)
    from make_version_info import main as make_version_info   # noqa: E402

    make_version_info()
    VERSION_FILE = os.path.join(SPEC_DIR, "version_info.txt")
except Exception as e:                      # noqa: BLE001 - 打包时给出提示即可
    print(f"[build.spec] 生成版本信息失败，exe 将不带版本号：{e}")
    VERSION_FILE = None

# ---- 需要一起打包的数据文件 ----
datas = [
    (os.path.join(ROOT, "assets"), "assets"),          # 托盘图标 + 文件类型 svg
    (os.path.join(ROOT, "ui", "style.qss"), "ui"),     # 样式表
    (os.path.join(ROOT, "docs"), "docs"),              # 用户手册（托盘「帮助」打开）
]

# pynput 的后端模块是按平台动态导入的，显式收集，免得打包后快捷键失效
from PyInstaller.utils.hooks import collect_submodules   # noqa: E402

hiddenimports = collect_submodules("pynput")

# 明显用不到的东西，剔掉能小一圈；拿不准的别往里加
excludes = [
    "tkinter",
    "unittest",
    "pydoc_data",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtQuick",
    "PySide6.QtQml",
]

a = Analysis(
    [os.path.join(ROOT, "main.py")],
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
    [],
    exclude_binaries=True,
    name="ClipboardHistory",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                                  # 托盘程序，不要黑框
    disable_windowed_traceback=False,
    icon=os.path.join(ROOT, "assets", "icon.ico"),
    version=VERSION_FILE,                           # exe 属性里的版本号
    # 清单里要求管理员权限（requireAdministrator）。为什么：
    # 安全软件会把「未签名的普通权限 exe」当不可信程序限制——实测非提权运行时
    # %APPDATA% 写入、HKCU\Run 写入、启动 explorer.exe、托盘图标全部被拦；
    # 以管理员身份运行则一切正常。代价：每次启动一次 UAC 提示，
    # 且开机自启必须走「最高权限计划任务」（见 core/autostart.py）。
    # 如果以后做了代码签名 / 加了杀软白名单，可以把这行改回 False。
    uac_admin=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ClipboardHistory",
)

# ---------------------------------------------------------------------------
# 想打成单文件 exe（分发方便，但每次启动都要先解包，会慢 1~2 秒）：
# 把上面 EXE/COLLECT 换成下面这段即可：
#
# exe = EXE(
#     pyz, a.scripts, a.binaries, a.datas, [],
#     name="ClipboardHistory", debug=False, strip=False, upx=False,
#     console=False, icon=os.path.join(ROOT, "assets", "icon.ico"),
# )
# ---------------------------------------------------------------------------
