# ui/main_window.py
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QPoint, QRect, QEvent, Signal
from PySide6.QtGui import QCursor, QScreen
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
    QPushButton, QLabel, QFrame, QApplication, QMessageBox,
)

from core.clipboard_io import write_item_to_clipboard
from core.models import ClipItem, ClipType
from core.storage import Storage
from ui.edit_dialog import EditTextDialog
from ui.item_widget import ItemWidget, ITEM_HEIGHT, ITEM_MARGIN
from ui.utils import screen_for_rect, screen_for_widget


# ------------------------- 常量 -------------------------
DEFAULT_WIDTH = 300              # 默认宽度
DEFAULT_HEIGHT_COLLAPSED = 120   # 折叠态默认高度（标题栏 + 1 条卡片 + 边距）
MIN_WIDTH = 220
MIN_HEIGHT = 90                  # 最小尺寸
EXPANDED_MAX_HEIGHT = 620        # 展开后最大高度
EDGE_STRIP_WIDTH = 5             # 贴边细条宽度（参考 Windows 文本编辑器）
EXPAND_DELAY_MS = 120            # 悬停展开延迟
COLLAPSE_DELAY_MS = 500          # 移出收起延迟
EDGE_SNAP_THRESHOLD = 8          # 距屏幕边缘多少像素触发贴边


def snap_region(window_rect: QRect, area: QRect,
                threshold: int = EDGE_SNAP_THRESHOLD) -> str:
    """
    窗口贴住了某块屏幕的哪条边：'left' / 'right' / ''（没贴住）。

    单独拎出来是为了能直接拿矩形做单元测试：
    多屏的贴边判定只看这两块矩形，跟 Qt 的窗口对象无关。
    """
    if window_rect.left() <= area.left() + threshold:
        return "left"
    if window_rect.right() >= area.right() - threshold:
        return "right"
    return ""


