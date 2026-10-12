# src/data_clean.py
"""
卸载时的数据清理：按类型统计并删除本机保存的**文本、图片、文件**。

为什么要单独一层：
1. 卸载器（uninstaller.exe）是一个独立的小 exe，不依赖 Qt、也不依赖主程序目录
   （它连整个安装目录都要删掉），所以这一层只用标准库；
2. 「删什么」必须是可勾选、可统计、可解释的——用户有权只删文本、留下图片，
   所以先统计（inspect）再删除（delete），并把删了多少、释放了多少写进结果。

存储结构（见 src/storage.py）：
    <数据目录>/
        history.db          items 表：type = text / html / image / files / ...
        files/              图片、大文本、文件快照等 blob

安全原则：
    - **绝不整目录 rmtree**：数据目录有可能被用户设成别的用途的目录，
      只删「程序自己写的东西」（history.db、files/、settings.json、app.log*、
      锁文件），只有目录空了才 rmdir；
    - 被其它进程占用的文件删不掉时记录下来，不抛异常；
    - 数据没删干净（用户选择保留）时，绝不碰 settings.json / app.log。
"""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field

# ---------------------------------------------------------
# 分类定义
# ---------------------------------------------------------
# (键, 界面上的名字, history.db 里对应的 type 值)
CATEGORIES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("text", "文本记录（含网页/富文本 HTML）", ("text", "html")),
    ("image", "图片", ("image",)),
    ("files", "文件（复制进来的文件）", ("files",)),
)
CATEGORY_KEYS: tuple[str, ...] = tuple(key for key, _, _ in CATEGORIES)
CATEGORY_TITLES: dict[str, str] = {key: title for key, title, _ in CATEGORIES}
TYPES_BY_CATEGORY: dict[str, tuple[str, ...]] = {
    key: types for key, _, types in CATEGORIES
}

DB_NAME = "history.db"
FILES_DIR_NAME = "files"
INSTALL_INFO_FILE = "install.json"
APP_DIR_NAME = "ClipboardHistory"

# 「全删干净」时才一并清掉的附属文件（前缀匹配的见 EXTRA_PREFIXES）
EXTRA_NAMES = (
    "settings.json",
    "app.cmd",
    "app.lock",
    ".lock",
)
EXTRA_PREFIXES = ("app.log",)         # app.log / app.log.1 / app.log.2


# ---------------------------------------------------------
# 结果结构
# ---------------------------------------------------------
@dataclass
class CategoryStat:
    key: str
    title: str
    count: int = 0            # 记录条数
    size: int = 0             # items.size 之和（记录声明的字节数）
    disk: int = 0             # 对应 blob 实际占用的字节数

    def describe(self) -> str:
        return f"{self.count} 条 / {format_size(self.disk or self.size)}"


@dataclass
class DirStat:
    """一个候选目录的统计结果"""

    path: str
    exists: bool = False
    has_db: bool = False
    db_bytes: int = 0
    files_count: int = 0
    files_bytes: int = 0
    categories: dict[str, CategoryStat] = field(default_factory=dict)
    unknown: int = 0                                  # 不认识的类型（保留，不删）
    extras: list[tuple[str, int]] = field(default_factory=list)
    error: str = ""

    @property
    def total_count(self) -> int:
        return sum(stat.count for stat in self.categories.values())

    @property
    def total_disk(self) -> int:
        return sum(stat.disk for stat in self.categories.values())

    @property
    def is_empty(self) -> bool:
        return not self.has_db and not self.files_count and not self.extras


@dataclass
class DeleteDirResult:
    path: str
    deleted: dict[str, int] = field(default_factory=dict)   # 分类 -> 删除条数
    blobs: int = 0
    freed: int = 0
    removed_extras: list[str] = field(default_factory=list)
    removed_dir: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def deleted_total(self) -> int:
        return sum(self.deleted.values())


