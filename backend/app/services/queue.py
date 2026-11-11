"""任务队列：入队、领取、取消、恢复。全部以 SQLite 为准，跨进程安全。"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from ..db import get_conn, tx


def enqueue(file_ids: Iterable[int], options: dict[str, Any] | None = None) -> dict:
    """把文件加入队列。已在队列/运行中的会跳过。返回 {queued, skipped}。"""
    ids = [int(i) for i in file_ids]
    if not ids:
        return {"queued": 0, "skipped": 0, "job_ids": []}

    opts = json.dumps(options or {}, ensure_ascii=False)
    queued, skipped, job_ids = 0, 0, []
    with tx() as c:
        for fid in ids:
            f = c.execute("SELECT id, status FROM files WHERE id=?", (fid,)).fetchone()
            if f is None:
                skipped += 1
                continue
            active = c.execute(
                "SELECT id FROM jobs WHERE file_id=? AND status IN ('queued','running')",
                (fid,),
            ).fetchone()
            if active:
                skipped += 1
                job_ids.append(active["id"])
                continue
            cur = c.execute("INSERT INTO jobs(file_id, options) VALUES(?,?)", (fid, opts))
            job_ids.append(cur.lastrowid)
            c.execute(
                "UPDATE files SET status='queued', error=NULL, updated_at=datetime('now','localtime') WHERE id=?",
                (fid,),
            )
            queued += 1
    return {"queued": queued, "skipped": skipped, "job_ids": job_ids}


def claim_next() -> dict | None:
    """原子领取一个待处理任务（小文件优先，体感更快出结果）。"""
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT j.id AS job_id, j.file_id, f.duration, f.size "
            "FROM jobs j JOIN files f ON f.id=j.file_id "
            "WHERE j.status='queued' ORDER BY COALESCE(f.duration, f.size/16000.0) ASC, j.id ASC LIMIT 1"
        ).fetchone()
        if row is None:
            conn.execute("COMMIT")
            return None
        conn.execute(
            "UPDATE jobs SET status='running', stage='start', progress=0.0, "
            "started_at=datetime('now','localtime'), attempts=attempts+1, cancel_requested=0 WHERE id=?",
            (row["job_id"],),
        )
        conn.execute(
            "UPDATE files SET status='running', updated_at=datetime('now','localtime') WHERE id=?",
            (row["file_id"],),
        )
        conn.execute("COMMIT")
        return {"job_id": row["job_id"], "file_id": row["file_id"]}
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise


def is_cancel_requested(job_id: int) -> bool:
    row = get_conn().execute("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)).fetchone()
    return bool(row and row["cancel_requested"])


def request_cancel(job_id: int) -> bool:
    with tx() as c:
        job = c.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            return False
        if job["status"] == "queued":
            c.execute(
                "UPDATE jobs SET status='canceled', finished_at=datetime('now','localtime') WHERE id=?",
                (job_id,),
            )
            c.execute(
                "UPDATE files SET status='idle' WHERE id=(SELECT file_id FROM jobs WHERE id=?)",
                (job_id,),
            )
            return True
        if job["status"] == "running":
            c.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job_id,))
            return True
        return False


def cancel_queued() -> int:
    with tx() as c:
        rows = c.execute("SELECT id, file_id FROM jobs WHERE status='queued'").fetchall()
        for r in rows:
            c.execute(
                "UPDATE jobs SET status='canceled', finished_at=datetime('now','localtime') WHERE id=?",
                (r["id"],),
            )
            c.execute("UPDATE files SET status='idle' WHERE id=?", (r["file_id"],))
    return len(rows)


def mark_failed(job_id: int, error: str, canceled: bool = False) -> None:
    with tx() as c:
        job = c.execute("SELECT file_id FROM jobs WHERE id=?", (job_id,)).fetchone()
        if job is None:
            return
        status = "canceled" if canceled else "failed"
        c.execute(
            "UPDATE jobs SET status=?, error=?, finished_at=datetime('now','localtime') WHERE id=?",
            (status, str(error)[:2000], job_id),
        )
        c.execute(
            "UPDATE files SET status=?, error=?, updated_at=datetime('now','localtime') WHERE id=?",
            (
                "idle" if canceled else "failed",
                None if canceled else str(error)[:2000],
                job["file_id"],
            ),
        )


def reset_stuck_jobs() -> int:
    """进程重启后，把 running 的任务放回队列。"""
    with tx() as c:
        rows = c.execute("SELECT id, file_id FROM jobs WHERE status='running'").fetchall()
        for r in rows:
            c.execute(
                "UPDATE jobs SET status='queued', progress=0, stage=NULL WHERE id=?", (r["id"],)
            )
            c.execute("UPDATE files SET status='queued' WHERE id=?", (r["file_id"],))
    return len(rows)


def queue_stats() -> dict:
    conn = get_conn()
    rows = conn.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status").fetchall()
    counts = {r["status"]: r["n"] for r in rows}
    cur = conn.execute(
        "SELECT j.id job_id, j.file_id, j.progress, j.stage, f.name, f.duration, j.started_at "
        "FROM jobs j JOIN files f ON f.id=j.file_id WHERE j.status='running' ORDER BY j.id LIMIT 1"
    ).fetchone()
    total_wait = conn.execute(
        "SELECT COALESCE(SUM(COALESCE(f.duration, 0)),0) s FROM jobs j JOIN files f ON f.id=j.file_id "
        "WHERE j.status='queued'"
    ).fetchone()["s"]
    return {
        "queued": counts.get("queued", 0),
        "running": counts.get("running", 0),
        "done": counts.get("done", 0),
        "failed": counts.get("failed", 0),
        "canceled": counts.get("canceled", 0),
        "current": dict(cur) if cur else None,
        "queued_seconds": float(total_wait or 0),
        "rtf": None,
    }
