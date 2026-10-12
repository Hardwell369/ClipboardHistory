# core/clipboard_listener.py
from __future__ import annotations

import threading

import win32api
import win32con
import win32gui
import ctypes
from PySide6.QtCore import QObject, Signal


WM_CLIPBOARDUPDATE = 0x031D
WINDOW_CLASS_NAME = "ClipboardHistoryListener"


class ClipboardListener(QObject):
    """
    Windows 剪贴板监听链。
    在独立线程里创建隐藏窗口 + 消息循环，
    收到 WM_CLIPBOARDUPDATE 时通过 Qt 信号通知主线程。
    """

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._hwnd: int = 0
        self._running = False
        self._wnd_class_atom = None

    # ---------------------------------------------------------
    # 启动 / 停止
    # ---------------------------------------------------------
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._hwnd:
            try:
                win32gui.PostMessage(self._hwnd, win32con.WM_CLOSE, 0, 0)
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=2)
        self._thread = None

    # ---------------------------------------------------------
    # 线程内逻辑
    # ---------------------------------------------------------
    def _run(self) -> None:
        # 注册窗口类
        wc = win32gui.WNDCLASS()
        wc.lpfnWndProc = self._wnd_proc
        wc.lpszClassName = WINDOW_CLASS_NAME
        wc.hInstance = win32api.GetModuleHandle(None)

        try:
            self._wnd_class_atom = win32gui.RegisterClass(wc)
        except win32gui.error:
            # 已注册过则忽略
            self._wnd_class_atom = WINDOW_CLASS_NAME

        # 创建隐藏窗口
        self._hwnd = win32gui.CreateWindow(
            self._wnd_class_atom,
            "ClipboardListener",
            0, 0, 0, 0, 0,
            0, 0, wc.hInstance, None,
        )
        ctypes.windll.user32.AddClipboardFormatListener(self._hwnd)

        # 消息循环
        try:
            win32gui.PumpMessages()
        finally:
            try:
                ctypes.windll.user32.RemoveClipboardFormatListener(self._hwnd)
            except Exception:
                pass
            try:
                win32gui.DestroyWindow(self._hwnd)
            except Exception:
                pass

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_CLIPBOARDUPDATE:
            # 通知主线程
            self.changed.emit()
            return 0
        if msg == win32con.WM_CLOSE:
            win32gui.DestroyWindow(hwnd)
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)