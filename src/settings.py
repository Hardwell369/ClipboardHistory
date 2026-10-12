# src/settings.py
from __future__ import annotations

import json
import os
from typing import Any

from src import applog


DEFAULT_SETTINGS: dict[str, Any] = {
    # 存储
    "storage_dir": "",                      # 空 = 用默认 %APPDATA%\ClipboardHistory
    "max_items": 300,                       # 最多记录数
    "max_size_mb": 100,                     # 单条上限（MB）
    "text_disk_threshold_kb": 100,          # 文本落盘阈值（KB）

    # 界面
    "expand_count": 10,                     # 展开显示条数
    "expand_delay_ms": 120,                 # 悬停展开延迟
    "collapse_delay_ms": 500,               # 移出收起延迟
    "edge_strip_width": 5,                  # 贴边细条宽度
    "always_on_top": True,                  # 置顶
    "window_size": 320,                     # 默认正方形边长
    "window_pos": None,                     # [x, y] 或 None

    # 行为
    "exclude_password": True,               # 排除密码
    "auto_start": False,                    # 开机自启
    "global_hotkey": "",                    # 全局快捷键，空 = 不注册
    "next_item_hotkey": "Ctrl+Shift+V",     # 复制上一条（列表第 2 条）
}


class Settings:
    """JSON 配置读写"""

    def __init__(self, path: str):
        self.path = path
        self._data: dict[str, Any] = dict(DEFAULT_SETTINGS)
        self.load()

    # ---------------------------------------------------------
    # 读写
    # ---------------------------------------------------------
    def load(self) -> None:
        if not os.path.exists(self.path):
            self.save()
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 合并默认值，保证新增字段有默认
            merged = dict(DEFAULT_SETTINGS)
            merged.update(data or {})
            self._data = merged
        except (json.JSONDecodeError, OSError):
            # 配置损坏，重置
            self._data = dict(DEFAULT_SETTINGS)
            self.save()

    def save(self) -> bool:
        """
        写盘。返回是否成功，**绝不往外抛异常**。

        调用它的地方包括退出流程（_release_resources -> _save_window_geometry）：
        配置写不进去（目录被安全软件拦、只读、漫游配置异常……）如果往外抛，
        退出 / 重启就会整个断在那里——用户看到的就是「点退出没反应」。
        所以这里失败只记日志，让流程继续走完。

        配置文件本身写不进去时，会退到「跟数据放一起」的 settings.json：
        实测安全软件可能把整个 %APPDATA% 对未签名 exe 拦掉，那时候至少还能
        把窗口位置之类的改动存下来。
        """
        if self._save_to(self.path):
            return True

        fallback = self._fallback_path()
        if fallback and os.path.normcase(fallback) != os.path.normcase(self.path):
            if self._save_to(fallback):
                applog.get("settings").warning("配置已改存到备选位置：%s", fallback)
                return True
        return False

    def _fallback_path(self) -> str:
        """备选配置位置 = 数据目录里的 settings.json（数据目录通常是可写的）"""
        try:
            base = self.resolve_storage_dir()
        except Exception:                       # noqa: BLE001
            return ""
        return os.path.join(base, "settings.json") if base else ""

    def _save_to(self, path: str) -> bool:
        log = applog.get("settings")
        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        except OSError as e:
            log.error("创建配置目录失败：%s（%s）", os.path.dirname(path), e)
            return False

        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
            return True
        except OSError as e:
            log.error("保存配置失败：%s（%s）", path, e)
            try:
                os.remove(tmp)              # 别留下半截 .tmp
            except OSError:
                pass
            return False

    # ---------------------------------------------------------
    # 访问
    # ---------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        if key in self._data:
            return self._data[key]
        if default is not None:
            return default
        return DEFAULT_SETTINGS.get(key)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def update(self, values: dict[str, Any]) -> None:
        self._data.update(values)

    def as_dict(self) -> dict[str, Any]:
        return dict(self._data)

    # ---------------------------------------------------------
    # 便捷属性
    # ---------------------------------------------------------
    @property
    def max_size_bytes(self) -> int:
        return int(self.get("max_size_mb", 100)) * 1024 * 1024

    @property
    def text_disk_threshold_bytes(self) -> int:
        return int(self.get("text_disk_threshold_kb", 100)) * 1024

    def resolve_storage_dir(self) -> str:
        """返回实际存储目录（环境变量 > 配置 > 默认 %APPDATA%\\ClipboardHistory）"""
        env_dir = os.environ.get("CLIPBOARD_DATA_DIR", "").strip()
        if env_dir:
            return env_dir
        d = self.get("storage_dir") or ""
        if d:
            return d
        return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                            "ClipboardHistory")


# ------------------------- 默认配置路径 -------------------------
def default_settings_path() -> str:
    base = os.environ.get("APPDATA", os.path.expanduser("~"))
    return os.path.join(base, "ClipboardHistory", "settings.json")