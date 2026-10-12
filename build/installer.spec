# build/installer.spec
"""
安装程序（ClipboardHistory-Setup.exe）的 PyInstaller 配置：单文件 exe。

用法（在项目根目录执行，**必须先打主程序**）：
    pyinstaller build/build.spec --clean --noconfirm
    pyinstaller build/installer.spec --clean --noconfirm
    或者干脆一条命令：python build/build_all.py

产物：
    dist/ClipboardHistory-Setup.exe      自包含的安装程序，直接发给用户即可

它是怎么把自己装出去的：
    主程序的整个目录（dist/ClipboardHistory，含 _internal 和 uninstaller.exe）
    会被塞进这个 exe 的 payload/ 目录里；运行时 PyInstaller 先解包到临时目录，
    安装向导再从 _MEIPASS/payload 把文件复制到用户选的安装目录
    （见 core/installer.py）。payload 里没有卸载器时，这里会自动把
    dist/uninstaller.exe 补进 payload/（先单独打 uninstaller.spec 才有）。

清单同样要求管理员权限（uac_admin=True）：
安装要写 Program Files、HKCU 卸载项、桌面/开始菜单快捷方式和计划任务，
不提权这些全都做不了；主程序本身也要求提权，两者保持一致。

体积说明：payload 里带着 PySide6，所以 Setup.exe 有一百多 MB。
如果想要小一点的安装包，用 Inno Setup / NSIS 之类的工具包一层即可，
本向导也可以单独使用（主程序 exe 加 --install 参数）。
"""
import os
import sys

try:
    SPEC_DIR = SPECPATH                     # noqa: F821
except NameError:
    SPEC_DIR = os.path.dirname(os.path.abspath(__file__))

ROOT = os.path.abspath(os.path.join(SPEC_DIR, os.pardir))
PAYLOAD = os.path.join(ROOT, "dist", "ClipboardHistory")
UNINSTALLER = os.path.join(ROOT, "dist", "uninstaller.exe")

if not os.path.isdir(PAYLOAD) or not os.path.isfile(
        os.path.join(PAYLOAD, "ClipboardHistory.exe")):
    raise SystemExit(
        f"[installer.spec] 找不到主程序目录：{PAYLOAD}\n"
        f"请先执行：pyinstaller build/build.spec --clean --noconfirm")

# ---- exe 版本信息（属性 -> 详细信息）----
VERSION_FILE = None
try:
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    sys.path.insert(0, SPEC_DIR)
    from make_version_info import main as make_version_info   # noqa: E402

    make_version_info("setup")
    VERSION_FILE = os.path.join(SPEC_DIR, "version_info.txt")
except Exception as e:                      # noqa: BLE001
    print(f"[installer.spec] 生成版本信息失败，exe 将不带版本号：{e}")

datas = [
    (PAYLOAD, "payload"),                              # 要安装的程序本体
    (os.path.join(ROOT, "assets"), "assets"),          # 向导图标
    (os.path.join(ROOT, "ui", "style.qss"), "ui"),     # 向导样式
]

# 卸载器是独立 exe，正常情况下已经在 dist/ClipboardHistory 里（build_all.py 放的）。
# 这里再兜一层：单独跑 installer.spec 时也能把它带进 payload。
if not os.path.isfile(os.path.join(PAYLOAD, "uninstaller.exe")):
    if os.path.isfile(UNINSTALLER):
        datas.append((UNINSTALLER, "payload"))
        print("[installer.spec] payload 里没有卸载器，已自动补入 dist/uninstaller.exe")
    else:
        print("[installer.spec] 警告：没找到 dist/uninstaller.exe，"
              "装出来的程序会退回「主程序 --uninstall」的老卸载流程")

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
    [os.path.join(ROOT, "installer.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=[],
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
    name="ClipboardHistory-Setup",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=os.path.join(ROOT, "assets", "icon.ico"),
    version=VERSION_FILE,
    uac_admin=True,
)
