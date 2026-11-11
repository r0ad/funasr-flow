"""声纹库：登记、改名、删除、声纹质心匹配工具。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from ..config import safe_unlink, temp_path
from ..db import all_settings, get_conn, get_setting, tx

PALETTE = ["#4C8DFF", "#FF7A59", "#22C55E", "#A855F7", "#EAB308", "#06B6D4", "#F43F5E", "#8B5CF6"]


def normalize(vec: np.ndarray) -> np.ndarray:
    v = np.asarray(vec, dtype=np.float32).reshape(-1)
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def list_speakers() -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT s.*, (SELECT COUNT(DISTINCT file_id) FROM file_speakers WHERE speaker_id=s.id) files
           FROM speakers s ORDER BY s.id"""
    ).fetchall()
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "color": r["color"],
            "notes": r["notes"],
            "created_at": r["created_at"],
            "has_embedding": r["embedding"] is not None,
            "file_count": r["files"],
        }
        for r in rows
    ]


def upsert_speaker(
    name: str,
    embedding: bytes | np.ndarray | None,
    overwrite: bool = False,
    color: str | None = None,
) -> int:
    name = name.strip()
    if not name:
        raise ValueError("姓名不能为空")
    blob = None
    if embedding is not None:
        blob = (
            normalize(
                np.frombuffer(embedding, dtype=np.float32)
                if isinstance(embedding, bytes)
                else embedding
            )
            .astype(np.float32)
            .tobytes()
        )
    conn = get_conn()
    row = conn.execute("SELECT id, embedding FROM speakers WHERE name=?", (name,)).fetchone()
    with tx() as c:
        if row is None:
            n = c.execute("SELECT COUNT(*) n FROM speakers").fetchone()["n"]
            cur = c.execute(
                "INSERT INTO speakers(name, color, embedding) VALUES(?,?,?)",
                (name, color or PALETTE[n % len(PALETTE)], blob),
            )
            return int(cur.lastrowid)
        if blob is not None and (overwrite or row["embedding"] is None):
            c.execute("UPDATE speakers SET embedding=? WHERE id=?", (blob, row["id"]))
        return int(row["id"])


def delete_speaker(speaker_id: int) -> None:
    with tx() as c:
        c.execute("DELETE FROM speakers WHERE id=?", (speaker_id,))


def rename_speaker(speaker_id: int, name: str, color: str | None = None) -> None:
    with tx() as c:
        if color:
            c.execute(
                "UPDATE speakers SET name=?, color=? WHERE id=?", (name.strip(), color, speaker_id)
            )
        else:
            c.execute("UPDATE speakers SET name=? WHERE id=?", (name.strip(), speaker_id))
        c.execute(
            "UPDATE file_speakers SET display_name=? WHERE speaker_id=?", (name.strip(), speaker_id)
        )


def embed_slice(src: Path, start_ms: int, end_ms: int) -> np.ndarray:
    """从原始音频截取片段并提取声纹（不必先转整段 wav）。"""
    from ..engines.registry import get_engine, load_builtin

    load_builtin()
    cfg = all_settings()
    engine = get_engine(str(cfg.get("engine", "funasr")))
    tmp = temp_path(suffix=".wav", prefix="slice-")
    try:
        ss = max(0, start_ms) / 1000
        to = max(ss + 0.3, end_ms / 1000)
        cmd = [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-ss",
            f"{ss:.3f}",
            "-to",
            f"{to:.3f}",
            "-i",
            str(src),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(tmp),
        ]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError(f"截取音频失败: {p.stderr[-300:]}")
        import soundfile as sf

        audio, sr = sf.read(str(tmp), dtype="float32", always_2d=False)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        return normalize(engine.embed_audio(audio, cfg))
    finally:
        safe_unlink(tmp)


def slice_embedding(file_id: int, start_ms: int, end_ms: int) -> np.ndarray:
    """从录音库里某条录音的指定区间提取声纹。"""
    conn = get_conn()
    row = conn.execute("SELECT rel_path FROM files WHERE id=?", (file_id,)).fetchone()
    if row is None:
        raise ValueError("文件不存在")
    src = Path(str(get_setting("input_dir"))) / Path(row["rel_path"])
    if not src.exists():
        raise ValueError("源文件已丢失")
    return embed_slice(src, start_ms, end_ms)


def enroll_from_file_range(
    file_id: int, start_ms: int, end_ms: int, name: str, overwrite: bool = False
) -> int:
    return upsert_speaker(name, slice_embedding(file_id, start_ms, end_ms), overwrite=overwrite)


def set_speaker_embedding(speaker_id: int, embedding: bytes | np.ndarray) -> None:
    """覆盖已有说话人的声纹（不改名、不新建）。"""
    blob = (
        normalize(
            np.frombuffer(embedding, dtype=np.float32)
            if isinstance(embedding, bytes)
            else embedding
        )
        .astype(np.float32)
        .tobytes()
    )
    with tx() as c:
        c.execute("UPDATE speakers SET embedding=? WHERE id=?", (blob, speaker_id))
