"""声纹库 API：登记、改名、删除、合并、上传音频登记。"""

from __future__ import annotations

import subprocess

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from ..config import safe_unlink, temp_path
from ..db import get_conn, tx
from ..services import speakers as svc

router = APIRouter(prefix="/api/speakers", tags=["speakers"])


class SpeakerPatch(BaseModel):
    name: str | None = None
    color: str | None = None
    notes: str | None = None


class MergeBody(BaseModel):
    source_id: int


@router.get("")
def list_speakers() -> dict:
    return {"items": svc.list_speakers()}


class NewSpeaker(BaseModel):
    name: str
    notes: str | None = None


@router.post("")
def create_speaker(body: NewSpeaker) -> dict:
    """只建名字、暂无声纹（之后可在详情页或上传音频补上）。"""
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "姓名不能为空")
    sid = svc.upsert_speaker(name, None)
    if body.notes:
        with tx() as c:
            c.execute("UPDATE speakers SET notes=? WHERE id=?", (body.notes, sid))
    return {"ok": True, "speaker_id": sid}


# ---------------------------------------------------------------- 具体路径先声明
@router.post("/from-file")
def enroll_from_file(
    file_id: int, start_ms: int, end_ms: int, name: str, overwrite: bool = False
) -> dict:
    try:
        sid = svc.enroll_from_file_range(file_id, start_ms, end_ms, name, overwrite)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(500, str(e)) from e
    return {"ok": True, "speaker_id": sid}


async def _embed_body(request: Request):
    """读 raw body → 解码成 16k 单声道 → 提声纹。用 raw body 免掉 python-multipart 依赖。"""
    data = await request.body()
    if not data:
        raise HTTPException(400, "音频为空")
    src = temp_path(suffix=".bin", prefix="enroll-")
    wav = temp_path(suffix=".wav", prefix="enroll-")
    try:
        src.write_bytes(data)
        p = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-i",
                str(src),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(wav),
            ],
            capture_output=True,
            text=True,
        )
        if p.returncode != 0:
            raise HTTPException(400, f"音频解码失败: {p.stderr[-200:]}")
        import soundfile as sf

        from ..engines.registry import get_engine, load_builtin

        audio, _ = sf.read(str(wav), dtype="float32", always_2d=False)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        load_builtin()
        return get_engine("funasr").embed_audio(audio, {})
    finally:
        safe_unlink(src)
        safe_unlink(wav)


@router.post("/enroll")
async def enroll_new(name: str = Query(...), request: Request = None) -> dict:
    emb = await _embed_body(request)
    return {"ok": True, "speaker_id": svc.upsert_speaker(name, emb, overwrite=True)}


@router.post("/{speaker_id}/enroll-from-file")
def enroll_existing_from_file(speaker_id: int, file_id: int, start_ms: int, end_ms: int) -> dict:
    """用录音库里某条录音的一段音频，覆盖已有说话人的声纹。"""
    if get_conn().execute("SELECT id FROM speakers WHERE id=?", (speaker_id,)).fetchone() is None:
        raise HTTPException(404, "声纹不存在")
    try:
        emb = svc.slice_embedding(file_id, start_ms, end_ms)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(500, str(e)) from e
    svc.set_speaker_embedding(speaker_id, emb)
    return {"ok": True, "speaker_id": speaker_id}


@router.post("/{speaker_id}/enroll")
async def enroll_existing(speaker_id: int, request: Request) -> dict:
    row = get_conn().execute("SELECT name FROM speakers WHERE id=?", (speaker_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "声纹不存在")
    emb = await _embed_body(request)
    return {"ok": True, "speaker_id": svc.upsert_speaker(row["name"], emb, overwrite=True)}


@router.post("/{target_id}/merge")
def merge_into(target_id: int, body: MergeBody) -> dict:
    src = int(body.source_id)
    if src == target_id:
        raise HTTPException(400, "不能与自己合并")
    conn = get_conn()
    rows = conn.execute("SELECT id FROM speakers WHERE id IN (?,?)", (target_id, src)).fetchall()
    if len(rows) != 2:
        raise HTTPException(404, "声纹不存在")
    src_emb = conn.execute("SELECT embedding FROM speakers WHERE id=?", (src,)).fetchone()[
        "embedding"
    ]
    with tx() as c:
        c.execute("UPDATE file_speakers SET speaker_id=? WHERE speaker_id=?", (target_id, src))
        c.execute(
            "UPDATE speakers SET embedding=? WHERE id=? AND embedding IS NULL",
            (src_emb, target_id),
        )
        c.execute("DELETE FROM speakers WHERE id=?", (src,))
    return {"ok": True, "target_id": target_id}


@router.get("/{speaker_id}/files")
def speaker_files(speaker_id: int) -> dict:
    rows = (
        get_conn()
        .execute(
            """SELECT f.id, f.name, f.duration, fs.cluster, fs.confidence
           FROM file_speakers fs JOIN files f ON f.id=fs.file_id
           WHERE fs.speaker_id=? ORDER BY f.mtime DESC""",
            (speaker_id,),
        )
        .fetchall()
    )
    return {"items": [dict(r) for r in rows]}


@router.patch("/{speaker_id}")
def patch_speaker(speaker_id: int, body: SpeakerPatch) -> dict:
    conn = get_conn()
    if conn.execute("SELECT id FROM speakers WHERE id=?", (speaker_id,)).fetchone() is None:
        raise HTTPException(404, "声纹不存在")
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "姓名不能为空")
        if conn.execute(
            "SELECT id FROM speakers WHERE name=? AND id<>?", (name, speaker_id)
        ).fetchone():
            raise HTTPException(409, "同名声纹已存在")
        svc.rename_speaker(speaker_id, name, body.color)
    elif body.color:
        with tx() as c:
            c.execute("UPDATE speakers SET color=? WHERE id=?", (body.color, speaker_id))
    if body.notes is not None:
        with tx() as c:
            c.execute("UPDATE speakers SET notes=? WHERE id=?", (body.notes, speaker_id))
    return {"ok": True}


@router.delete("/{speaker_id}")
def delete_speaker(speaker_id: int) -> dict:
    svc.delete_speaker(speaker_id)
    return {"ok": True}
