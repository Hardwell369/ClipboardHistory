# src/models.py
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class ClipType(str, Enum):
    """剪贴板内容类型"""
    TEXT = "text"
    IMAGE = "image"
    FILES = "files"
    HTML = "html"
    UNKNOWN = "unknown"     # 库里出现本版本不认识的 type 时的兜底，避免整列表渲染失败


@dataclass
class ClipItem:
    """
    一条剪贴板记录。

    - TEXT  : content 存文本；若超过落盘阈值，content 为空，file_path 指向 .txt
    - IMAGE : content 为空，file_path 指向图片文件
    - FILES : content 存 JSON 字符串（路径列表），file_path 为空
    - HTML  : content 存 HTML 源码
    """
    id: int | None = None
    type: ClipType = ClipType.TEXT
    content: str = ""
    preview: str = ""
    file_path: str | None = None
    size: int = 0
    created_at: float = field(default_factory=time.time)

    # ---- 便捷属性 ----
    @property
    def is_text_on_disk(self) -> bool:
        """文本是否落盘存储"""
        return self.type == ClipType.TEXT and bool(self.file_path)

    @property
    def is_image(self) -> bool:
        return self.type == ClipType.IMAGE

    @property
    def is_files(self) -> bool:
        return self.type == ClipType.FILES

    def to_row(self) -> tuple:
        """转为 SQLite 插入行"""
        return (
            self.type.value,
            self.content,
            self.preview,
            self.file_path,
            self.size,
            self.created_at,
        )

    @classmethod
    def from_row(cls, row: tuple) -> "ClipItem":
        """从 SQLite 行构造"""
        try:
            clip_type = ClipType(row[1])
        except ValueError:
            # 未知类型（例如旧版本读到新版本写入的数据）不该让整个列表炸掉
            clip_type = ClipType.UNKNOWN
        return cls(
            id=row[0],
            type=clip_type,
            content=row[2] or "",
            preview=row[3] or "",
            file_path=row[4],
            size=row[5] or 0,
            created_at=row[6] or 0.0,
        )