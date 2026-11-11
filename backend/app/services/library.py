"""资料库（library）：把外部录音导入到应用自己管理的目录。

为什么要导入而不是「引用原目录」：

* 容器里看到的宿主机路径（/host/h/...）只有在带着对应 ``-v`` 启动时才存在，
  换挂载点或重启容器后，旧路径全部失效，界面就显示「文件丢失」；
* 宿主机直接跑和容器里跑，同一条录音的绝对路径根本不一样，没法共用一个库；
* 手机导出的目录经常被清理/移动。

所以：**导入时把音频复制进 ``data/library``（容器里是 /data 卷）**，DB 只记资料库里
的绝对路径，``source_path`` 留一份来源信息用于查重与显示。之后原目录删了、换挂载、
换机器重启都不影响已有录音和转写结果。
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from pathlib import Path

from ..config import LIBRARY_DIR
from ..db import get_conn, tx
from .scanner import AUDIO_EXTS, abs_key

log = logging.getLogger(__name__)

INGEST_MODES = {"copy", "link"}  # link 只在同一文件系统可用，失败自动回退 copy

_lock = threading.Lock()
_state: dict = {
    "running": False,
    "mode": "copy",
    "dir": "",
    "total": 0,
    "done": 0,
    "added": 0,
    "skipped": 0,
    "replaced": 0,
    "failed": 0,
    "current": "",
    "started_at": None,
    "finished_at": None,
    "error": None,
}
_thread: threading.Thread | None = None
_cancel = {"on": False}


# --------------------------------------------------------------------------- 路径工具
def file_path(rel_path: str | Path) -> Path:
    """把 files.rel_path 还原成磁盘路径（入库时一律写绝对路径）。"""
    return Path(str(rel_path))


def is_in_library(path: str | Path) -> bool:
    try:
        return Path(path).resolve().is_relative_to(LIBRARY_DIR.resolve())
    except (OSError, ValueError):
        return False


def unique_target(name: str) -> Path:
    """资料库里的目标路径：重名自动加 `` (2)``、`` (3)``。"""
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    safe = Path(name).name
    target = LIBRARY_DIR / safe
    n = 2
    while target.exists():
        target = LIBRARY_DIR / f"{Path(safe).stem} ({n}){Path(safe).suffix}"
        n += 1
    return target


# --------------------------------------------------------------------------- 进度
def state() -> dict:
    with _lock:
        return dict(_state)


def _update(**kw) -> None:
    with _lock:
        _state.update(kw)


def cancel_import() -> None:
    _cancel["on"] = True


# --------------------------------------------------------------------------- 单个文件
def import_one(src: Path, mode: str = "copy") -> dict:
    """把一个音频导进资料库。返回 {status: added|skipped|replaced, id, path}。"""
    src = src.expanduser()
    if not src.is_file():
        raise FileNotFoundError(str(src))
    if src.suffix.lower() not in AUDIO_EXTS:
        raise ValueError(f"不支持的音频格式: {src.suffix}")

    st = src.stat()
    key = abs_key(src)
    conn = get_conn()

    if is_in_library(src):
        # 源文件本来就在资料库里（用户直接把音频拷进 data/library 后点了「导入这个目录」）：
        # 不复制、不产生 " (2)" 副本，缺记录就补一条，其余交给「重新扫描资料库」。
        row = conn.execute("SELECT id FROM files WHERE rel_path=?", (key,)).fetchone()
        if row is not None:
            return {"status": "skipped", "id": int(row["id"]), "path": key}
        with tx() as c:
            cur = c.execute(
                "INSERT INTO files(rel_path, source_path, name, ext, size, mtime) VALUES(?,?,?,?,?,?)",
                (key, None, src.name, src.suffix.lower(), st.st_size, st.st_mtime),
            )
            fid = int(cur.lastrowid or 0)
        return {"status": "added", "id": fid, "path": key}

    old = conn.execute(
        "SELECT id, rel_path, size FROM files WHERE source_path=?", (key,)
    ).fetchone()
    if old is not None and int(old["size"]) == st.st_size and file_path(old["rel_path"]).exists():
        return {"status": "skipped", "id": int(old["id"]), "path": old["rel_path"]}

    if old is not None:
        target = file_path(old["rel_path"])  # 内容变了：覆盖原副本
    else:
        target = unique_target(src.name)

    if mode == "link":
        try:
            if target.exists():
                target.unlink()
            os.link(src, target)
        except OSError:
            shutil.copy2(src, target)  # 跨盘/权限问题就退回复制
    else:
        shutil.copy2(src, target)

    if target.stat().st_size != st.st_size:  # 断流/磁盘满，宁可报错也别留下坏文件
        target.unlink(missing_ok=True)
        raise OSError(f"复制不完整: {src.name}")

    rel = abs_key(target)
    with tx() as c:
        if old is not None:
            c.execute(
                "UPDATE files SET rel_path=?, name=?, ext=?, size=?, mtime=?, status='idle', "
                "error=NULL, updated_at=datetime('now','localtime') WHERE id=?",
                (rel, target.name, target.suffix.lower(), st.st_size, st.st_mtime, old["id"]),
            )
            c.execute("DELETE FROM transcripts WHERE file_id=?", (old["id"],))
            fid = int(old["id"])
            status = "replaced"
        else:
            cur = c.execute(
                "INSERT INTO files(rel_path, source_path, name, ext, size, mtime) VALUES(?,?,?,?,?,?)",
                (rel, key, target.name, target.suffix.lower(), st.st_size, st.st_mtime),
            )
            fid = int(cur.lastrowid or 0)
            status = "added"
    return {"status": status, "id": fid, "path": rel}


# --------------------------------------------------------------------------- 整目录
def import_dir(root: str | Path, mode: str = "copy", should_cancel=None) -> dict:
    """导入整个目录（递归）。返回计数；进度通过 state() 暴露。"""
    root_path = Path(root).expanduser()
    if not root_path.is_dir():
        raise FileNotFoundError(f"目录不存在: {root_path}")
    if mode not in INGEST_MODES:
        mode = "copy"

    if is_in_library(root_path):
        # 直接导入资料库目录本身 = 重扫一遍，把手工拷进来的音频登记上
        res = rescan()
        return {
            "dir": str(root_path),
            "total": res["total"],
            "added": res["added"],
            "skipped": 0,
            "replaced": 0,
            "failed": 0,
            "rescanned": True,
        }

    files: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(root_path):
        for name in filenames:
            if Path(name).suffix.lower() in AUDIO_EXTS:
                files.append(Path(dirpath) / name)
    files.sort()

    _update(
        running=True,
        mode=mode,
        dir=str(root_path),
        total=len(files),
        done=0,
        added=0,
        skipped=0,
        replaced=0,
        failed=0,
        current="",
        started_at=time.time(),
        finished_at=None,
        error=None,
    )

    added = skipped = replaced = 0
    failed = 0
    try:
        for i, f in enumerate(files, 1):
            if should_cancel is not None and should_cancel():
                raise InterruptedError("导入已取消")
            _update(done=i - 1, current=f.name)
            try:
                res = import_one(f, mode)
            except Exception as exc:  # noqa: BLE001 - 单个文件失败不打断整批
                failed += 1
                log.warning("导入失败 %s: %s", f, exc)
            else:
                if res["status"] == "skipped":
                    skipped += 1
                else:
                    # 覆盖也计入 added（用户视角"导进来了"），但单独给 replaced 供界面显示
                    added += 1
                    if res["status"] == "replaced":
                        replaced += 1
            _update(done=i, added=added, skipped=skipped, replaced=replaced, failed=failed)
    except InterruptedError as exc:
        _update(error=str(exc))
    except Exception as exc:  # noqa: BLE001
        _update(error=f"{type(exc).__name__}: {exc}")
        log.exception("导入目录失败")
    finally:
        _update(running=False, current="", finished_at=time.time())
        # 新入库的文件还没有时长：让后台探针补上（列表里立刻能看到 0:xx）
        try:
            from .scanner import start_duration_probe

            start_duration_probe(str(LIBRARY_DIR))
        except Exception as exc:  # noqa: BLE001 - 补时长失败不影响导入结果
            log.warning("补时长失败: %s", exc)

    return {
        "dir": str(root_path),
        "total": len(files),
        "added": added,
        "skipped": skipped,
        "replaced": replaced,
        "failed": failed,
    }


def start_import(root: str, mode: str = "copy") -> dict:
    """后台线程导入，立刻返回当前进度（界面轮询 /api/state）。"""
    global _thread
    with _lock:
        if _state["running"]:
            return {"started": False, "reason": "已有导入任务在跑", **dict(_state)}
    _cancel["on"] = False
    _thread = threading.Thread(
        target=import_dir,
        args=(root, mode),
        kwargs={"should_cancel": lambda: _cancel["on"]},
        daemon=True,
        name="import",
    )
    _thread.start()
    time.sleep(0.05)
    return {"started": True, **state()}


# --------------------------------------------------------------------------- 资料库自身
def rescan() -> dict:
    """重新扫一遍资料库目录：发现被手工删掉的文件、刷新时长。"""
    from .scanner import scan

    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    return scan(str(LIBRARY_DIR))


def stats() -> dict:
    """资料库自身的统计（只算真正落在 LIBRARY_DIR 里的副本，不含仍指向外部目录的老记录）。"""
    prefix = abs_key(LIBRARY_DIR).lower() + "/"
    rows = get_conn().execute("SELECT rel_path FROM files").fetchall()
    total = 0
    count = 0
    for r in rows:
        p = file_path(r["rel_path"])
        if not str(p).replace("\\", "/").lower().startswith(prefix):
            continue
        count += 1
        try:
            total += p.stat().st_size
        except OSError:  # 记录还在但文件被手工删了：计数保留，体积不计
            continue
    return {"dir": str(LIBRARY_DIR), "files": count, "bytes": total}
