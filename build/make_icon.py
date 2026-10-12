# build/make_icon.py
"""
由 assets/icon.svg 生成 assets/icon.ico（多尺寸）。

为什么需要它：icon.ico 得是真正的 Windows 图标，PyInstaller 才能嵌进 exe
（托盘那侧 Qt 会按内容嗅探格式，把 svg 改名成 ico 也能显示，但打包时不行）。

用法（在项目根目录）：
    python build/make_icon.py

依赖 Pillow（只在生成图标时用得到，不是运行时依赖）：
    pip install pillow
"""
from __future__ import annotations

import io
import os
import sys

SIZES = [16, 24, 32, 48, 64, 128, 256]


def main() -> int:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    svg_path = os.path.join(root, "assets", "icon.svg")
    ico_path = os.path.join(root, "assets", "icon.ico")

    if not os.path.exists(svg_path):
        print(f"找不到矢量源文件：{svg_path}")
        return 1

    try:
        from PIL import Image
    except ImportError:
        print("需要 Pillow：pip install pillow")
        return 1

    from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtWidgets import QApplication

    # QSvgRenderer / QImage 需要一个 QApplication（不显示任何窗口）
    app = QApplication.instance() or QApplication(sys.argv)
    _ = app

    renderer = QSvgRenderer(svg_path)
    if not renderer.isValid():
        print(f"svg 无效：{svg_path}")
        return 1

    # 每个尺寸单独渲染，小图标才不会有缩放糊边
    images = []
    for size in SIZES:
        img = QImage(size, size, QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        painter = QPainter(img)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()

        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        img.save(buf, "PNG")
        buf.close()
        images.append(Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA"))

    # 最大的一张作为底图，其余尺寸用各自渲染好的图，避免被拉伸
    base = images[-1]
    base.save(
        ico_path,
        format="ICO",
        sizes=[(im.width, im.height) for im in images],
        append_images=images[:-1],
    )

    with Image.open(ico_path) as check:
        got = sorted(check.ico.sizes())
    print(f"已生成 {ico_path}")
    print(f"包含尺寸：{got}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
