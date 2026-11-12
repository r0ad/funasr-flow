"""文件库相关接口：导入、分页列表、音频流、批量操作。"""

from __future__ import annotations

import re
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from ..config import LIBRARY_DIR
from ..db import get_conn, get_metric, get_setting, tx
from ..services import library
from ..services import queue as q
from ..services.scanner import AUDIO_EXTS
from .deps import file_dict, resolve_audio, speaker_color, stream_audio

router = APIRouter(prefix="/api/files", tags=["files"])

SORTS = {
    "name": "f.name COLLATE NOCASE",
    "duration": "COALESCE(f.duration, 0)",
    "size": "f.size",
    "status": "f.status",
    "updated": "f.updated_at",
}


_BAD_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


class TranscribeAllBody(BaseModel):
    """全部转写的参数：redo=连已完成的也重跑，dry_run=只估算不入队。"""

    redo: bool = False
    dry_run: bool = False


def _safe_name(name: str) -> str:
    """只留文件名本身，压掉路径分隔符和 Windows 非法字符。"""
    base = Path(str(name).replace("\\", "/")).name
    base = _BAD_CHARS.sub("_", base).strip(" .") or "录音"
    stem, ext = Path(base).stem, Path(base).suffix.lower()
    if ext not in AUDIO_EXTS:
        raise HTTPException(400, f"不支持的音频格式: {ext or '（无扩展名）'}")
    return f"{stem[:80]}{ext}"


@router.post("/upload")
async def upload(request: Request, name: str = Query(..., description="原始文件名")) -> dict:
    """接收单个音频文件（请求体是原始字节），存到 data/uploads/ 后登记入库。

    不走 multipart：省掉 python-multipart 依赖，界面用 XHR 直传文件对象即可。
    """
    filename = _safe_name(name)
    # 若传了子目录（界面上传时保留用户选的相对路径），只取最后一段，避免目录穿越
    target = library.unique_target(filename)

    size = 0
    try:
        with target.open("wb") as fh:
            async for chunk in request.stream():
                if chunk:
                    fh.write(chunk)
                    size += len(chunk)
    except Exception as exc:  # noqa: BLE001
        target.unlink(missing_ok=True)
        raise HTTPException(500, f"写入失败: {exc}") from exc

    if size == 0:
        target.unlink(missing_ok=True)
        raise HTTPException(400, "空文件")

    # 同名同大小视为同一条录音：丢掉刚写的副本，直接跳过（浏览器整文件夹重传时常见）
    dup = (
        get_conn()
        .execute(
            "SELECT * FROM files WHERE name=? AND size=? AND status != 'missing' ORDER BY id LIMIT 1",
            (filename, size),
        )
        .fetchone()
    )
    if dup is not None:
        target.unlink(missing_ok=True)
        has_tr = bool(
            get_conn()
            .execute("SELECT 1 FROM transcripts WHERE file_id=? LIMIT 1", (dup["id"],))
            .fetchone()
        )
        return {"ok": True, "skipped": True, "file": file_dict(dup, has_transcript=has_tr)}

    rel = str(target.resolve())
    now = time.time()
    with tx() as c:
        cur = c.execute(
            "INSERT INTO files(rel_path, name, ext, size, mtime) VALUES(?,?,?,?,?)",
            (rel, target.name, target.suffix.lower(), size, now),
        )
        fid = int(cur.lastrowid or 0)
    row = get_conn().execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()
    return {
        "ok": True,
        "file": file_dict(row, has_transcript=False, model=None, segment_count=0, speakers=[]),
    }


