# src/clipboard_io.py
from __future__ import annotations

import json
import os
import re
import uuid
from typing import Optional

import win32clipboard
import win32con
import win32gui
from PySide6.QtGui import QClipboard, QImage
from PySide6.QtWidgets import QApplication

from src.models import ClipItem, ClipType


# ------------------------- 常量 -------------------------
# 密码类关键词。注意：**不再拿它们在正文里做子串匹配**（那是老版本的误杀源头，
# 见 sensitive_reason() 的说明），现在只作为「赋值形态」的键名和界面提示词。
PASSWORD_KEYWORDS = (
    "password", "passwd", "pwd", "密码", "口令", "passcode",
)
SENSITIVE_WINDOW_TITLES = (
    "password", "密码", "1password", "bitwarden", "keepass",
    "lastpass", "dashlane", "enpass",
)

# 凭证赋值：password: xxx / password=xxx / "password": "xxx" / 密码：xxx / 口令=xxx
PASSWORD_ASSIGN_RE = re.compile(
    r"(?:(?<![A-Za-z])(?:password|passwd|pwd|passcode)(?![A-Za-z])|密码|口令)"
    r"\s*[\"']?\s*[:=：]\s*([^\s]{1,64})",
    re.IGNORECASE,
)
# 密钥长这样：可打印 ASCII、不含空格、不含中文（"默认开启" 这种说明文字直接排除）
_ASCII_VALUE_RE = re.compile(r"[!-~]{1,64}")
# 值里带括号 -> 判定为代码/表达式（pwd = os.getcwd() 不该被当成密码）
_CODE_CHARS = set("(){}[]<>")
_TRIM_CHARS = "\"'`,;:。；，、)]}>"

IMAGE_EXTS = {"png": "PNG", "jpg": "JPG", "jpeg": "JPG", "bmp": "BMP"}


# ------------------------- 读取剪贴板 -------------------------
def read_clipboard() -> Optional[tuple[ClipType, str, str, int, Optional[bytes]]]:
    """
    读取系统剪贴板，返回：
        (type, content, preview, size, image_bytes_or_None)

    优先级：文件 > 纯文本 > 图片 > HTML
    —— 只要存在纯文本，就统一按纯文本处理，避免浏览器/Word
       复制时同时携带 text/html 导致内容被存成 HTML。
    —— 文本也优先于图片：Excel / WPS 表格 / 部分文档编辑器复制时，
       剪贴板上同时有 CF_UNICODETEXT 和整段位图。以前优先取图片的结果是
       ① 想要的文本在历史里变成了一条图片；② 整页位图很大，一旦超过
       单条上限，这一次复制就整个丢掉（连图片都没有）。
    """
    cb: QClipboard = QApplication.clipboard()
    md = cb.mimeData()

    # 1. 文件优先（资源管理器复制文件时也会带 text/uri-list）
    if md.hasUrls():
        paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
        if paths:
            content = json.dumps(paths, ensure_ascii=False)
            preview = _make_files_preview(paths)
            size = len(content.encode("utf-8"))
            return ClipType.FILES, content, preview, size, None

    # 2. 纯文本（提到图片和 HTML 之前）
    if md.hasText():
        text = md.text()
        if text and text.strip():
            preview = _make_preview(text)
            size = len(text.encode("utf-8"))
            return ClipType.TEXT, text, preview, size, None

    # 3. 图片
    if md.hasImage():
        img: QImage = cb.image()
        if not img.isNull():
            png_bytes = _qimage_to_png_bytes(img)
            preview = f"图片 {img.width()}×{img.height()}"
            size = len(png_bytes)
            return ClipType.IMAGE, "", preview, size, png_bytes

    # 4. HTML（仅在没有任何纯文本时才走这里）
    if md.hasHtml():
        html = md.html()
        if html:
            preview = _make_preview(html)
            size = len(html.encode("utf-8"))
            return ClipType.HTML, html, preview, size, None

    return None

def get_foreground_window_title() -> str:
    """获取前台窗口标题（用于密码判断）"""
    try:
        return win32gui.GetWindowText(win32gui.GetForegroundWindow())
    except Exception:
        return ""


