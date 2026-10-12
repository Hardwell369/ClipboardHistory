# core/hotkey.py
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from pynput import keyboard


# 修饰键归一化映射
_MOD_MAP = {
    "ctrl_l": "ctrl", "ctrl_r": "ctrl",
    "shift_l": "shift", "shift_r": "shift",
    "alt_l": "alt", "alt_r": "alt", "alt_gr": "alt",
    "cmd": "cmd", "cmd_l": "cmd", "cmd_r": "cmd",
}
_MOD_NAMES = {"ctrl", "shift", "alt", "cmd"}

# 字母/数字键的虚拟键码和 ASCII 大写码一致，用它做与键盘布局无关的稳定名字。
# 必须这样做：按住 Ctrl 时 pynput 报的 char 会变成控制字符
# （Ctrl+V -> '\x16'、Ctrl+C -> '\x03'），拿 char 当键名就永远匹配不上。
_VK_ALNUM = {
    vk: chr(vk).lower()
    for vk in list(range(0x41, 0x5B)) + list(range(0x30, 0x3A))
}


class _HotkeySpec:
    """单个热键的解析结果"""
    __slots__ = ("name", "mods", "key")

    def __init__(self, name: str, mods: set[str], key: str):
        self.name = name
        self.mods = mods
        self.key = key


class HotkeyManager(QObject):
    """
    全局快捷键管理，支持注册多个热键。
    触发时发出 triggered(name)，name 是注册时给的标识。
    """

    triggered = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._listener: keyboard.Listener | None = None
        self._specs: dict[str, _HotkeySpec] = {}   # name -> spec
        self._pressed: set[str] = set()

    # ---------------------------------------------------------
    # 对外接口
    # ---------------------------------------------------------
    def register(self, name: str, sequence: str) -> bool:
        """注册一个热键。sequence 为空则注销该 name。"""
        self.unregister(name)
        if not sequence:
            return False
        spec = self._parse(name, sequence)
        if spec is None:
            return False
        self._specs[name] = spec
        self._ensure_listener()
        return True

    def unregister(self, name: str) -> None:
        self._specs.pop(name, None)
        if not self._specs:
            self._stop_listener()

    def unregister_all(self) -> None:
        self._specs.clear()
        self._pressed.clear()
        self._stop_listener()

    def registered_names(self) -> list[str]:
        return list(self._specs.keys())

    # ---------------------------------------------------------
    # 解析
    # ---------------------------------------------------------
    def _parse(self, name: str, sequence: str) -> _HotkeySpec | None:
        parts = [p.strip().lower() for p in sequence.split("+") if p.strip()]
        mods: set[str] = set()
        key: str | None = None
        for p in parts:
            if p in ("ctrl", "control"):
                mods.add("ctrl")
            elif p == "shift":
                mods.add("shift")
            elif p == "alt":
                mods.add("alt")
            elif p in ("meta", "win", "super", "cmd"):
                mods.add("cmd")
            else:
                key = p
        if key is None:
            return None
        return _HotkeySpec(name, mods, key)

    # ---------------------------------------------------------
    # 监听器
    # ---------------------------------------------------------
    def _ensure_listener(self) -> None:
        if self._listener is not None:
            return
        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.start()

    def _stop_listener(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        self._pressed.clear()

    # ---------------------------------------------------------
    # 键名转换
    # ---------------------------------------------------------
    def _key_name(self, key) -> str | None:
        if isinstance(key, keyboard.Key):
            return _MOD_MAP.get(key.name, key.name)
        if isinstance(key, keyboard.KeyCode):
            # 先认虚拟键码：按住 Ctrl 时 char 是控制字符，只有 VK 靠得住
            if key.vk is not None and key.vk in _VK_ALNUM:
                return _VK_ALNUM[key.vk]
            if key.char and key.char.isprintable():
                return key.char.lower()
            if key.vk is not None:
                return f"vk{key.vk}"
        return None

    # ---------------------------------------------------------
    # 回调
    # ---------------------------------------------------------
    def _on_press(self, key):
        name = self._key_name(key)
        if name is None:
            return
        self._pressed.add(name)

        for spec in self._specs.values():
            if self._match(spec):
                self.triggered.emit(spec.name)
                self._pressed.clear()
                return

    def _on_release(self, key):
        name = self._key_name(key)
        if name:
            self._pressed.discard(name)

    def _match(self, spec: _HotkeySpec) -> bool:
        if spec.key not in self._pressed:
            return False
        if not spec.mods.issubset(self._pressed):
            return False
        # 不允许按下多余的修饰键
        extra = _MOD_NAMES - spec.mods
        if extra & self._pressed:
            return False
        return True