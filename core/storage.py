# core/storage.py
from __future__ import annotations

import filecmp
import json
import os
import shutil
import sqlite3
import time
import uuid
from typing import Iterable

from core import applog
from core.models import ClipItem, ClipType


# ------------------------- 默认配置 -------------------------
DEFAULT_MAX_ITEMS = 300                 # 最多记录数
DEFAULT_MAX_SIZE = 100 * 1024 * 1024    # 单条上限 100MB
DEFAULT_TEXT_DISK_THRESHOLD = 100 * 1024  # 文本落盘阈值 100KB
PREVIEW_LEN = 120                       # 预览截断长度

LOCK_FILE_NAME = ".lock"                # 存储目录里的跨进程互斥文件
LOCK_TIMEOUT_S = 3.0                    # 等锁最长时间；超时说明确实有别的实例在跑

# 跨进程文件锁：Windows 用 msvcrt，其它平台用 fcntl。
# 这个锁是单实例保护的兜底——接口调用（core/single_instance.py）挡住的是
# “正常双击两次”，这里挡住的是“绕过接口直接起第二份进程”，
# 保证同一个 history.db 永远只有一个写入者。
try:
    import msvcrt
except ImportError:                     # 非 Windows
    msvcrt = None                       # type: ignore[assignment]
    try:
        import fcntl
    except ImportError:
        fcntl = None                    # type: ignore[assignment]


class StorageLockedError(RuntimeError):
    """存储目录已被另一个进程占用"""

    def __init__(self, path: str):
        super().__init__(f"存储目录已被占用：{path}")
        self.path = path