# ---------------------------------------------------------
# 小工具
# ---------------------------------------------------------
def format_size(num: float) -> str:
    """人类可读的字节数（界面、报告里都用它）"""
    try:
        value = float(num)
    except (TypeError, ValueError):
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _size_of(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _file_bytes(path: str) -> int:
    """删掉一个文件并返回它占用的字节数（删不掉返回 0）"""
    try:
        size = os.path.getsize(path)
    except OSError:
        return 0
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    try:
        os.remove(path)
    except OSError:
        return 0
    return size


def _try_rmdir(path: str) -> bool:
    """只在目录为空时删掉它（非空目录绝不动）"""
    try:
        os.rmdir(path)
        return True
    except OSError:
        return False


def default_settings_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_DIR_NAME)


def default_data_dir() -> str:
    """与程序默认值一致：%APPDATA%\\ClipboardHistory"""
    return default_settings_dir()


# ---------------------------------------------------------
# 找出所有可能有数据的目录
# ---------------------------------------------------------
def settings_storage_dir(settings_path: str = "") -> str:
    """
    读 settings.json 里的存储路径（**只读，不会创建配置**）。

    注意不能用 src.settings.Settings：它在文件不存在时会顺手写一份出来，
    卸载流程里绝对不能在用户机器上凭空造出配置目录。
    """
    path = settings_path or os.path.join(default_settings_dir(), "settings.json")
    value = _read_json(path).get("storage_dir") or ""
    return str(value).strip()


def install_data_dir(install_dir: str) -> str:
    """读安装目录里 install.json 记录的数据目录（安装器写的）"""
    if not install_dir:
        return ""
    value = _read_json(os.path.join(install_dir, INSTALL_INFO_FILE)).get("data_dir")
    return str(value or "").strip()


def candidate_dirs(install_dir: str = "",
                   extra_dirs: list[str] | None = None) -> list[str]:
    """
    按优先级列出所有可能有剪贴板数据的目录。

    顺序：命令行指定 > 环境变量 > settings.json 的存储路径 >
    install.json 的数据目录 > 默认 %APPDATA%\\ClipboardHistory。
    去重（大小写不敏感）但保持顺序。
    """
    out: list[str] = []
    for item in (extra_dirs or []):
        for part in str(item).split(","):
            if part.strip():
                out.append(part.strip())
    env_dir = (os.environ.get("CLIPBOARD_DATA_DIR") or "").strip()
    if env_dir:
        out.append(env_dir)
    configured = settings_storage_dir()
    if configured:
        out.append(configured)
    recorded = install_data_dir(install_dir)
    if recorded:
        out.append(recorded)
    out.append(default_data_dir())

    seen: set[str] = set()
    uniq: list[str] = []
    for path in out:
        key = _norm(path)
        if key not in seen:
            seen.add(key)
            uniq.append(os.path.abspath(path))
    return uniq