def sensitive_reason(text: str, foreground_title: str = "") -> str:
    """
    敏感内容判断，命中时返回原因（写日志用），没命中返回空串。

    只在两种情况下拦：

    1. 来源窗口的标题像密码管理器 / 密码页面（最可靠的信号）；
    2. 正文里出现「凭证赋值」——键名后面跟冒号或等号，而且**值本身像密钥**
       （可打印 ASCII、不含空格和中文、不含括号）。

    为什么不拿关键词在整段正文里做子串匹配（老版本的做法）：
    全选（Ctrl+A）复制的往往是整篇文档，只要正文里**提到**过关键词就被整条丢掉。
    实测：README / 用户手册里存在「密码判断」「密码：推荐开」这类说明文字，
    于是 Ctrl+A + Ctrl+C 之后历史里什么都没有——用户看到的就是「复制了却没记录」。

        password: hunter2         -> 拦（凭证赋值，值是密钥）
        "password": "s3cret"      -> 拦
        密码：abc123               -> 拦
        密码判断 / 密码：推荐开      -> 放行（值不是密钥）
        pwd = os.getcwd()         -> 放行（值里有括号，是代码）
    """
    title = (foreground_title or "").lower()
    for key in SENSITIVE_WINDOW_TITLES:
        if key in title:
            return f"来源窗口标题含「{key}」"

    for match in PASSWORD_ASSIGN_RE.finditer(text or ""):
        key = match.group(0)[: match.start(1) - match.start(0)]
        key = key.strip().rstrip(":：=").strip().strip("\"'").strip()
        value = match.group(1).strip(_TRIM_CHARS)
        if not value or not _ASCII_VALUE_RE.fullmatch(value):
            continue                    # 值是中文/带空格 -> 说明文字，不是密码
        if set(value) & _CODE_CHARS:
            continue                    # 值是表达式 -> 代码，不是密码
        # 日志里绝不写值本身，只写长度
        return f"正文出现凭证赋值：{key}（值 {len(value)} 字符）"
    return ""


def is_sensitive(text: str, foreground_title: str = "") -> bool:
    """密码/敏感内容判断（True = 不该记录）"""
    return bool(sensitive_reason(text, foreground_title))


def clipboard_formats() -> list[str]:
    """当前剪贴板暴露给 Qt 的 mime 类型列表（只用于排查日志，不参与判断）"""
    try:
        md = QApplication.clipboard().mimeData()
        return sorted(md.formats())
    except Exception:                   # noqa: BLE001 - 没有 QApplication 等情况
        return []


# ------------------------- 写入剪贴板 -------------------------
def write_text_to_clipboard(text: str) -> None:
    QApplication.clipboard().setText(text)


def write_image_to_clipboard(img: QImage) -> None:
    QApplication.clipboard().setImage(img)


def write_files_to_clipboard(paths: list[str]) -> None:
    """
    把文件对象写入剪贴板，可在资源管理器 Ctrl+V 粘贴。
    使用 CF_HDROP 格式。
    """
    if not paths:
        return
    files = "\0".join(paths) + "\0\0"
    data = files.encode("utf-16-le")

    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        # DROPFILES 结构头 + 文件列表
        dropfiles = _make_dropfiles_header()
        win32clipboard.SetClipboardData(win32con.CF_HDROP, dropfiles + data)
    finally:
        win32clipboard.CloseClipboard()


def write_item_to_clipboard(item: ClipItem, storage) -> bool:
    """
    把一条历史记录写回剪贴板。
    - TEXT  : 若落盘，从磁盘读取正文
    - IMAGE : 从 file_path 加载图片
    - FILES : content 是 JSON 路径列表
    - HTML  : content 是 HTML 源码（同时写纯文本兜底）
    """
    if item.type == ClipType.TEXT:
        text = storage.read_text(item.id) or item.content
        write_text_to_clipboard(text)
        return True

    if item.type == ClipType.IMAGE:
        if item.file_path and os.path.exists(item.file_path):
            img = QImage(item.file_path)
            if not img.isNull():
                write_image_to_clipboard(img)
                return True
        return False

    if item.type == ClipType.FILES:
        try:
            paths = json.loads(item.content)
        except (json.JSONDecodeError, TypeError):
            return False
        if not paths:
            return False
        write_files_to_clipboard(paths)
        return True

    if item.type == ClipType.HTML:
        # 简单处理：写纯文本兜底
        write_text_to_clipboard(item.content)
        return True

    return False


# ------------------------- 落盘辅助 -------------------------
def save_image_bytes(image_bytes: bytes, files_dir: str) -> str:
    """把 PNG 字节写入磁盘，返回路径"""
    os.makedirs(files_dir, exist_ok=True)
    fname = f"{uuid.uuid4().hex}.png"
    path = os.path.join(files_dir, fname)
    with open(path, "wb") as f:
        f.write(image_bytes)
    return path


# ------------------------- 内部工具 -------------------------
def _qimage_to_png_bytes(img: QImage) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba)


def _make_preview(text: str, max_len: int = 120) -> str:
    text = text.replace("\r\n", "\n").replace("\n", " ").strip()
    return text[:max_len]


def _make_files_preview(paths: list[str]) -> str:
    names = [os.path.basename(p) or p for p in paths]
    if len(names) == 1:
        return names[0]
    return f"{names[0]} 等 {len(names)} 个文件"


def _make_dropfiles_header() -> bytes:
    """
    DROPFILES 结构：
        DWORD pFiles;  POINT pt;  BOOL fNC;  BOOL fWide;
    这里 pFiles = 20（结构大小），pt = (0,0)，fNC = 0，fWide = 1
    """
    import struct

    return struct.pack("<IiiII", 20, 0, 0, 0, 1)