class Storage:
    """
    剪贴板历史存储。

    目录结构：
        base_dir/
            history.db          # SQLite 元数据
            files/              # 图片、大文本、文件快照
    """

    def __init__(
        self,
        base_dir: str,
        max_items: int = DEFAULT_MAX_ITEMS,
        max_size: int = DEFAULT_MAX_SIZE,
        text_disk_threshold: int = DEFAULT_TEXT_DISK_THRESHOLD,
        lock: bool = True,
    ):
        self.base_dir = base_dir
        self.files_dir = os.path.join(base_dir, "files")
        self.db_path = os.path.join(base_dir, "history.db")
        self.lock_path = os.path.join(base_dir, LOCK_FILE_NAME)

        self.max_items = max_items
        self.max_size = max_size
        self.text_disk_threshold = text_disk_threshold

        os.makedirs(self.files_dir, exist_ok=True)

        self._lock_file = None
        if lock:
            # 先上锁再开库：拿不到锁说明已经有实例在用这个库，
            # 那就别去动它（SQLite 并发写会互相覆盖、报 database is locked）。
            self._acquire_lock()

        try:
            self.db = sqlite3.connect(self.db_path)
        except Exception:
            self._release_lock()
            raise
        self.db.row_factory = sqlite3.Row
        self._init_db()

    # ---------------------------------------------------------
    # 跨进程锁
    # ---------------------------------------------------------
    def _acquire_lock(self) -> None:
        """
        独占存储目录。拿不到锁抛 StorageLockedError。

        用 msvcrt.locking(LK_NBLCK) 锁住 .lock 的第 0 字节：
        这种锁由系统在进程结束时自动释放，进程被强杀也不会留下死锁。
        """
        if msvcrt is None and fcntl is None:
            return                      # 两个模块都没有：放弃加锁，不影响使用

        # "a+" 既能写锁区间，又不会像 "w" 那样把别的进程正在用的锁文件截断
        try:
            f = open(self.lock_path, "a+", encoding="utf-8")
        except OSError as e:
            # 连锁文件都建不出来（目录只读 / 被安全软件拦住）：不能把程序直接弄崩，
            # 退化成「没有存储层保护」继续跑，但日志里必须留痕
            # （正常路径上还有单实例锁兜着，见 core/single_instance.py）。
            applog.get("storage").warning(
                "无法打开存储锁文件 %s：%s（本次启动没有存储层互斥保护）",
                self.lock_path, e)
            return
        try:
            if os.path.getsize(self.lock_path) == 0:
                f.write(" ")            # 锁区间要有字节存在
                f.flush()
        except OSError as e:
            applog.get("storage").warning("初始化存储锁文件失败：%s", e)

        deadline = time.monotonic() + LOCK_TIMEOUT_S
        while True:
            try:
                self._lock_once(f)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    f.close()
                    raise StorageLockedError(self.base_dir) from None
                time.sleep(0.1)

        # 写进 pid 方便排查“到底是谁占着”
        try:
            f.seek(0)
            f.truncate()
            f.write(f"{os.getpid()}\n")
            f.flush()
        except OSError:
            pass
        self._lock_file = f

    @staticmethod
    def _lock_once(f) -> None:
        """尝试加一次锁；被占用时抛 OSError"""
        f.seek(0)
        if msvcrt is not None:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)     # type: ignore[union-attr]
        else:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[union-attr]

    def _release_lock(self) -> None:
        f = self._lock_file
        self._lock_file = None
        if f is None:
            return
        try:
            f.seek(0)
            if msvcrt is not None:
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[union-attr]
            else:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)          # type: ignore[union-attr]
        except OSError:
            pass
        finally:
            f.close()

    @property
    def has_lock(self) -> bool:
        return self._lock_file is not None

    # ---------------------------------------------------------
    # 初始化
    # ---------------------------------------------------------
    def _init_db(self) -> None:
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS items (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                type        TEXT    NOT NULL,
                content     TEXT,
                preview     TEXT,
                file_path   TEXT,
                size        INTEGER DEFAULT 0,
                created_at  REAL    NOT NULL
            )
            """
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_created_at ON items(created_at DESC)"
        )
        self.db.commit()

    # ---------------------------------------------------------
    # 增
    # ---------------------------------------------------------
    def add(self, item: ClipItem) -> ClipItem | None:
        """
        新增一条记录。
        - 超过 max_size 直接丢弃，返回 None
        - 同类型 + 同内容去重，旧记录删除
        - 大文本落盘
        - 超出 max_items 淘汰最旧
        返回入库后的 ClipItem（含 id / file_path）。
        """
        if item.size > self.max_size:
            return None

        # ---- 去重 ----
        # 必须放在“大文本落盘”之前：落盘会把正文清空成 ""，
        # 之后再比对就只能拿空串去比，大文本永远算不上重复。
        self._dedupe(item)

        # ---- 大文本落盘 ----
        if item.type == ClipType.TEXT and item.size > self.text_disk_threshold:
            file_path = self._write_text_file(item.content)
            item.file_path = file_path
            item.content = ""  # 正文进磁盘，DB 只留预览

        # ---- 入库 ----
        cur = self.db.execute(
            """
            INSERT INTO items(type, content, preview, file_path, size, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            item.to_row(),
        )
        self.db.commit()
        item.id = cur.lastrowid

        # ---- 淘汰 ----
        self._enforce_limit()
        return item

    # ---------------------------------------------------------
    # 查
    # ---------------------------------------------------------
    def list_all(self) -> list[ClipItem]:
        """按时间倒序返回全部记录"""
        cur = self.db.execute(
            "SELECT id, type, content, preview, file_path, size, created_at "
            "FROM items ORDER BY created_at DESC"
        )
        return [ClipItem.from_row(tuple(r)) for r in cur.fetchall()]

    def get(self, item_id: int) -> ClipItem | None:
        cur = self.db.execute(
            "SELECT id, type, content, preview, file_path, size, created_at "
            "FROM items WHERE id = ?",
            (item_id,),
        )
        row = cur.fetchone()
        return ClipItem.from_row(tuple(row)) if row else None

    def count(self) -> int:
        cur = self.db.execute("SELECT COUNT(*) FROM items")
        return cur.fetchone()[0]

    # ---------------------------------------------------------
    # 改
    # ---------------------------------------------------------
    def update_text(self, item_id: int, new_text: str) -> bool:
        """
        修改一条文本记录（仅 TEXT）。
        - 若原记录已落盘，同步写回文件
        - 否则更新 DB content
        修改后置顶（created_at 刷新）。
        """
        item = self.get(item_id)
        if not item or item.type != ClipType.TEXT:
            return False

        size = len(new_text.encode("utf-8"))
        preview = self._make_preview(new_text)
        now = time.time()

        # 内容变大且超阈值，改为落盘
        if size > self.text_disk_threshold:
            if item.file_path:
                self._write_text_file(new_text, item.file_path)
            else:
                file_path = self._write_text_file(new_text)
                self.db.execute(
                    "UPDATE items SET content='', file_path=?, preview=?, size=?, created_at=? "
                    "WHERE id=?",
                    (file_path, preview, size, now, item_id),
                )
                self.db.commit()
                return True
            self.db.execute(
                "UPDATE items SET content='', preview=?, size=?, created_at=? WHERE id=?",
                (preview, size, now, item_id),
            )
        else:
            # 内容变小，若原来在磁盘则删除文件
            if item.file_path and os.path.exists(item.file_path):
                try:
                    os.remove(item.file_path)
                except OSError:
                    pass
            self.db.execute(
                "UPDATE items SET content=?, file_path=NULL, preview=?, size=?, created_at=? "
                "WHERE id=?",
                (new_text, preview, size, now, item_id),
            )
        self.db.commit()
        return True

    def read_text(self, item_id: int) -> str | None:
        """读取文本正文（自动处理落盘）"""
        item = self.get(item_id)
        if not item or item.type != ClipType.TEXT:
            return None
        if item.file_path and os.path.exists(item.file_path):
            with open(item.file_path, "r", encoding="utf-8") as f:
                return f.read()
        return item.content

    # ---------------------------------------------------------
    # 删
    # ---------------------------------------------------------
    def delete(self, item_id: int) -> bool:
        """删除记录 + 对应磁盘文件"""
        item = self.get(item_id)
        if not item:
            return False
        self._remove_file(item.file_path)
        self.db.execute("DELETE FROM items WHERE id=?", (item_id,))
        self.db.commit()
        return True

    def clear(self) -> None:
        """清空全部记录"""
        self.db.execute("DELETE FROM items")
        self.db.commit()
        if os.path.isdir(self.files_dir):
            shutil.rmtree(self.files_dir, ignore_errors=True)
        os.makedirs(self.files_dir, exist_ok=True)

    # ---------------------------------------------------------
    # 内部工具
    # ---------------------------------------------------------
    def _dedupe(self, item: ClipItem) -> None:
        """
        同类型 + 同内容去重。

        - 文本：按正文比对（可能落盘，要读文件），先按 size 预筛，
                免得每复制一次都要把所有历史正文读一遍
        - 图片：按文件内容比对，content/preview 区分不出两张同尺寸的图
        - 文件 / HTML：content 本身就是内容（preview 是从它派生的，不用参与比对）
        """
        if item.type == ClipType.TEXT:
            self._dedupe_text(item)
        elif item.type == ClipType.IMAGE:
            self._dedupe_image(item)
        else:
            row = self.db.execute(
                "SELECT id, file_path FROM items WHERE type=? AND content=?",
                (item.type.value, item.content),
            ).fetchone()
            if row:
                self._drop_row(row["id"], row["file_path"])

    def _dedupe_text(self, item: ClipItem) -> None:
        content = item.content or ""
        size = item.size or len(content.encode("utf-8"))
        rows = self.db.execute(
            "SELECT id, file_path FROM items WHERE type=? AND size=?",
            (ClipType.TEXT.value, size),
        ).fetchall()
        for row in rows:
            if self._stored_text(row["id"], row["file_path"]) == content:
                self._drop_row(row["id"], row["file_path"])
                return

    def _dedupe_image(self, item: ClipItem) -> None:
        """图片只在文件内容完全相同时才算重复"""
        if not item.file_path or not os.path.exists(item.file_path):
            return
        rows = self.db.execute(
            "SELECT id, file_path FROM items WHERE type=? AND size=?",
            (ClipType.IMAGE.value, item.size),
        ).fetchall()
        for row in rows:
            old = row["file_path"]
            if not old or not os.path.exists(old):
                continue
            if not filecmp.cmp(item.file_path, old, shallow=False):
                continue                    # 尺寸一样但内容不同：不是同一条
            self._drop_row(row["id"], old)
            return

    def _stored_text(self, item_id: int, file_path: str | None) -> str:
        """取某条文本记录的正文（落盘的从文件读）"""
        if file_path and os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    return f.read()
            except OSError:
                return ""
        row = self.db.execute(
            "SELECT content FROM items WHERE id=?", (item_id,)
        ).fetchone()
        return (row["content"] or "") if row else ""

    def _drop_row(self, item_id: int, file_path: str | None) -> None:
        """删掉被判定为重复的旧记录（连同它的文件）"""
        self._remove_file(file_path)
        self.db.execute("DELETE FROM items WHERE id=?", (item_id,))
        self.db.commit()

    def _enforce_limit(self) -> None:
        """超出 max_items 淘汰最旧"""
        cur = self.db.execute(
            "SELECT id FROM items ORDER BY created_at DESC LIMIT -1 OFFSET ?",
            (self.max_items,),
        )
        for row in cur.fetchall():
            self.delete(row["id"])

    def _write_text_file(self, text: str, path: str | None = None) -> str:
        """把文本写入 files_dir，返回文件路径"""
        if path is None:
            path = os.path.join(self.files_dir, f"{uuid.uuid4().hex}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def _remove_file(self, path: str | None) -> None:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass

    @staticmethod
    def _make_preview(text: str) -> str:
        text = text.replace("\r\n", "\n").replace("\n", " ")
        return text[:PREVIEW_LEN]

    def touch(self, item_id: int) -> bool:
        """置顶：只刷新 created_at"""
        cur = self.db.execute(
            "UPDATE items SET created_at=? WHERE id=?",
            (time.time(), item_id),
        )
        self.db.commit()
        return cur.rowcount > 0

    # ---------------------------------------------------------
    # 资源释放
    # ---------------------------------------------------------
    def close(self) -> None:
        try:
            self.db.close()
        except Exception:
            pass
        self._release_lock()


# ------------------------- 自测 -------------------------
if __name__ == "__main__":
    import tempfile

    tmp = tempfile.mkdtemp(prefix="clip_test_")
    st = Storage(tmp, max_items=5, text_disk_threshold=20)

    # 普通文本
    st.add(ClipItem(type=ClipType.TEXT, content="hello", preview="hello", size=5))
    # 大文本（落盘）
    big = "x" * 100
    st.add(ClipItem(type=ClipType.TEXT, content=big, preview="x" * 20, size=100))
    # 文件列表
    st.add(ClipItem(type=ClipType.FILES, content=json.dumps(["C:/a.txt"]),
                    preview="a.txt", size=1))
    # 图片占位
    st.add(ClipItem(type=ClipType.IMAGE, content="", preview="img",
                    file_path=os.path.join(st.files_dir, "fake.png"), size=10))

    print("count =", st.count())
    for it in st.list_all():
        print(it.id, it.type, repr(it.preview), "disk=", bool(it.file_path))

    # 修改大文本
    st.update_text(2, "y" * 50)
    print("after edit:", st.read_text(2))

    # 删除
    st.delete(1)
    print("after delete count =", st.count())

    st.close()