# ---------------------------------------------------------
# 统计
# ---------------------------------------------------------
def inspect_dir(path: str) -> DirStat:
    """统计一个目录里各类数据的条数与体积（只读，不改任何东西）"""
    stat = DirStat(path=path)
    stat.exists = os.path.isdir(path)
    if not stat.exists:
        return stat

    files_dir = os.path.join(path, FILES_DIR_NAME)
    if os.path.isdir(files_dir):
        try:
            for name in os.listdir(files_dir):
                full = os.path.join(files_dir, name)
                if os.path.isfile(full):
                    stat.files_count += 1
                    stat.files_bytes += _size_of(full)
        except OSError as e:
            stat.error = f"读取 files 目录失败：{e}"

    for name in os.listdir(path) if os.path.isdir(path) else []:
        full = os.path.join(path, name)
        if not os.path.isfile(full):
            continue
        lowered = name.lower()
        if any(lowered.startswith(p) for p in EXTRA_PREFIXES) or name in EXTRA_NAMES:
            stat.extras.append((name, _size_of(full)))

    db_path = os.path.join(path, DB_NAME)
    if not os.path.isfile(db_path):
        return stat
    stat.has_db = True
    stat.db_bytes = _size_of(db_path)

    for key, title, _ in CATEGORIES:
        stat.categories[key] = CategoryStat(key=key, title=title)

    try:
        # 只读打开：程序没关掉时也能统计（拿不到就退化成「文件系统估算」）
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3.0)
    except sqlite3.Error as e:
        stat.error = f"打不开 {DB_NAME}：{e}"
        return stat
    try:
        rows = con.execute(
            "SELECT type, COUNT(*), IFNULL(SUM(size), 0) FROM items GROUP BY type"
        ).fetchall()
        known: dict[str, str] = {}
        for key, _, types in CATEGORIES:
            for value in types:
                known[value] = key
        for type_value, count, size in rows:
            key = known.get(str(type_value))
            if key is None:
                stat.unknown += int(count or 0)
                continue
            cat = stat.categories[key]
            cat.count += int(count or 0)
            cat.size += int(size or 0)
        # 每条记录对应的 blob 实际占用
        for key, _, types in CATEGORIES:
            placeholders = ",".join("?" * len(types))
            cur = con.execute(
                f"SELECT file_path FROM items WHERE type IN ({placeholders})", types)
            for (file_path,) in cur.fetchall():
                if file_path and os.path.isfile(file_path):
                    stat.categories[key].disk += _size_of(file_path)
    except sqlite3.Error as e:
        stat.error = f"读 {DB_NAME} 失败：{e}"
    finally:
        con.close()
    return stat


def inspect(paths: list[str]) -> list[DirStat]:
    """统计多个目录（顺序与传入一致）"""
    return [inspect_dir(path) for path in paths]


def has_anything(stats: list[DirStat]) -> bool:
    """有没有值得问用户的数据（没有任何数据就不用弹选择了）"""
    return any(not stat.is_empty for stat in stats)


# ---------------------------------------------------------
# 删除
# ---------------------------------------------------------
def _remaining_items(db_path: str) -> int:
    """
    库里还剩多少条记录。

    读不出来（库损坏 / 被占用）时返回 -1，调用方按「还有东西」处理——
    宁可多留一份配置，也不能把用户没让删的东西删了。
    """
    if not os.path.isfile(db_path):
        return 0
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3.0)
    except sqlite3.Error:
        return -1
    try:
        return int(con.execute("SELECT COUNT(*) FROM items").fetchone()[0])
    except sqlite3.Error:
        return -1
    finally:
        con.close()


def _purge_extras(path: str, result: DeleteDirResult) -> None:
    """把「程序自己写的东西」清干净（只在数据全删掉时调用）"""
    files_dir = os.path.join(path, FILES_DIR_NAME)
    if os.path.isdir(files_dir):
        try:
            for name in os.listdir(files_dir):
                full = os.path.join(files_dir, name)
                if os.path.isfile(full):
                    freed = _file_bytes(full)
                    if freed:
                        result.freed += freed
                        result.blobs += 1
            _try_rmdir(files_dir)
        except OSError:
            pass

    try:
        names = os.listdir(path)
    except OSError:
        return
    for name in names:
        full = os.path.join(path, name)
        if not os.path.isfile(full):
            continue
        lowered = name.lower()
        is_extra = (name in EXTRA_NAMES
                    or any(lowered.startswith(p) for p in EXTRA_PREFIXES)
                    or lowered.startswith(DB_NAME))          # history.db / -wal / -shm
        if not is_extra:
            continue
        freed = _file_bytes(full)
        if freed or not os.path.exists(full):
            result.freed += freed
            result.removed_extras.append(name)

    result.removed_dir = _try_rmdir(path)


