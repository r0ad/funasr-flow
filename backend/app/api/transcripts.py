"""转写结果接口：读取、编辑、说话人改名、声纹登记、搜索、导出。"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from ..db import get_conn, tx
from ..services import speakers as spk
from .deps import speaker_color

router = APIRouter(prefix="/api", tags=["transcripts"])


def _fmt_clock(ms: int) -> str:
    s = ms / 1000
    return f"{int(s // 3600):02d}:{int(s % 3600 // 60):02d}:{s % 60:06.3f}".replace(".", ",")


def _fmt_short(ms: int) -> str:
    s = ms / 1000
    return f"{int(s // 60):02d}:{s % 60:05.2f}"


def load_transcript(file_id: int) -> dict:
    conn = get_conn()
    f = conn.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
    if f is None:
        raise HTTPException(404, "文件不存在")
    t = conn.execute("SELECT * FROM transcripts WHERE file_id=?", (file_id,)).fetchone()
    if t is None:
        return {"file": dict(f), "transcript": None, "segments": [], "speakers": []}
    segs = [
        {
            "idx": s["idx"],
            "start_ms": s["start_ms"],
            "end_ms": s["end_ms"],
            "speaker": s["speaker"],
            "text": s["text"],
        }
        for s in conn.execute(
            "SELECT * FROM segments WHERE transcript_id=? ORDER BY idx", (t["id"],)
        )
    ]
    speakers = [
        {
            "cluster": s["cluster"],
            "name": s["display_name"] or s["spk_name"] or _default_name(s["cluster"]),
            "display_name": s["display_name"],
            "speaker_id": s["speaker_id"],
            "confidence": s["confidence"],
            "seconds": s["seconds"],
            "utt_count": s["utt_count"],
            "color": speaker_color(s["cluster"]),
        }
        for s in conn.execute(
            "SELECT fs.*, sp.name AS spk_name FROM file_speakers fs "
            "LEFT JOIN speakers sp ON sp.id=fs.speaker_id WHERE fs.file_id=? ORDER BY fs.seconds DESC",
            (file_id,),
        )
    ]
    return {
        "file": dict(f),
        "transcript": {
            "id": t["id"],
            "duration": t["duration"],
            "model": t["model"],
            "asr_ms": t["asr_ms"],
            "created_at": t["created_at"],
            "updated_at": t["updated_at"],
        },
        "segments": segs,
        "speakers": speakers,
    }


def _default_name(cluster: str) -> str:
    import re

    m = re.search(r"(\d+)$", cluster or "")
    return f"说话人{int(m.group(1)) + 1 if m else 1}"


@router.get("/transcripts/{file_id}")
def get_transcript(file_id: int) -> dict:
    return load_transcript(file_id)


class SegmentPatch(BaseModel):
    text: str | None = None
    speaker: str | None = None


@router.patch("/transcripts/{file_id}/segments/{idx}")
def patch_segment(file_id: int, idx: int, body: SegmentPatch) -> dict:
    conn = get_conn()
    t = conn.execute("SELECT id FROM transcripts WHERE file_id=?", (file_id,)).fetchone()
    if t is None:
        raise HTTPException(404, "该文件还没有转写结果")
    sets, params = [], []
    if body.text is not None:
        sets.append("text=?")
        params.append(body.text.strip())
    if body.speaker is not None:
        sets.append("speaker=?")
        params.append(body.speaker)
    if not sets:
        raise HTTPException(400, "没有要修改的字段")
    with tx() as c:
        cur = c.execute(
            f"UPDATE segments SET {', '.join(sets)} WHERE transcript_id=? AND idx=?",
            (*params, t["id"], idx),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "句子不存在")
    return {"ok": True}


class SpeakerPatch(BaseModel):
    display_name: str | None = None
    speaker_id: int | None = None


@router.patch("/transcripts/{file_id}/speakers/{cluster}")
def patch_speaker(file_id: int, cluster: str, body: SpeakerPatch) -> dict:
    with tx() as c:
        row = c.execute(
            "SELECT id FROM file_speakers WHERE file_id=? AND cluster=?", (file_id, cluster)
        ).fetchone()
        if row is None:
            raise HTTPException(404, "说话人不存在")
        if body.display_name is not None:
            c.execute(
                "UPDATE file_speakers SET display_name=? WHERE id=?",
                (body.display_name.strip(), row["id"]),
            )
        if body.speaker_id is not None:
            sp = c.execute("SELECT name FROM speakers WHERE id=?", (body.speaker_id,)).fetchone()
            if sp is None:
                raise HTTPException(400, "声纹库中不存在该人物")
            c.execute(
                "UPDATE file_speakers SET speaker_id=?, display_name=? WHERE id=?",
                (body.speaker_id, sp["name"], row["id"]),
            )
    return {"ok": True}


class EnrollFromCluster(BaseModel):
    name: str
    overwrite: bool = False


@router.post("/transcripts/{file_id}/speakers/{cluster}/enroll")
def enroll_from_cluster(file_id: int, cluster: str, body: EnrollFromCluster) -> dict:
    conn = get_conn()
    row = conn.execute(
        "SELECT centroid FROM file_speakers WHERE file_id=? AND cluster=?", (file_id, cluster)
    ).fetchone()
    if row is None or row["centroid"] is None:
        raise HTTPException(400, "该说话人还没有声纹质心，请先完成转写")
    speaker_id = spk.upsert_speaker(body.name, row["centroid"], overwrite=body.overwrite)
    with tx() as c:
        c.execute(
            "UPDATE file_speakers SET speaker_id=?, display_name=? WHERE file_id=? AND cluster=?",
            (speaker_id, body.name, file_id, cluster),
        )
    return {"ok": True, "speaker_id": speaker_id}


class BulkAssign(BaseModel):
    file_id: int
    cluster: str
    speaker_id: int


@router.post("/transcripts/bulk-assign")
def bulk_assign(body: BulkAssign) -> dict:
    with tx() as c:
        sp = c.execute("SELECT name FROM speakers WHERE id=?", (body.speaker_id,)).fetchone()
        if sp is None:
            raise HTTPException(400, "声纹库中不存在该人物")
        c.execute(
            "UPDATE file_speakers SET speaker_id=?, display_name=? WHERE file_id=? AND cluster=?",
            (body.speaker_id, sp["name"], body.file_id, body.cluster),
        )
        # 同一声纹在其他文件里的历史结果一并更新
        c.execute(
            "UPDATE file_speakers SET speaker_id=? WHERE speaker_id IS NULL AND display_name=?",
            (body.speaker_id, sp["name"]),
        )
    return {"ok": True}


@router.get("/search")
def search(q: str = Query(..., min_length=1), limit: int = 60) -> dict:
    conn = get_conn()
    rows = []
    if len(q.strip()) >= 3:
        try:
            rows = conn.execute(
                """SELECT s.start_ms, s.end_ms, s.text, s.speaker, t.file_id, f.name, f.rel_path
                   FROM segments_fts fts
                   JOIN segments s ON s.id = fts.rowid
                   JOIN transcripts t ON t.id = s.transcript_id
                   JOIN files f ON f.id = t.file_id
                   WHERE segments_fts MATCH ? ORDER BY rank LIMIT ?""",
                (q.strip(), limit),
            ).fetchall()
        except Exception:
            rows = []
    if not rows:
        rows = conn.execute(
            """SELECT s.start_ms, s.end_ms, s.text, s.speaker, t.file_id, f.name, f.rel_path
               FROM segments s JOIN transcripts t ON t.id=s.transcript_id
               JOIN files f ON f.id=t.file_id WHERE s.text LIKE ? LIMIT ?""",
            (f"%{q.strip()}%", limit),
        ).fetchall()
    return {
        "items": [
            {
                "file_id": r["file_id"],
                "name": r["name"],
                "cluster": r["speaker"],
                "start_ms": r["start_ms"],
                "end_ms": r["end_ms"],
                "text": r["text"],
            }
            for r in rows
        ]
    }


# --------------------------------------------------------------------- 导出
def _export_text(file_id: int, fmt: str) -> tuple[str, bytes]:
    data = load_transcript(file_id)
    if data["transcript"] is None:
        raise HTTPException(400, f"{data['file']['name']} 还没有转写结果")
    name = data["file"]["name"]
    names = {s["cluster"]: s["name"] for s in data["speakers"]}
    segs = data["segments"]

    if fmt == "json":
        payload = {
            "name": name,
            "duration": data["transcript"]["duration"],
            "model": data["transcript"]["model"],
            "speakers": data["speakers"],
            "segments": segs,
        }
        return f"{Path(name).stem}.json", json.dumps(payload, ensure_ascii=False, indent=2).encode(
            "utf-8"
        )

    if fmt == "srt":
        lines = []
        for i, s in enumerate(segs, 1):
            who = names.get(s["speaker"], s["speaker"])
            lines.append(str(i))
            lines.append(f"{_fmt_clock(s['start_ms'])} --> {_fmt_clock(s['end_ms'])}")
            lines.append(f"{who}：{s['text']}")
            lines.append("")
        return f"{Path(name).stem}.srt", "\n".join(lines).encode("utf-8")

    if fmt == "md":
        lines = [
            f"# {name}",
            "",
            f"> 时长 {_fmt_short(int((data['transcript']['duration'] or 0) * 1000))} · "
            f"模型 {data['transcript']['model']} · 说话人 {len(data['speakers'])} 人",
            "",
        ]
        for s in segs:
            who = names.get(s["speaker"], s["speaker"])
            lines.append(f"**{who}** `{_fmt_short(s['start_ms'])}`  ")
            lines.append(f"{s['text']}")
            lines.append("")
        return f"{Path(name).stem}.md", "\n".join(lines).encode("utf-8")

    # 默认 txt
    lines = [
        f"# {name}",
        f"# 时长 {_fmt_short(int((data['transcript']['duration'] or 0) * 1000))}",
        "",
    ]
    for s in segs:
        who = names.get(s["speaker"], s["speaker"])
        lines.append(f"[{_fmt_short(s['start_ms'])}] {who}：{s['text']}")
    return f"{Path(name).stem}.txt", "\n".join(lines).encode("utf-8")


def _disposition(fname: str) -> str:
    """中文文件名要用 RFC 5987 编码，否则浏览器下载会乱码。"""
    from urllib.parse import quote

    ascii_name = fname.encode("ascii", "replace").decode("ascii").replace("?", "_") or "transcript"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(fname, safe='')}"


@router.get("/export")
def export(ids: str = Query(...), format: str = "txt") -> Response:
    try:
        file_ids = [int(x) for x in ids.split(",") if x.strip()]
    except ValueError as e:
        raise HTTPException(400, "ids 参数格式错误") from e
    fmt = format if format in {"txt", "md", "srt", "json"} else "txt"

    if len(file_ids) == 1:
        fname, blob = _export_text(file_ids[0], fmt)
        media = {
            "txt": "text/plain; charset=utf-8",
            "md": "text/markdown; charset=utf-8",
            "srt": "application/x-subrip; charset=utf-8",
            "json": "application/json",
        }[fmt]
        return Response(
            blob,
            media_type=media,
            headers={
                "Content-Disposition": _disposition(fname),
            },
        )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fid in file_ids:
            try:
                fname, blob = _export_text(fid, fmt)
            except HTTPException:
                continue
            zf.writestr(fname, blob)
    buf.seek(0)
    return Response(
        buf.read(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="transcripts.zip"'},
    )
