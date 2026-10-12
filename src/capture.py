# src/capture.py
"""
剪贴板采集：读剪贴板 -> 过滤 -> 入库。

单独成模块的原因：「复制了但历史里没有」这种问题，只有把**没记录的原因**
落到日志里才查得动。决策集中在这一处，界面入口（main.py）和测试都走它，
测试可以直接喂数据，不必真去动系统剪贴板。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from src.clipboard_io import (
    clipboard_formats,
    get_foreground_window_title,
    read_clipboard,
    save_image_bytes,
    sensitive_reason,
)
from src.models import ClipItem, ClipType

# 未记录的原因码（会写进 app.log）
REASON_OK = "ok"                  # 已记录
REASON_EMPTY = "empty"            # 剪贴板此刻读不到任何可用格式
REASON_SENSITIVE = "sensitive"    # 命中密码规则
REASON_TOO_LARGE = "too-large"    # 超过单条大小上限
REASON_ERROR = "error"            # 读取 / 落盘 / 入库出错

REASON_TEXT = {
    REASON_OK: "已记录",
    REASON_EMPTY: "剪贴板此刻没有任何可用格式（被别的程序占用，或对方正在写入）",
    REASON_SENSITIVE: "命中密码规则，按设置跳过",
    REASON_TOO_LARGE: "超过单条大小上限",
    REASON_ERROR: "读取或落盘出错",
}


@dataclass
class Capture:
    """一次采集的结果。item 为 None = 没记录，reason/detail 说明为什么。"""

    item: ClipItem | None = None
    reason: str = REASON_OK
    detail: str = ""
    formats: list[str] = field(default_factory=list)
    type_name: str = ""
    size: int = 0

    @property
    def saved(self) -> bool:
        return self.item is not None

    def describe(self) -> str:
        """一行日志文本：原因 / 详情 / 大小 / 剪贴板格式"""
        why = REASON_TEXT.get(self.reason, self.reason)
        parts = [f"原因={self.reason}（{why}）"]
        if self.detail:
            parts.append(self.detail)
        if self.size:
            parts.append(f"大小={self.size} 字节")
        if self.type_name:
            parts.append(f"类型={self.type_name}")
        parts.append(f"剪贴板格式={_fmt_formats(self.formats)}")
        return " ".join(parts)


def _fmt_formats(formats: list[str]) -> str:
    return ",".join(formats) if formats else "(无)"


def capture_once(
    storage,
    *,
    exclude_password: bool = True,
    reader: Callable = read_clipboard,
    title_of: Callable = get_foreground_window_title,
    formats_of: Callable = clipboard_formats,
) -> Capture:
    """
    采集一次剪贴板。

    reader / title_of / formats_of 都可以替换（测试用），默认就是真实实现。
    """
    try:
        formats = list(formats_of() or [])
    except Exception:                   # noqa: BLE001 - 拿格式只是给日志用
        formats = []

    try:
        data = reader()
    except Exception as e:              # noqa: BLE001
        return Capture(reason=REASON_ERROR, formats=formats,
                       detail=f"读取剪贴板失败：{type(e).__name__}: {e}")

    if not data:
        return Capture(reason=REASON_EMPTY, formats=formats,
                       detail=f"Qt 看到的格式={_fmt_formats(formats)}")

    ctype, content, preview, size, image_bytes = data
    cap = Capture(formats=formats, type_name=ctype.value, size=size)

    # ---- 密码排除（只对文本）----
    if exclude_password and ctype == ClipType.TEXT:
        try:
            title = title_of() or ""
        except Exception:               # noqa: BLE001
            title = ""
        why = sensitive_reason(content, title)
        if why:
            cap.reason = REASON_SENSITIVE
            cap.detail = f"{why}；前台窗口={title[:60]!r}"
            return cap

    # ---- 单条上限 ----
    if size > storage.max_size:
        cap.reason = REASON_TOO_LARGE
        cap.detail = f"{size} 字节 > 上限 {storage.max_size} 字节"
        return cap

    # ---- 图片落盘 ----
    file_path = None
    if ctype == ClipType.IMAGE and image_bytes:
        try:
            file_path = save_image_bytes(image_bytes, storage.files_dir)
        except Exception as e:          # noqa: BLE001
            cap.reason = REASON_ERROR
            cap.detail = f"图片落盘失败：{type(e).__name__}: {e}"
            return cap
        content = ""

    # ---- 入库 ----
    item = ClipItem(type=ctype, content=content, preview=preview,
                    file_path=file_path, size=size)
    try:
        saved = storage.add(item)
    except Exception as e:              # noqa: BLE001
        cap.reason = REASON_ERROR
        cap.detail = f"入库失败：{type(e).__name__}: {e}"
        return cap
    if saved is None:                   # 存储层也可能因为上限丢弃
        cap.reason = REASON_TOO_LARGE
        cap.detail = f"{size} 字节被存储层拒绝"
        return cap

    cap.item = saved
    return cap


# ------------------------- 自测 -------------------------
if __name__ == "__main__":
    import tempfile

    from src.models import ClipType as _T
    from src.storage import Storage

    tmp = tempfile.mkdtemp(prefix="clip_capture_")
    st = Storage(tmp, lock=False)

    def fake(ctype, text):
        return lambda: (ctype, text, text[:120], len(text.encode("utf-8")), None)

    # 0 = 不拦
    cases = [
        ("README 里的说明文字", "│ clipboard_io.py  # 读写剪贴板 + 密码判断", 0),
        ("手册里的设置说明", "排除密码类内容（推荐）   默认：开启", 0),
        ("真凭证", "password: hunter2", 1),
        ("真凭证(中文键)", "密码：abc123", 1),
        ("代码", "pwd = os.getcwd()", 0),
        ("密码本", "这是我的密码本", 0),
    ]
    for name, text, want_blocked in cases:
        cap = capture_once(st, reader=fake(_T.TEXT, text),
                           title_of=lambda: "Visual Studio Code", formats_of=lambda: ["text/plain"])
        blocked = cap.reason == REASON_SENSITIVE
        print(("OK " if blocked == bool(want_blocked) else "FAIL"),
              f"{name:<20} blocked={blocked} {cap.describe()}")

    cap = capture_once(st, reader=lambda: None, formats_of=lambda: [])
    print(("OK " if cap.reason == REASON_EMPTY else "FAIL"), "空剪贴板 ->", cap.describe())

    print("入库条数 =", st.count())
    st.close()
