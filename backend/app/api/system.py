"""系统 API：健康检查、设置、队列状态、统计、SSE 事件流。"""

from __future__ import annotations

import asyncio
import json
import shutil
import threading
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..config import CACHE_DIR, DEFAULT_SETTINGS, MODEL_CHOICES, ROOT
from ..db import all_settings, get_conn, get_metric, set_setting
from ..services import queue as q

router = APIRouter(prefix="/api", tags=["system"])

_worker_alive = {"v": None}


def set_worker_thread(t: threading.Thread | None) -> None:
    _worker_alive["v"] = t


class SettingsPatch(BaseModel):
    values: dict[str, Any]


# ------------------------------------------------------------------ 健康
@router.get("/health")
def health() -> dict:
    import importlib.util

    def has_dep(name: str) -> bool:
        return importlib.util.find_spec(name) is not None

    gpu = {"available": False, "name": None, "vram_mb": None}
    try:
        import torch

        if torch.cuda.is_available():
            gpu = {
                "available": True,
                "name": torch.cuda.get_device_name(0),
                "vram_mb": int(torch.cuda.get_device_properties(0).total_memory / 1024 / 1024),
            }
    except Exception:
        pass

    from ..engines.registry import list_engines, load_builtin

    load_builtin()
    engines = list_engines()

    t = _worker_alive["v"]
    beat = get_metric("worker_heartbeat", 0.0)
    return {
        "ok": True,
        "root": str(ROOT),
        "cache_dir": str(CACHE_DIR),
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "python": has_dep("fastapi"),
        "funasr": has_dep("funasr"),
        "torch": has_dep("torch"),
        "gpu": gpu,
        "engines": engines,
        "worker": {
            "thread": bool(t and t.is_alive()),
            "heartbeat_age": round(time.time() - beat, 1) if beat else None,
        },
        "ml": {"models": MODEL_CHOICES},
    }


# ------------------------------------------------------------------ 设置
@router.get("/settings")
def get_settings() -> dict:
    return {"values": all_settings(), "defaults": DEFAULT_SETTINGS, "models": MODEL_CHOICES}


@router.patch("/settings")
def patch_settings(body: SettingsPatch) -> dict:
    for k, v in body.values.items():
        if k not in DEFAULT_SETTINGS:
            raise HTTPException(400, f"未知配置项: {k}")
        base = DEFAULT_SETTINGS[k]
        try:
            if isinstance(base, bool):
                v = bool(v)
            elif isinstance(base, int):
                v = int(v)
            elif isinstance(base, float):
                v = float(v)
            elif isinstance(base, str):
                v = str(v)
        except (TypeError, ValueError):
            raise HTTPException(400, f"配置项 {k} 类型错误") from None
        set_setting(k, v)
    return {"values": all_settings()}


# ------------------------------------------------------------------ 队列
def snapshot() -> dict:
    conn = get_conn()
    rows = conn.execute("SELECT status, COUNT(*) n FROM files GROUP BY status").fetchall()
    by_status = {r["status"]: r["n"] for r in rows}
    active = conn.execute(
        """SELECT j.id, j.file_id, j.stage, j.progress, j.started_at, f.name, f.duration
           FROM jobs j JOIN files f ON f.id=j.file_id
           WHERE j.status='running' ORDER BY j.id LIMIT 1"""
    ).fetchone()
    return {
        "queue": q.queue_stats(),
        "files": by_status,
        "active": dict(active) if active else None,
        "worker_age": round(time.time() - get_metric("worker_heartbeat", 0.0), 1)
        if get_metric("worker_heartbeat", 0.0)
        else None,
        "ts": int(time.time()),
    }


@router.get("/state")
def state() -> dict:
    return snapshot()


@router.get("/events")
async def events() -> StreamingResponse:
    """SSE：状态有变化才推，客户端 EventSource 直接消费。"""

    async def gen():
        last = ""
        idle = 0
        while True:
            try:
                data = json.dumps(snapshot(), ensure_ascii=False)
            except Exception as e:  # 数据库短暂锁冲突不该断开连接
                data = json.dumps({"error": str(e)})
            if data != last:
                yield f"data: {data}\n\n"
                last = data
                idle = 0
            else:
                idle += 1
                if idle % 15 == 0:
                    yield ": keepalive\n\n"
            await asyncio.sleep(0.7)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/queue/cancel")
def cancel_queue(mode: str = "queued") -> dict:
    """mode=queued 只取消排队；mode=all 连当前运行的也请求中断。"""
    n = q.cancel_queued()
    interrupted = 0
    if mode == "all":
        running = get_conn().execute("SELECT id FROM jobs WHERE status='running'").fetchall()
        for r in running:
            q.request_cancel(int(r["id"]))
            interrupted += 1
    return {"canceled": n, "interrupted": interrupted}


@router.post("/queue/clear")
def clear_finished() -> dict:
    """清掉已结束的任务记录，保持队列界面干净。"""
    conn = get_conn()
    with conn:
        cur = conn.execute("DELETE FROM jobs WHERE status IN ('done','failed','canceled')")
    return {"deleted": cur.rowcount}


# ------------------------------------------------------------------ 统计
@router.get("/stats")
def stats() -> dict:
    conn = get_conn()
    row = conn.execute(
        """SELECT COUNT(*) files, COALESCE(SUM(duration),0) total_seconds,
                  COALESCE(SUM(CASE WHEN status='done' THEN duration ELSE 0 END),0) done_seconds
           FROM files"""
    ).fetchone()
    seg = conn.execute("SELECT COUNT(*) n FROM segments").fetchone()
    spk = conn.execute("SELECT COUNT(*) n FROM speakers").fetchone()
    hours = conn.execute(
        "SELECT COALESCE(SUM(MAX(end_ms-start_ms,0)),0)/3600000.0 h FROM segments"
    ).fetchone()
    rtf = get_metric("rtf_est", 0.0)
    return {
        "files": row["files"],
        "total_seconds": round(row["total_seconds"], 1),
        "done_seconds": round(row["done_seconds"], 1),
        "segments": seg["n"],
        "speakers": spk["n"],
        "speech_hours": round(hours["h"], 2),
        "rtf_est": round(rtf, 4),
        "eta_seconds": round(max(row["total_seconds"] - row["done_seconds"], 0) * (rtf or 0.1), 0),
    }