def _delete_in_dir(path: str, categories: list[str],
                   progress=None) -> DeleteDirResult:
    result = DeleteDirResult(path=path)
    if not os.path.isdir(path):
        return result

    db_path = os.path.join(path, DB_NAME)
    if not os.path.isfile(db_path):
        # 没有库（例如只有配置的 %APPDATA% 目录）：
        # 只有「用户要求全删」时才清掉配置与日志
        if set(categories) >= set(CATEGORY_KEYS):
            if progress:
                progress(f"正在清理配置目录 {path} …")
            _purge_extras(path, result)
        return result

    try:
        con = sqlite3.connect(db_path, timeout=5.0)
    except sqlite3.Error as e:
        result.errors.append(f"打不开 {db_path}：{e}")
        return result

    try:
        for key in categories:
            types = TYPES_BY_CATEGORY[key]
            placeholders = ",".join("?" * len(types))
            title = CATEGORY_TITLES.get(key, key)
            if progress:
                progress(f"正在删除{title} …")
            rows = con.execute(
                f"SELECT file_path FROM items WHERE type IN ({placeholders})",
                types).fetchall()
            for (file_path,) in rows:
                if file_path and os.path.isfile(file_path):
                    freed = _file_bytes(file_path)
                    if freed:
                        result.freed += freed
                        result.blobs += 1
            cur = con.execute(
                f"DELETE FROM items WHERE type IN ({placeholders})", types)
            con.commit()
            result.deleted[key] = int(cur.rowcount or 0)

        # 孤儿 blob：files/ 里没有被任何剩余记录引用的文件（去重、淘汰留下的）
        if progress:
            progress("正在清理残留文件 …")
        refs: set[str] = set()
        for (file_path,) in con.execute(
                "SELECT file_path FROM items WHERE file_path IS NOT NULL").fetchall():
            if file_path:
                refs.add(_norm(file_path))
        files_dir = os.path.join(path, FILES_DIR_NAME)
        if os.path.isdir(files_dir):
            for name in os.listdir(files_dir):
                full = os.path.join(files_dir, name)
                if not os.path.isfile(full):
                    continue
                if _norm(full) in refs:
                    continue
                freed = _file_bytes(full)
                if freed:
                    result.freed += freed
                    result.blobs += 1

        # 收缩数据库（删掉大量记录后能明显变小）
        try:
            con.execute("VACUUM")
        except sqlite3.Error:
            pass
    except sqlite3.Error as e:
        result.errors.append(f"清理 {db_path} 失败：{e}")
    finally:
        con.close()

    # 全删干净了才动配置/日志，并把空目录收掉
    if set(categories) >= set(CATEGORY_KEYS):
        if _remaining_items(db_path) == 0:
            if progress:
                progress("正在清理配置与日志 …")
            _purge_extras(path, result)
        elif _remaining_items(db_path) > 0:
            result.errors.append(
                f"库里还有不认识的记录类型，已保留 {DB_NAME} 与配置文件")
    return result


def delete_data(paths: list[str], categories: list[str],
                progress=None) -> list[DeleteDirResult]:
    """
    按分类删除多个目录里的数据。

    categories 为空 = 什么都不删（用户选择「保留数据」），直接返回空结果。
    每个目录各自 try/except，失败只记在结果里，不影响其它目录。
    """
    wanted = [key for key in CATEGORY_KEYS if key in set(categories or [])]
    if not wanted:
        return []
    out: list[DeleteDirResult] = []
    for path in paths:
        try:
            out.append(_delete_in_dir(path, wanted, progress=progress))
        except Exception as e:                      # noqa: BLE001 - 卸载不能被它挡住
            item = DeleteDirResult(path=path)
            item.errors.append(f"{type(e).__name__}: {e}")
            out.append(item)
    return out


def remove_session_files(paths: list[str]) -> list[str]:
    """
    删掉锁/命令文件（app.lock / app.cmd / .lock）。

    这些是「这次会话的痕迹」，跟数据留不留无关；删不掉（文件被占用）就算了。
    """
    removed: list[str] = []
    for path in paths:
        if not os.path.isdir(path):
            continue
        for name in ("app.lock", "app.cmd", ".lock"):
            full = os.path.join(path, name)
            if os.path.isfile(full) and _file_bytes(full):
                removed.append(full)
    return removed


