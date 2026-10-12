# ui/elided_label.py
"""
带「行数上限 + 省略号」的文本标签。

为什么不用 QLabel 的 wordWrap：
QLabel 在「文本框比文本矮」的时候是**按垂直居中裁剪**的——实测卡片里
4 行多（64px）的文本塞进 37px 高的 label 后，第一行被顶到框外、和后面的
行叠在一起，看起来就是「文字跑出框了」。QLabel 也没有「多行折行 + 最后一行
省略号」的能力（QFontMetrics.elidedText 只处理单行）。

所以这里自己排：
1. 用 QTextLayout 按可用宽度折行，算出真正放得下的行数；
2. 最后一行如果还有放不下的内容，用 …（省略号）收尾；
3. 从 top 开始画（放得下时垂直居中），**永远画在自己的矩形里**，
   并把裁剪区限制在自身范围内，保证任何情况下都不越界。
"""
from __future__ import annotations

import re

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QPainter, QTextLayout, QTextOption
from PySide6.QtWidgets import QLabel, QSizePolicy, QStyle, QStyleOption

# 连续空白（含换行、制表符）压成一个空格：预览只做一行/两行摘要，
# 保留原始换行会把「几行文本挤在一个框里」变得更不可控
_WHITESPACE_RE = re.compile(r"[\s\u00a0\u3000]+")

DEFAULT_MAX_LINES = 2


class ElidedLabel(QLabel):
    """最多显示 max_lines 行、放不下的内容用省略号收尾的 QLabel"""

    def __init__(self, text: str = "", max_lines: int = DEFAULT_MAX_LINES,
                 parent=None):
        super().__init__(parent)
        self._full_text = ""
        self._max_lines = max(1, int(max_lines))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        if text:
            self.setText(text)

    # ---------------------------------------------------------
    # 文本
    # ---------------------------------------------------------
    def setText(self, text) -> None:            # noqa: N802 - 保持 QLabel 接口
        # 只留一行内的空白，避免出现大段空白把有效内容顶掉
        self._full_text = _WHITESPACE_RE.sub(" ", str(text or "")).strip()
        super().setText(self._full_text)        # 让 text() / 无障碍拿到原文
        self.updateGeometry()
        self.update()

    def full_text(self) -> str:
        return self._full_text

    @property
    def max_lines(self) -> int:
        return self._max_lines

    def set_max_lines(self, lines: int) -> None:
        self._max_lines = max(1, int(lines))
        self.updateGeometry()
        self.update()

    # ---------------------------------------------------------
    # 尺寸：高度最多 max_lines 行，宽度跟着布局走
    # ---------------------------------------------------------
    def line_spacing(self) -> int:
        return max(1, self.fontMetrics().lineSpacing())

    def _line_limit(self) -> int:
        """当前矩形高度下真正放得下的行数（不会超过 max_lines）"""
        limit = self._max_lines
        h = self.height()
        if h > 0:
            limit = min(limit, max(1, h // self.line_spacing()))
        return max(1, limit)

    def sizeHint(self) -> QSize:                # noqa: N802
        return QSize(super().sizeHint().width(),
                     self.line_spacing() * self._max_lines)

    def minimumSizeHint(self) -> QSize:         # noqa: N802
        return QSize(0, self.line_spacing())

    # ---------------------------------------------------------
    # 排版
    # ---------------------------------------------------------
    def visible_lines(self, width: int | None = None) -> list[str]:
        """
        按宽度折行后**真正会画出来**的行（最多 _line_limit() 行）。

        最后一行放不下时带省略号；文本短于上限时就是原样的一行。
        """
        text = self._full_text
        if not text:
            return []

        if width is None:
            width = self.contentsRect().width()
        width = max(1, int(width))
        limit = self._line_limit()
        fm = self.fontMetrics()

        layout = QTextLayout(text, self.font())
        option = QTextOption()
        # 中文没有空格、URL 又不带空格，必须在任意位置都能断行
        option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)

        lines: list[str] = []
        layout.beginLayout()
        try:
            while len(lines) < limit:
                line = layout.createLine()
                if not line.isValid():
                    break
                line.setLineWidth(width)
                start = line.textStart()
                if len(lines) == limit - 1:
                    # 最后一行：剩下的全给它，放不下由 elidedText 加省略号
                    lines.append(fm.elidedText(text[start:], Qt.ElideRight, width))
                else:
                    lines.append(text[start:start + line.textLength()].rstrip())
        finally:
            layout.endLayout()
        return lines

    # ---------------------------------------------------------
    # 绘制
    # ---------------------------------------------------------
    def paintEvent(self, event) -> None:        # noqa: N802
        painter = QPainter(self)
        # 先让样式表把背景/边框画出来（例如缩略图的灰底、卡片的圆角）
        option = QStyleOption()
        option.initFrom(self)
        self.style().drawPrimitive(QStyle.PE_Widget, option, painter, self)

        lines = self.visible_lines()
        if not lines:
            return

        rect = self.contentsRect()
        painter.setClipRect(rect)
        painter.setPen(self.palette().color(self.foregroundRole()))

        spacing = self.line_spacing()
        total = spacing * len(lines)
        # 放得下就垂直居中（单行看起来更自然），放不下就顶格画，绝不越过下边
        top = rect.top() + max(0, (rect.height() - total) // 2)

        for i, line in enumerate(lines):
            line_rect = QRect(rect.left(), top + i * spacing, rect.width(), spacing)
            painter.drawText(line_rect, int(Qt.AlignLeft | Qt.AlignVCenter), line)
