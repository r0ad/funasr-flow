"""文件库相关接口：扫描、分页列表、音频流、批量操作。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from ..db import get_conn, get_setting, tx
from ..services import queue as q
from ..services.scanner import scan as scan_dir
from .deps import file_dict, resolve_audio, speaker_color, stream_audio

router = APIRouter(prefix="/api/files", tags=["files"])

SORTS = {
    "name": "f.name COLLATE NOCASE",
    "duration": "COALESCE(f.duration, 0)",
    "size": "f.size",
    "status": "f.status",
    "updated": "f.updated_at",
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


@router.post("/scan")
def do_scan(body: ScanBody | None = None) -> dict:
    root = (body.dir if body and body.dir else None) or str(get_setting("input_dir"))
    if body and body.dir:
        from ..db import set_setting

        set_setting("input_dir", body.dir)
    try:
        result = scan_dir(root)
    except FileNotFoundError as e:
        raise HTTPException(400, str(e)) from e
    from ..services.scanner import start_duration_probe

    start_duration_probe(root)
    return {"ok": True, "root": root, **result}


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
def delete_file(file_id: int) -> dict:
    with tx() as c:
        c.execute("DELETE FROM files WHERE id=?", (file_id,))
    return {"ok": True}