def summarize(results: list[DeleteDirResult]) -> str:
    """把删除结果写成几行文字（界面 / 日志 / 报告都用它）"""
    if not results:
        return "已按选择保留本机数据，未删除任何记录。"
    lines: list[str] = []
    for item in results:
        if item.deleted_total == 0 and not item.removed_extras:
            if item.errors:
                lines.append(f"{item.path}：无数据可删")
            continue
        parts = [f"{CATEGORY_TITLES.get(k, k)} {v} 条"
                 for k, v in item.deleted.items() if v]
        parts.append(f"清理文件 {item.blobs} 个（{format_size(item.freed)}）")
        if item.removed_extras:
            parts.append("已删除配置/日志")
        lines.append(f"{item.path}：{'，'.join(parts)}")
    for item in results:
        for err in item.errors:
            lines.append(f"问题：{err}")
    return "\n".join(lines) if lines else "没有需要删除的数据。"


# ---------------------------------------------------------
# 自测：python -m src.data_clean
# ---------------------------------------------------------
if __name__ == "__main__":
    import tempfile

    root = tempfile.mkdtemp(prefix="clipclean_")
    data = os.path.join(root, "data")
    os.makedirs(os.path.join(data, "files"), exist_ok=True)

    blob_img = os.path.join(data, "files", "img.png")
    blob_txt = os.path.join(data, "files", "big.txt")
    with open(blob_img, "wb") as f:
        f.write(b"x" * 2048)
    with open(blob_txt, "w", encoding="utf-8") as f:
        f.write("y" * 500)
    with open(os.path.join(data, "settings.json"), "w", encoding="utf-8") as f:
        f.write("{}")

    con = sqlite3.connect(os.path.join(data, "history.db"))
    con.execute("CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "type TEXT, content TEXT, preview TEXT, file_path TEXT, "
                "size INTEGER DEFAULT 0, created_at REAL)")
    con.execute("INSERT INTO items(type, content, preview, file_path, size, created_at)"
                " VALUES ('text', 'hello', 'hello', NULL, 5, 1)")
    con.execute("INSERT INTO items(type, content, preview, file_path, size, created_at)"
                " VALUES ('html', '<b>x</b>', 'x', NULL, 8, 2)")
    con.execute("INSERT INTO items(type, content, preview, file_path, size, created_at)"
                " VALUES ('text', '', 'big', ?, 500, 3)", (blob_txt,))
    con.execute("INSERT INTO items(type, content, preview, file_path, size, created_at)"
                " VALUES ('image', '', 'img', ?, 2048, 4)", (blob_img,))
    con.commit()
    con.close()

    stats = inspect([data])
    for stat in stats:
        print("目录:", stat.path, "存在:", stat.exists, "库:", stat.db_bytes, "B")
        for key, cat in stat.categories.items():
            print(f"   {cat.title}: 条数={cat.count} 声明={cat.size}B 磁盘={cat.disk}B")
        print("   files/:", stat.files_count, "个,", stat.files_bytes, "B")
        print("   附属文件:", stat.extras)
        print("   错误:", stat.error or "无")

    print("\n--- 只删文本与图片（保留文件类，这里没有）---")
    for res in delete_data([data], ["text", "image"]):
        print("  删除:", res.deleted, "blob:", res.blobs, "释放:", res.freed,
              "保留库:", not res.removed_extras, "删目录:", res.removed_dir)
    print("  剩余记录:", _remaining_items(os.path.join(data, "history.db")))
    print("  目录还在:", os.path.isdir(data))

    print("\n--- 全删 ---")
    for res in delete_data([data], list(CATEGORY_KEYS)):
        print("  删除:", res.deleted, "释放:", res.freed, "清附属:", res.removed_extras)
    print("  目录还在:", os.path.isdir(data))