class MainWindow(QWidget):
    """
    主窗口：
    - 无边框、圆角、可拖拽、可拉伸
    - 默认只显示最新一条
    - 悬停 -> 向下展开为多条卡片纵向列表
    - 移出 -> 延时收起
    - 拖到屏幕边缘 -> 贴边细条（持久），悬停探出，移开缩回
    - 空白处右键 -> 弹出与托盘右键一致的菜单（卡片上的右键仍是单条菜单）
    """

    item_added = Signal()
    context_menu_requested = Signal(QPoint)     # 全局坐标
    minimized_to_tray = Signal()                # 「−」收进托盘（主程序用来发气泡）

    def __init__(self, storage: Storage, settings: dict | None = None):
        super().__init__()
        self.storage = storage
        self.settings = settings or {}

        self._expanded = False
        self._snapped = False                   # 当前是否收缩为细条
        self._snap_side: str | None = None      # 'left' / 'right' / None，持久贴边方向
        self._normal_width = DEFAULT_WIDTH      # 非细条状态下的宽度（探出时恢复用）
        self.edge_strip_width = EDGE_STRIP_WIDTH    # 先给个默认，_load_settings 会覆盖
        # 拖拽/拉伸过程中鼠标所在的显示器：刚贴边那一刻鼠标可能已经离开窗口，
        # 光看窗口位置会选错屏，所以这里记一份
        self._drag_snap_screen: QScreen | None = None
        self._drag_offset: QPoint | None = None
        self._resizing = False
        self._resize_edge = ""
        self._resize_start_geo: QRect | None = None
        self._resize_start_pos: QPoint | None = None

        self._load_settings()

        self._build_window()
        self._build_ui()
        self._setup_timers()
        self.refresh()

    def _load_settings(self) -> None:
        """把设置里与窗口有关的项读进来（细条宽度、延迟、展开条数）"""
        self.expand_count = int(self.settings.get("expand_count", 10))
        self.edge_strip_width = self._clamp_strip_width(
            self.settings.get("edge_strip_width", EDGE_STRIP_WIDTH))
        self.expand_delay_ms = int(self.settings.get("expand_delay_ms", EXPAND_DELAY_MS))
        self.collapse_delay_ms = int(
            self.settings.get("collapse_delay_ms", COLLAPSE_DELAY_MS))

    @staticmethod
    def _clamp_strip_width(value) -> int:
        """细条宽度限制在设置对话框允许的 1~20px，配置被手改坏了也不至于出怪样子"""
        try:
            return max(1, min(20, int(value)))
        except (TypeError, ValueError):
            return EDGE_STRIP_WIDTH

    # ---------------------------------------------------------
    # 窗口基础
    # ---------------------------------------------------------
    def _build_window(self) -> None:
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.Tool
            | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setMinimumSize(MIN_WIDTH, MIN_HEIGHT)
        self.resize(DEFAULT_WIDTH, DEFAULT_HEIGHT_COLLAPSED)

        self.container = QFrame(self)
        self.container.setObjectName("MainContainer")

    # ---------------------------------------------------------
    # UI
    # ---------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.container)

        root = QVBoxLayout(self.container)
        root.setContentsMargins(6, 4, 6, 6)
        root.setSpacing(4)

        root.addLayout(self._build_title_bar())

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(ITEM_MARGIN)
        self.list_layout.addStretch(1)

        self.scroll.setWidget(self.list_container)
        root.addWidget(self.scroll, 1)

        self.empty_label = QLabel("暂无剪贴板记录")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setObjectName("EmptyLabel")
        root.addWidget(self.empty_label)
        self.empty_label.hide()

    def _build_title_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setContentsMargins(2, 0, 2, 0)
        bar.setSpacing(4)

        self.title_label = QLabel("剪贴板历史")
        self.title_label.setObjectName("TitleLabel")
        bar.addWidget(self.title_label)
        bar.addStretch(1)

        self.btn_min = QPushButton("−")
        self.btn_min.setToolTip("收进托盘")
        self.btn_close = QPushButton("×")
        self.btn_close.setToolTip("隐藏窗口（程序继续在托盘里运行）")
        for b in (self.btn_min, self.btn_close):
            b.setFixedSize(24, 24)
            b.setObjectName("TitleButton")
            b.setCursor(Qt.PointingHandCursor)
        # 「−」不是真最小化：本窗口是 Qt::Tool（没有任务栏按钮），Windows 会把
        # 最小化的它缩成 237x39 的小条塞到屏幕左下角并且一直「可见」，
        # 点托盘还唤不回来。托盘程序就按托盘程序的方式办：收进托盘。
        self.btn_min.clicked.connect(self.minimize_to_tray)
        self.btn_close.clicked.connect(self.hide)

        bar.addWidget(self.btn_min)
        bar.addWidget(self.btn_close)
        return bar

    # ---------------------------------------------------------
    # 定时器
    # ---------------------------------------------------------
    def _setup_timers(self) -> None:
        self._expand_timer = QTimer(self)
        self._expand_timer.setSingleShot(True)
        self._expand_timer.timeout.connect(self._do_expand)

        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.timeout.connect(self._do_collapse)

    # ---------------------------------------------------------
    # 数据刷新
    # ---------------------------------------------------------
    def refresh(self) -> None:
        items = self.storage.list_all()

        while self.list_layout.count() > 1:
            child = self.list_layout.takeAt(0)
            w = child.widget()
            if w:
                w.deleteLater()

        if not items:
            self.empty_label.show()
            self.scroll.hide()
            return
        self.empty_label.hide()
        self.scroll.show()

        count = self.expand_count if self._expanded else 1
        show_items = items[:count]

        for item in show_items:
            iw = ItemWidget(item)
            iw.clicked.connect(self._on_item_clicked)
            iw.edit_requested.connect(self._on_item_edit)
            iw.delete_requested.connect(self._on_item_delete)
            self.list_layout.insertWidget(self.list_layout.count() - 1, iw)

        self._adjust_height()

    def apply_settings(self, settings: dict) -> None:
        """设置变更后重新读取配置并刷新界面"""
        self.settings = settings
        self._load_settings()
        self.refresh()
        # 正贴边时，细条宽度改了要立刻生效
        if self._snapped and self._snap_side is not None:
            self._snap(self._snap_side)

    @property
    def is_snapped(self) -> bool:
        """当前是不是缩在屏幕边缘的细条形态"""
        return self._snapped

    def _adjust_height(self) -> None:
        """根据当前状态调整窗口高度"""
        if self._snapped:
            return

        TITLE_H = 32
        PADDING = 16

        n = min(self.expand_count, self.storage.count()) if self._expanded else 1
        if n <= 0:
            content_h = 60
        else:
            content_h = n * (ITEM_HEIGHT + ITEM_MARGIN)

        target_h = TITLE_H + PADDING + content_h

        if self._expanded:
            target_h = min(EXPANDED_MAX_HEIGHT, target_h)

        self.resize(self.width(), max(MIN_HEIGHT, target_h))

    # ---------------------------------------------------------
    # 展开 / 收起
    # ---------------------------------------------------------
    def _do_expand(self) -> None:
        if self._expanded:
            return
        self._expanded = True
        self.refresh()

    def _do_collapse(self) -> None:
        # 弹出菜单/模态对话框存在时，延后收起
        if QApplication.activePopupWidget() is not None:
            self._collapse_timer.start(self.collapse_delay_ms)
            return
        if QApplication.activeModalWidget() is not None:
            self._collapse_timer.start(self.collapse_delay_ms)
            return

        if not self._expanded and self._snapped:
            return

        self._expanded = False
        self.refresh()

        if self._snap_side is not None:
            self._snap(self._snap_side)

    # ---------------------------------------------------------
    # 鼠标进入 / 离开
    # ---------------------------------------------------------
    def enterEvent(self, e):
        self._collapse_timer.stop()

        if self._snapped:
            # 贴边状态：一步到位，探出 + 立即展开
            self._peek()
            self._expand_timer.stop()
            self._do_expand()
        else:
            # 普通悬停：延迟展开，避免误触
            self._expand_timer.start(self.expand_delay_ms)

        super().enterEvent(e)

    def leaveEvent(self, e):
        self._expand_timer.stop()

        # 弹出菜单或模态对话框打开时，不自动收起
        if QApplication.activePopupWidget() is not None:
            return
        if QApplication.activeModalWidget() is not None:
            return

        self._collapse_timer.start(self.collapse_delay_ms)
        super().leaveEvent(e)

    # ---------------------------------------------------------
    # 点击 / 修改 / 删除
    # ---------------------------------------------------------
    def _on_item_clicked(self, item_id: int) -> None:
        item = self.storage.get(item_id)
        if not item:
            return
        if not write_item_to_clipboard(item, self.storage):
            return
        self.storage.touch(item_id)
        self.refresh()

    def _on_item_edit(self, item_id: int, current_text: str) -> None:
        full_text = self.storage.read_text(item_id)
        if full_text is None:
            full_text = current_text

        new_text = EditTextDialog.get_text(full_text, self)
        if new_text is None:            # 取消
            return

        if not self.storage.update_text(item_id, new_text):
            QMessageBox.warning(self, "失败", "该记录不是文本，无法修改。")
            return
        self.refresh()

    def _on_item_delete(self, item_id: int) -> None:
        self.storage.delete(item_id)
        self.refresh()

    # ---------------------------------------------------------
    # 贴边（360 悬浮球模式）
    # ---------------------------------------------------------
    def snap_screen(self) -> QScreen | None:
        """
        贴边按“窗口所在的显示器”算，而不是主屏：

        多屏时窗口被拖到副屏，细条就应该贴在副屏的那条边上。

        唯一例外是“拖拽结束后刚贴边”的那一刻——此时鼠标在窗口外面
        （贴边判定看的是鼠标位置），所以用 _drag_snap_screen 覆盖。
        """
        return self._drag_snap_screen or screen_for_widget(self)

    @staticmethod
    def _snap_x(side: str, area: QRect, width: int) -> int:
        """
        指定屏幕 + 指定宽度时，贴左边/右边的 x 坐标。
        贴边、探出、拖拽吸附都用这一处，免得三份代码慢慢算歪。
        """
        if side == "left":
            return area.left()
        return area.right() - width + 1

    def _snap(self, side: str) -> None:
        """
        缩回细条形态（持久状态）。

        细条对应的就是“收起”，所以这里顺手把列表收成单条。
        否则从展开状态直接拖到边缘时，细条会一直保持展开后的高度，
        得先悬停展开一次、再移开，之后才会变成单条高度。
        """
        self._snap_side = side

        # 先退出贴边态再算高度：_adjust_height() 在贴边态下是不动高度的
        self._snapped = False
        if self._expanded:
            self._expanded = False
            self.refresh()                  # 收起成单条，内部会调 _adjust_height()
        else:
            self._adjust_height()
        self._snapped = True

        screen = self.snap_screen()
        area = screen.availableGeometry() if screen is not None else None
        strip_w = self.edge_strip_width
        h = self.height()
        y = self.y()

        self._set_strip_width(True)         # 放开最小宽度，否则会被卡在 MIN_WIDTH
        if area is None:                    # 拿不到屏幕信息：保持原位，只收窄
            self.setGeometry(self.x(), y, strip_w, h)
            return
        self.setGeometry(self._snap_x(side, area, strip_w), y, strip_w, h)

    def _set_strip_width(self, striped: bool) -> None:
        """
        细条形态下把最小宽度放到细条宽度（设置项 edge_strip_width），
        否则 setMinimumSize(MIN_WIDTH) 会把细条撑成 220px。
        """
        self.setMinimumWidth(self.edge_strip_width if striped else MIN_WIDTH)

    def _peek(self) -> None:
        """从细条状态探出宽度（高度由后续 _do_expand 统一决定）"""
        if not self._snapped or self._snap_side is None:
            return
        self._snapped = False

        screen = self.snap_screen()
        area = screen.availableGeometry() if screen is not None else None
        w = max(MIN_WIDTH, self._normal_width)
        h = self.height()
        y = self.y()

        if area is None:                    # 拿不到屏幕信息：只改宽度，位置不动
            self.setGeometry(self.x(), y, w, h)
            self._set_strip_width(False)
            return

        # 细条贴在副屏上时，探出也要按副屏的边算，否则窗口会跳到主屏去
        x = self._snap_x(self._snap_side, area, w)

        # 先只改宽度和 x，高度保持，随后 _do_expand 会一次性修正高度
        self.setGeometry(x, y, w, h)
        # 恢复最小宽度放在 setGeometry 之后，免得先把窗口撑开再改宽
        self._set_strip_width(False)

    def _resolve_snap_after_drag(self) -> None:
        """
        拖拽/拉伸结束后：靠近某块屏的边缘就贴到那块屏上。

        判定规则：
        - 鼠标所在的那块屏优先（多屏时鼠标跟手，用户意图最明确）
        - 用那块屏看窗口有没有贴到它的左右边
        - 真贴住了 -> 先把窗口按该屏边缘对齐，再收起成细条，
          否则细条会用一个“还在两块屏之间”的 y，看着像跳了一下
        - 都没贴住 -> 清除贴边状态
        """
        screen = self._drag_snap_screen or screen_for_widget(self)
        if screen is None:
            self._drag_snap_screen = None
            self._snap_side = None
            self._snapped = False
            self._set_strip_width(False)
            return

        area = screen.availableGeometry()
        g = self.frameGeometry()
        side = snap_region(g, area)

        if not side:
            self._drag_snap_screen = None
            self._snap_side = None
            self._snapped = False
            self._set_strip_width(False)    # 拖离边缘：恢复正常最小宽度
            return

        # 先把窗口吸到该屏的左右边上，再收成细条
        # （_snap 是靠 _drag_snap_screen 认屏的，所以它必须留到 _snap 之后再清）
        self.move(self._snap_x(side, area, g.width()), g.top())
        self._snap(side)
        self._drag_snap_screen = None

    def _screen_under_pointer(self) -> QScreen | None:
        """鼠标所在的显示器（拖拽/拉伸过程中记录用）"""
        return screen_for_rect(QRect(QCursor.pos(), QCursor.pos()))

    def resizeEvent(self, e):
        # 记住“正常状态”下的宽度，细条探出时恢复到这个宽度
        # （不能写死 DEFAULT_WIDTH：用户拉宽过、或配置里指定过别的宽度）
        if not self._snapped and self.width() > self.edge_strip_width:
            self._normal_width = self.width()
        super().resizeEvent(e)

    # ---------------------------------------------------------
    # 拖拽 / 拉伸
    # ---------------------------------------------------------
    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            # 卡片上的右键会被 ItemWidget 自己吃掉（单条菜单），
            # 只有空白处 / 标题栏的右键才会冒泡到这里
            self.context_menu_requested.emit(e.globalPosition().toPoint())
            e.accept()
            return
        if e.button() != Qt.LeftButton:
            return
        pos = e.position().toPoint()
        edge = self._detect_resize_edge(pos)
        # 新的一轮拖拽开始：清掉上一轮的屏信息，免得残留到这次判定里
        self._drag_snap_screen = None
        if edge:
            self._resizing = True
            self._resize_edge = edge
            self._resize_start_geo = self.geometry()
            self._resize_start_pos = e.globalPosition().toPoint()
        else:
            self._drag_offset = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        # 拖拽过程中鼠标可能移到另一块屏上，贴边判定以松手时的位置为准
        if e.buttons() & Qt.LeftButton:
            self._drag_snap_screen = self._screen_under_pointer()
        if self._resizing and self._resize_start_geo and self._resize_start_pos:
            self._do_resize(e.globalPosition().toPoint())
            return
        if self._drag_offset and (e.buttons() & Qt.LeftButton):
            self.move(e.globalPosition().toPoint() - self._drag_offset)
        else:
            self._update_cursor(e.position().toPoint())
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._resizing:
            self._resizing = False
            self._resize_start_geo = None
            self._resize_start_pos = None
            self._resolve_snap_after_drag()
        elif self._drag_offset is not None:
            self._resolve_snap_after_drag()
        self._drag_offset = None
        self.unsetCursor()
        super().mouseReleaseEvent(e)

    def _detect_resize_edge(self, pos: QPoint, margin: int = 6) -> str:
        r = self.rect()
        edge = ""
        if pos.x() <= margin:
            edge += "l"
        elif pos.x() >= r.width() - margin:
            edge += "r"
        if pos.y() <= margin:
            edge += "t"
        elif pos.y() >= r.height() - margin:
            edge += "b"
        return edge

    def _update_cursor(self, pos: QPoint) -> None:
        edge = self._detect_resize_edge(pos)
        cursors = {
            "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor,
            "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor,
            "lt": Qt.SizeFDiagCursor, "rb": Qt.SizeFDiagCursor,
            "rt": Qt.SizeBDiagCursor, "lb": Qt.SizeBDiagCursor,
        }
        self.setCursor(cursors.get(edge, Qt.ArrowCursor))

    def _do_resize(self, global_pos: QPoint) -> None:
        delta = global_pos - self._resize_start_pos
        geo = QRect(self._resize_start_geo)
        if "l" in self._resize_edge:
            geo.setLeft(geo.left() + delta.x())
        if "r" in self._resize_edge:
            geo.setRight(geo.right() + delta.x())
        if "t" in self._resize_edge:
            geo.setTop(geo.top() + delta.y())
        if "b" in self._resize_edge:
            geo.setBottom(geo.bottom() + delta.y())
        if geo.width() < MIN_WIDTH:
            geo.setWidth(MIN_WIDTH)
        if geo.height() < MIN_HEIGHT:
            geo.setHeight(MIN_HEIGHT)
        self.setGeometry(geo)

    # ---------------------------------------------------------
    # 最小化 -> 收进托盘
    # ---------------------------------------------------------
    def minimize_to_tray(self) -> None:
        """「−」按钮：收进托盘（不是真最小化，原因见 changeEvent）"""
        self.hide()
        self.minimized_to_tray.emit()

    def _hide_after_minimize(self) -> None:
        """兜底路径（系统把窗口最小化了）延迟到这里再收"""
        if self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        self.hide()
        self.minimized_to_tray.emit()

    def changeEvent(self, e):
        """
        兜底：任何把本窗口最小化的路径（Win+D、系统菜单、第三方工具）都改成
        「收进托盘」。

        为什么不能真最小化（实测复现）：
        本窗口是 Qt::Tool（无任务栏按钮）。这类窗口一旦进入最小化状态，
        Windows 会把它缩成一条 237x39 的小窗**塞到屏幕左下角**，而且
        IsWindowVisible 仍然是 True；此时托盘再唤出（show + raise + activate）
        也恢复不了（isMinimized 仍是 True）——用户看到的就是「缩小后屏幕
        左下角残留一个窗口，还唤不回来」。

        两个坑：
        1. 必须先解除最小化再隐藏，否则下次 show() 还是缩在左下角；
        2. 隐藏要**延后一轮事件循环**：Qt 的 showMinimized() 返回之后还会把
           窗口显示出来，在 changeEvent 里同步 hide() 会被它顶掉
           （实测同步 hide() 之后 isVisible 仍然是 True）。
        """
        if e.type() == QEvent.Type.WindowStateChange and self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
            QTimer.singleShot(0, self._hide_after_minimize)
            return
        super().changeEvent(e)

    # ---------------------------------------------------------
    # 关闭时隐藏而非退出
    # ---------------------------------------------------------
    def closeEvent(self, e):
        e.ignore()
        self.hide()

    def copy_next_item(self) -> bool:
        """
        复制“紧跟着最新记录的前一条”（列表第 2 条），并置顶。
        位置调整逻辑与点击记录一致：写剪贴板 → touch → refresh。
        无记录或只有 1 条时返回 False。
        """
        items = self.storage.list_all()
        if len(items) < 2:
            return False
        target = items[1]
        if not write_item_to_clipboard(target, self.storage):
            return False
        self.storage.touch(target.id)
        self.refresh()
        return True