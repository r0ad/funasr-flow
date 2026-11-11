"""API 公共工具：行对象序列化、音频区间读取。"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from ..db import get_conn, get_setting

SPEAKER_COLORS = [
    "#4C8DFF",
    "#FF7A59",
    "#22C55E",
    "#A855F7",
    "#EAB308",
    "#06B6D4",
    "#F43F5E",
    "#8B5CF6",
]


def file_dict(row: Any, **extra: Any) -> dict:
    d = {
        "id": row["id"],
        "name": row["name"],
        "rel_path": row["rel_path"],
        "ext": row["ext"],
        "size": row["size"],
        "duration": row["duration"],
        "status": row["status"],
        "error": row["error"],
        "updated_at": row["updated_at"],
    }
    d.update(extra)
    return d


def speaker_color(cluster: str, names: Iterable[str] = ()) -> str:
    m = re.search(r"(\d+)$", cluster or "")
    idx = int(m.group(1)) if m else 0
    return SPEAKER_COLORS[idx % len(SPEAKER_COLORS)]


def resolve_audio(file_id: int) -> Path:
    conn = get_conn()
    row = conn.execute("SELECT rel_path FROM files WHERE id=?", (file_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "文件不存在")
    path = Path(str(get_setting("input_dir"))) / Path(row["rel_path"])
    if not path.exists():
        raise HTTPException(410, "源文件已丢失")
    return path


RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def stream_audio(path: Path, range_header: str | None) -> Any:
    """支持 HTTP Range 的音频流，浏览器可拖动进度条。"""
    size = os.path.getsize(path)
    media = "audio/mp4" if path.suffix.lower() in {".m4a", ".mp4", ".m4b"} else "audio/mpeg"
    if path.suffix.lower() == ".wav":
        media = "audio/wav"
    if not range_header:

        def full():
            with open(path, "rb") as f:
                while chunk := f.read(256 * 1024):
                    yield chunk

        return StreamingResponse(
            full(),
            media_type=media,
            headers={"Accept-Ranges": "bytes", "Content-Length": str(size)},
        )

    m = RANGE_RE.match(range_header.strip())
    if not m:
        raise HTTPException(416, "无效的 Range")
    start = int(m.group(1)) if m.group(1) else 0
    end = int(m.group(2)) if m.group(2) else size - 1
    end = min(end, size - 1)
    if start > end:
        raise HTTPException(416, "Range 越界")
    length = end - start + 1

    def partial():
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(
        partial(),
        status_code=206,
        media_type=media,
        headers={
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
        },
    )
