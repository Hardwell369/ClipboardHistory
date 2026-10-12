# ui/file_icons.py
"""
文件类型图标。

图标资源位于 assets/file_icons/，文件名形如：
    文件类型-缩略图-<类型名>.svg
“标准图”是带折角的文件形状，“缩略图”是圆角方块；
列表左侧用“缩略图”系列，正好铺满 48×48 的位置。

对外只暴露三件事：
    icon_name_for_item(item)  -> 该条记录该用哪个图标
    icon_pixmap(name, size)   -> 拿到渲染好的 QPixmap（带缓存）
    THUMB_SIZE                -> 图标边长，和 ItemWidget 保持一致
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QGuiApplication, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from core.models import ClipItem, ClipType
from core.paths import resource


# 图标边长（ui/item_widget.py 中 QLabel 的固定尺寸）
THUMB_SIZE = 48

_ICON_DIR = resource("assets", "file_icons")
_ICON_PREFIX = "文件类型-缩略图-"
_ICON_SUFFIX = ".svg"

# ------------------------- 图标名 -------------------------
ICON_TEXT = "记事本"       # 纯文本 / HTML 以外的文本类内容
ICON_FOLDER = "文件夹"
ICON_WORD = "Word"
ICON_SHEET = "工作表"
ICON_SLIDE = "幻灯片"
ICON_PDF = "PDF文档"
ICON_IMAGE = "图片文件"
ICON_AUDIO = "声音文件"
ICON_VIDEO = "视频文件"
ICON_ARCHIVE = "压缩文件"
ICON_LINK = "链接"         # URL / HTML / 快捷方式
ICON_UNKNOWN = "未知文件"


# ------------------------- 扩展名映射 -------------------------
_EXT_ICONS: dict[str, str] = {}


def _register(icon: str, exts: str) -> None:
    for ext in exts.split():
        _EXT_ICONS[ext] = icon


_register(ICON_WORD, "doc docx docm dot dotx rtf odt wps")
_register(ICON_SHEET, "xls xlsx xlsm xlsb xlt csv tsv ods et")
_register(ICON_SLIDE, "ppt pptx pps ppsx pot potx odp dps")
_register(ICON_PDF, "pdf xps")
_register(
    ICON_IMAGE,
    "png jpg jpeg jpe jfif gif bmp webp svg svgz ico tif tiff "
    "heic heif avif raw psd ai",
)
_register(ICON_AUDIO, "mp3 wav flac aac m4a ogg oga wma ape opus mid midi aiff amr")
_register(ICON_VIDEO, "mp4 avi mkv mov wmv flv webm m4v mpg mpeg mpe rm rmvb m2ts 3gp")
_register(ICON_ARCHIVE, "zip rar 7z tar gz tgz bz2 tbz xz zst z lzh arj cab iso jar")
_register(ICON_LINK, "lnk url website html htm mht mhtml webloc")
_register(
    ICON_TEXT,
    "txt log md markdown rst ini cfg conf json xml yaml yml toml "
    "py pyw js mjs cjs ts tsx jsx java c h cpp hpp cc cs go rs rb php "
    "sh bash bat cmd ps1 sql css less sass scss vue",
)


# ------------------------- 图标名判定 -------------------------
@lru_cache(maxsize=512)
def icon_name_for_path(path: str) -> str:
    """单个文件/文件夹 -> 图标名"""
    if not path:
        return ICON_UNKNOWN
    try:
        if os.path.isdir(path):
            return ICON_FOLDER
    except OSError:
        pass

    name = os.path.basename(path.rstrip("\\/"))
    ext = os.path.splitext(name)[1].lower().lstrip(".")
    if not ext:
        # 无扩展名：目录已判过，其余按未知处理
        return ICON_UNKNOWN
    return _EXT_ICONS.get(ext, ICON_UNKNOWN)


def icon_name_for_paths(paths: list[str]) -> str:
    """
    多条路径 -> 图标名。
    只选了一个：按类型给图标；
    选了多个：类型一致就用该类型图标，混选则用文件夹图标。
    """
    if not paths:
        return ICON_UNKNOWN
    if len(paths) == 1:
        return icon_name_for_path(paths[0])

    names = {icon_name_for_path(p) for p in paths}
    if len(names) == 1:
        return names.pop()
    return ICON_FOLDER


def _paths_of(item: ClipItem) -> list[str]:
    """FILES 记录的 content 是 JSON 路径列表"""
    if not item.content:
        return []
    try:
        data = json.loads(item.content)
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    if isinstance(data, list):
        return [p for p in data if isinstance(p, str) and p]
    return []


def icon_name_for_item(item: ClipItem) -> str:
    """一条剪贴板记录 -> 图标名（图片走缩略图，不会用到这里）"""
    if item.type == ClipType.FILES:
        return icon_name_for_paths(_paths_of(item))
    if item.type == ClipType.HTML:
        return ICON_LINK
    if item.type == ClipType.IMAGE:
        return ICON_IMAGE
    if item.type == ClipType.UNKNOWN:
        return ICON_UNKNOWN
    return ICON_TEXT


# ------------------------- 渲染 -------------------------
def icon_path(name: str) -> str:
    """图标名 -> svg 绝对路径"""
    return os.path.join(_ICON_DIR, f"{_ICON_PREFIX}{name}{_ICON_SUFFIX}")


def _default_dpr() -> float:
    screen = QGuiApplication.primaryScreen()
    return float(screen.devicePixelRatio()) if screen else 1.0


@lru_cache(maxsize=128)
def _render_icon(name: str, size: int, dpr: float) -> QPixmap:
    path = icon_path(name)
    if not os.path.exists(path):
        return QPixmap()

    renderer = QSvgRenderer(path)
    if not renderer.isValid():
        return QPixmap()

    # 位图按 dpr 放大，然后先设好 devicePixelRatio 再绘制：
    # 这样 QPainter 的逻辑坐标就是 size×size，svg 正好铺满整张位图。
    # （若反过来先画再设 dpr，绘制时的逻辑坐标会被放大，图标就只画进去一角）
    px = max(1, int(round(size * dpr)))
    pix = QPixmap(px, px)
    pix.setDevicePixelRatio(dpr)
    pix.fill(Qt.transparent)

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return pix


def icon_pixmap(name: str, size: int = THUMB_SIZE, dpr: float = 0.0) -> QPixmap:
    """
    取渲染好的图标。图标文件缺失或 svg 无效时返回空 QPixmap，
    由调用方决定兜底显示（ItemWidget 会退回显示类型首字）。

    结果按 (图标名, 边长, 缩放比) 缓存，刷新列表时不会重复解析 svg。
    缩放比取整到两位，避免不同显示器上出现大量近似缓存。
    """
    if dpr <= 0:
        dpr = _default_dpr()
    return _render_icon(name, int(size), round(float(dpr), 2))
