# src/version.py
"""
版本与产品信息。

这里是全项目唯一的版本号来源：
- 关于对话框（ui/about_dialog.py）直接读
- main.py 用它设置 QApplication 的版本号
- build/version_info.txt 由 build/make_version_info.py 从这里生成，
  写进 exe 的「属性 -> 详细信息」（PyInstaller version 参数）

发版时只改 __version__，其余地方不用动。
"""
from __future__ import annotations

import sys

# ------------------------- 唯一版本号 -------------------------
__version__ = "1.0.2"

APP_NAME = "剪贴板历史"                  # 界面上显示的名字
APP_NAME_EN = "ClipboardHistory"         # 英文名 / 进程名 / 自启注册表键名
ORG_NAME = "ClipboardHistory"
DESCRIPTION = "Windows 剪贴板历史记录工具"

# ------------------------- 作者信息 -------------------------
# 关于对话框、用户手册、安装器、exe 属性（发布者）都从这里取，改一处即可
AUTHOR = "吴杰"
AUTHOR_EMAIL = "1794185873@qq.com"
COPYRIGHT = f"Copyright (C) 2026 {AUTHOR}"


# ------------------------- 派生信息 -------------------------
def version_tuple(version: str | None = None) -> tuple[int, int, int, int]:
    """
    "1.2.3" -> (1, 2, 3, 0)

    版本信息文件（VS_VERSIONINFO）要求四段数字，缺失的补 0；
    预发布后缀（如 1.2.3-beta）只取数字部分。
    """
    raw = (version or __version__).strip()
    nums: list[int] = []
    for part in raw.split("."):
        digits = ""
        for ch in part:
            if ch.isdigit():
                digits += ch
            else:
                break
        nums.append(int(digits) if digits else 0)
    while len(nums) < 4:
        nums.append(0)
    return tuple(nums[:4])  # type: ignore[return-value]


def version_string() -> str:
    """带 v 前缀，用于界面显示：v1.0.0"""
    return f"v{__version__}"


def version_text() -> str:
    """关于框 / 日志里用的整行版本信息"""
    return f"{APP_NAME} {version_string()}"


def runtime_text() -> str:
    """运行方式说明：源码运行还是打包后运行"""
    if getattr(sys, "frozen", False):
        return "可执行程序（exe）"
    return f"源码（Python {sys.version.split()[0]}）"