@router.get("")
def list_files(
    status: str | None = None,
    q_text: str | None = Query(None, alias="q"),
    sort: str = "name",
    order: str = "asc",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    limit = max(1, min(int(50 if limit is None else limit), 1000))
    offset = max(0, int(offset or 0))
    where, params = [], []
    if status:
        codes = [s.strip() for s in status.split(",") if s.strip()]
        where.append(f"f.status IN ({','.join('?' * len(codes))})")
        params.extend(codes)
    if q_text:
        where.append("f.name LIKE ?")
        params.append(f"%{q_text}%")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    order_sql = SORTS.get(sort, SORTS["name"])
    direction = "DESC" if order.lower() == "desc" else "ASC"
    conn = get_conn()
    total = conn.execute(f"SELECT COUNT(*) n FROM files f {clause}", params).fetchone()["n"]
    rows = conn.execute(
        f"""SELECT f.*, t.id AS tid, t.model AS model,
                   (SELECT COUNT(*) FROM segments s WHERE s.transcript_id=t.id) AS seg_count
            FROM files f LEFT JOIN transcripts t ON t.file_id=f.id
            {clause} ORDER BY {order_sql} {direction} LIMIT ? OFFSET ?""",
        (*params, limit, offset),
    ).fetchall()

    ids = [r["id"] for r in rows]
    spk_map: dict[int, list] = {i: [] for i in ids}
    if ids:
        for s in conn.execute(
            f"SELECT fs.file_id, fs.cluster, fs.display_name, fs.seconds, fs.speaker_id, sp.name "
            f"FROM file_speakers fs LEFT JOIN speakers sp ON sp.id=fs.speaker_id "
            f"WHERE fs.file_id IN ({','.join('?' * len(ids))}) ORDER BY fs.seconds DESC",
            ids,
        ):
            spk_map[s["file_id"]].append(
                {
                    "cluster": s["cluster"],
                    "name": s["display_name"] or s["name"] or s["cluster"],
                    "seconds": round(s["seconds"] or 0, 1),
                    "speaker_id": s["speaker_id"],
                    "color": speaker_color(s["cluster"]),
                }
            )

    items = [
        file_dict(
            r,
            has_transcript=r["tid"] is not None,
            model=r["model"],
            segment_count=r["seg_count"] or 0,
            speakers=spk_map.get(r["id"], []),
        )
        for r in rows
    ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/library")
def library_info() -> dict:
    """资料库概况（文件数/占用）+ 当前导入进度。必须注册在 `/{file_id}` 之前，否则被它吃掉。"""
    return {"ok": True, **library.stats(), "import": library.state()}


@router.post("/transcribe-all")
def transcribe_all(body: TranscribeAllBody | None = None) -> dict:
    """把所有还没转写的录音一次加入队列。

    `redo=true` 时连已完成的也重跑；`dry_run=true` 只算数量与预计耗时，不入队。
    返回里 `seconds` 是候选音频总时长，`eta_seconds` 按历史 rtf 估算。
    """
    body = body or TranscribeAllBody()
    conn = get_conn()
    if body.redo:
        rows = conn.execute("SELECT id, duration FROM files WHERE status != 'missing'").fetchall()
    else:
        rows = conn.execute(
            """SELECT f.id, f.duration FROM files f
               WHERE f.status IN ('idle', 'failed')
                 AND NOT EXISTS (SELECT 1 FROM transcripts t WHERE t.file_id = f.id)"""
        ).fetchall()
    ids = [r["id"] for r in rows]
    seconds = sum(float(r["duration"] or 0) for r in rows)
    rtf = get_metric("rtf_est", 0.0) or 0.1
    out = {
        "ok": True,
        "redo": body.redo,
        "candidates": len(ids),
        "seconds": round(seconds, 1),
        "eta_seconds": round(seconds * rtf, 0),
    }
    if body.dry_run or not ids:
        return {**out, "queued": 0, "skipped": 0, "job_ids": []}
    return {**out, **q.enqueue(ids, None)}


@router.get("/{file_id}")
def get_file(file_id: int) -> dict:
    conn = get_conn()
    row = conn.execute(
        "SELECT f.*, t.id tid, t.model FROM files f LEFT JOIN transcripts t ON t.file_id=f.id WHERE f.id=?",
        (file_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "文件不存在")
    speakers = [
        {
            "cluster": s["cluster"],
            "name": s["display_name"] or s["name"] or s["cluster"],
            "confidence": s["confidence"],
            "seconds": s["seconds"],
            "utt_count": s["utt_count"],
            "speaker_id": s["speaker_id"],
            "color": speaker_color(s["cluster"]),
        }
        for s in conn.execute(
            "SELECT fs.*, sp.name FROM file_speakers fs LEFT JOIN speakers sp ON sp.id=fs.speaker_id "
            "WHERE fs.file_id=? ORDER BY fs.seconds DESC",
            (file_id,),
        )
    ]
    return file_dict(
        row, has_transcript=row["tid"] is not None, model=row["model"], speakers=speakers
    )


@router.get("/{file_id}/audio")
def get_audio(file_id: int, request: Request):
    path: Path = resolve_audio(file_id)
    return stream_audio(path, request.headers.get("range"))


class ScanBody(BaseModel):
    dir: str | None = None
    mode: str | None = None  # copy（默认）| link


@router.post("/scan")
def do_scan(body: ScanBody | None = None) -> dict:
    """导入一个目录里的录音到资料库（后台跑，进度看 /api/state 的 import 字段）。

    传 ``dir`` = 从该目录导入；不传 = 重新扫一遍资料库（发现被手工删掉的文件）。
    """
    if body and body.dir:
        from ..db import set_setting

        root = Path(body.dir).expanduser()
        # 先同步确认目录存在：start_import 是后台线程，异常只会写进 import.error
        if not root.is_dir():
            raise HTTPException(400, f"目录不存在：{body.dir}")
        body.dir = str(root)
        set_setting("input_dir", body.dir)
        mode = (body.mode or str(get_setting("ingest_mode") or "copy")).lower()
        res = library.start_import(body.dir, mode)
        if not res.get("started"):
            raise HTTPException(409, res.get("reason") or "已有导入任务在跑")
        state = library.state()
        return {
            "ok": True,
            "importing": bool(state.get("running")),
            "root": body.dir,
            "import": state,
        }

    result = library.rescan()
    from ..services.scanner import start_duration_probe

    start_duration_probe(str(LIBRARY_DIR))
    return {"ok": True, "root": str(LIBRARY_DIR), **result}


@router.post("/import/cancel")
def cancel_import() -> dict:
    library.cancel_import()
    return {"ok": True, "import": library.state()}


class BatchBody(BaseModel):
    ids: list[int]
    action: str = "enqueue"  # enqueue | reset | delete
    options: dict | None = None


@router.post("/batch")
def batch(body: BatchBody) -> dict:
    if body.action == "enqueue":
        return {"ok": True, **q.enqueue(body.ids, body.options)}
    if body.action == "reset":
        with tx() as c:
            for fid in body.ids:
                c.execute("DELETE FROM transcripts WHERE file_id=?", (fid,))
                c.execute("DELETE FROM file_speakers WHERE file_id=?", (fid,))
                c.execute("UPDATE files SET status='idle', error=NULL WHERE id=?", (fid,))
        q.enqueue(body.ids, body.options)
        return {"ok": True, "reset": len(body.ids)}
    if body.action == "delete":
        with tx() as c:
            for fid in body.ids:
                c.execute("DELETE FROM files WHERE id=?", (fid,))
        return {"ok": True, "deleted": len(body.ids)}
    raise HTTPException(400, f"未知操作: {body.action}")


@router.delete("/{file_id}")
def delete_file(file_id: int, purge: bool = True) -> dict:
    """删除记录。资料库（data/library）里的副本是本应用自己的文件，默认一并删掉；
    用户原始目录里的录音永远不动。"""
    conn = get_conn()
    row = conn.execute("SELECT rel_path FROM files WHERE id=?", (file_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "文件不存在")
    path = Path(row["rel_path"])
    with tx() as c:
        c.execute("DELETE FROM files WHERE id=?", (file_id,))
    removed = False
    if purge:
        try:
            if library.is_in_library(path):
                path.unlink(missing_ok=True)
                removed = True
        except OSError:
            removed = False
    return {"ok": True, "purged": removed}
