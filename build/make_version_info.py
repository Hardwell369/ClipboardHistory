# build/make_version_info.py
"""
由 core/version.py 生成 PyInstaller 用的版本信息文件（Windows exe 的
「属性 -> 详细信息」里那几个字段：文件版本、产品版本、说明、版权）。

为什么需要它：版本号只在 core/version.py 里写一次，打包时不该再手抄一遍，
抄漏了就会出现「关于框写 1.0.0、exe 属性写 0.9」这种对不上的情况。

用法（在项目根目录）：
    python build/make_version_info.py            # 写到 build/version_info.txt
    pyinstaller build/build.spec --noconfirm     # spec 会自动调用本脚本

生成的文件是 UTF-8 的 Python 字面量（PyInstaller 用 eval 读它）。
"""
from __future__ import annotations

import os
import sys

# 允许直接 python build/make_version_info.py 运行
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.version import (          # noqa: E402
    APP_NAME, APP_NAME_EN, COPYRIGHT, DESCRIPTION, __version__, version_tuple,
)


def build_content(role: str = "") -> str:
    """
    role 用来区分「主程序 / 安装程序 / 卸载程序」，只影响 exe 属性里的
    说明和原始文件名，版本号来源始终是 core/version.py。
    """
    major, minor, patch, build = version_tuple()
    version_str = ".".join(str(n) for n in (major, minor, patch, build))
    # 中文说明也写进去：资源管理器里一眼能看出这是干什么的
    if role == "setup":
        file_desc = f"{APP_NAME} 安装程序"
        original = "ClipboardHistory-Setup.exe"
    elif role == "uninstaller":
        file_desc = f"{APP_NAME} 卸载程序"
        original = "uninstaller.exe"
    else:
        file_desc = f"{APP_NAME}（{DESCRIPTION}）"
        original = f"{APP_NAME_EN}.exe"

    return f"""# 由 build/make_version_info.py 自动生成，不要手改。
# 版本号来源：core/version.py 的 __version__ = "{__version__}"
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({major}, {minor}, {patch}, {build}),
    prodvers=({major}, {minor}, {patch}, {build}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          '080404b0',
          [StringStruct('CompanyName', '{APP_NAME_EN}'),
           StringStruct('FileDescription', '{file_desc}'),
           StringStruct('FileVersion', '{version_str}'),
           StringStruct('InternalName', '{APP_NAME_EN}'),
           StringStruct('LegalCopyright', '{COPYRIGHT}'),
           StringStruct('OriginalFilename', '{original}'),
           StringStruct('ProductName', '{APP_NAME}'),
           StringStruct('ProductVersion', '{version_str}')])
      ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"""


def main(role: str = "") -> int:
    out_path = os.path.join(ROOT, "build", "version_info.txt")
    content = build_content(role)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"已生成 {out_path}（版本 {__version__}，角色 {role or '主程序'}）")

    # 立刻回读校验一次：PyInstaller 是用 eval 读它的，语法/结构错了要在打包前发现
    try:
        from PyInstaller.utils.win32.versioninfo import (   # type: ignore
            FixedFileInfo, StringFileInfo, StringStruct, StringTable,
            VarFileInfo, VarStruct, VSVersionInfo,
        )
        ns = {
            "VSVersionInfo": VSVersionInfo, "FixedFileInfo": FixedFileInfo,
            "StringFileInfo": StringFileInfo, "StringTable": StringTable,
            "StringStruct": StringStruct, "VarFileInfo": VarFileInfo,
            "VarStruct": VarStruct,
        }
        info = eval(content, ns)        # noqa: S307 - 内容由本脚本生成
        fields = {s.name: s.val for s in info.kids[0].kids[0].kids}
        print("校验通过：FileVersion =", fields.get("FileVersion"),
              "| ProductName =", fields.get("ProductName"))
        if fields.get("FileVersion") != ".".join(str(n) for n in version_tuple()):
            print("版本号与 core/version.py 不一致！")
            return 1
    except ImportError:
        print("（没装 PyInstaller，跳过结构校验）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
