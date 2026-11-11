"""音频目录扫描：把磁盘上的文件同步进 files 表。"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path

from ..db import get_conn, get_setting, tx

log = logging.getLogger(__name__)
_probe_lock = threading.Lock()
_probing = {"on": False}

AUDIO_EXTS = {
    ".m4a",
    ".mp3",
    ".wav",
    ".amr",
    ".aac",
    ".opus",
    ".ogg",
    ".flac",
    ".mp4",
    ".wma",
    ".3gp",
    ".silk",
    ".m4b",
    ".webm",
}


def _walk(root: Path) -> list[os.DirEntry]:
    out: list[os.DirEntry] = []
    for entry in os.scandir(root):
        if entry.is_dir(follow_symlinks=False):
            out.extend(_walk(Path(entry.path)))
        elif entry.is_file(follow_symlinks=False):
            if Path(entry.name).suffix.lower() in AUDIO_EXTS:
                out.append(entry)
    return out


def abs_key(path: str | Path) -> str:
    """统一的入库键：绝对路径 + / 分隔（扫描与上传共用一套写法）。"""
    return str(Path(path).expanduser().resolve()).replace("\\", "/")


def scan(root_dir: str) -> dict:
    """扫描目录，返回 {added, updated, missing, total}。

    rel_path 存**绝对路径**：这样换一个录音目录扫描时，别的目录里的文件（以及上传的副本）
    不会被误判成 missing。missing 只在本目录范围内生效。
    """
    root = Path(root_dir).expanduser()
    if not root.is_dir():
        raise FileNotFoundError(f"目录不存在: {root}")
    root = root.resolve()
    prefix = abs_key(root).lower().rstrip("/") + "/"

    conn = get_conn()
    existing = {r["rel_path"]: r for r in conn.execute("SELECT * FROM files")}
    seen: set[str] = set()
    added = updated = 0

    entries = _walk(root)
    rows = []
    for e in entries:
        rel = abs_key(e.path)
        seen.add(rel.lower())
        st = e.stat()
        rows.append((rel, e.name, Path(e.name).suffix.lower().lstrip("."), st.st_size, st.st_mtime))

    with tx() as c:
        # 新增
        for rel, name, ext, size, mtime in rows:
            if rel not in existing:
                c.execute(
                    "INSERT OR IGNORE INTO files(rel_path, name, ext, size, mtime) VALUES(?,?,?,?,?)",
                    (rel, name, ext, size, mtime),
                )
                added += 1
        # 已存在：文件被替换则重置状态
        for rel, _name, _ext, size, mtime in rows:
            old = existing.get(rel)
            if old is None:
                continue
            if int(old["size"]) != int(size) or abs(float(old["mtime"]) - float(mtime)) > 1:
                c.execute(
                    "UPDATE files SET size=?, mtime=?, status='idle', error=NULL, updated_at=datetime('now','localtime') "
                    "WHERE id=?",
                    (size, mtime, old["id"]),
                )
                c.execute("DELETE FROM transcripts WHERE file_id=?", (old["id"],))
                updated += 1
        # 丢失
        missing = 0
        for rel, old in existing.items():
            if not rel.lower().startswith(prefix):
                continue  # 不属于本次扫描的目录，别动它
            if rel.lower() in seen:
                if old["status"] == "missing":
                    c.execute("UPDATE files SET status='idle' WHERE id=?", (old["id"],))
                continue
            if old["status"] != "missing":
                c.execute("UPDATE files SET status='missing' WHERE id=?", (old["id"],))
                missing += 1
    return {"added": added, "updated": updated, "missing": missing, "total": len(rows)}


def _probe_one(path: str) -> float | None:
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return float(out.stdout.strip())
    except Exception:
        return None


def _probe_worker(root: Path) -> None:
    try:
        conn = get_conn()
        while True:
            rows = conn.execute(
                "SELECT id, rel_path FROM files WHERE duration IS NULL AND status<>'missing' LIMIT 50"
            ).fetchall()
            if not rows:
                break
            for r in rows:
                rel = Path(r["rel_path"])
                d = _probe_one(str(rel if rel.is_absolute() else root / rel))
                if d is not None:
                    with tx() as c:
                        c.execute(
                            "UPDATE files SET duration=? WHERE id=? AND duration IS NULL",
                            (d, r["id"]),
                        )
    except Exception as e:  # 后台任务失败不影响主流程
        log.warning("探测时长失败: %s", e)
    finally:
        with _probe_lock:
            _probing["on"] = False


def start_duration_probe(root_dir: str) -> bool:
    """后台补齐未知时长（ffprobe 逐个跑，不阻塞接口）。已有任务时返回 False。"""
    with _probe_lock:
        if _probing["on"]:
            return False
        _probing["on"] = True
    t = threading.Thread(
        target=_probe_worker, args=(Path(root_dir),), name="duration-probe", daemon=True
    )
    t.start()
    return True


def _auto_scan_worker() -> None:
    """定时扫描（设置 auto_scan_minutes，0 表示关闭）。每 15 秒看一次是否到点。"""
    last = 0.0
    while not _autoscan["stop"]:
        time.sleep(min(15.0, max(1.0, _autoscan["tick"])))
        try:
            minutes = float(get_setting("auto_scan_minutes") or 0)
        except (TypeError, ValueError):
            minutes = 0.0
        if minutes <= 0 or time.time() - last < minutes * 60:
            continue
        last = time.time()
        try:
            root = str(get_setting("input_dir"))
            from . import library

            if library.state().get("running"):
                continue
            res = library.import_dir(root)
            log.info("定时导入：%s", res)
            if res["added"]:
                from ..config import LIBRARY_DIR

                start_duration_probe(str(LIBRARY_DIR))
        except Exception as e:  # 定时任务失败不退出循环
            log.warning("定时扫描失败: %s", e)


_autoscan = {"stop": False, "thread": None, "tick": 15.0}


def start_auto_scan() -> bool:
    """启动定时扫描线程（幂等）。"""
    if _autoscan["thread"] and _autoscan["thread"].is_alive():
        return False
    _autoscan["stop"] = False
    t = threading.Thread(target=_auto_scan_worker, name="auto-scan", daemon=True)
    _autoscan["thread"] = t
    t.start()
    return True


def stop_auto_scan() -> None:
    _autoscan["stop"] = True
    t = _autoscan["thread"]
    if t and t.is_alive():
        t.join(timeout=3)
    _autoscan["thread"] = None
