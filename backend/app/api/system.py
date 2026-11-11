"""系统 API：健康检查、设置、队列状态、统计、SSE 事件流。"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..config import CACHE_DIR, DEFAULT_SETTINGS, MODEL_CHOICES, ROOT
from ..db import all_settings, get_conn, get_metric, set_setting
from ..services import queue as q
from ..services.scanner import AUDIO_EXTS

router = APIRouter(prefix="/api", tags=["system"])

_worker_alive = {"v": None}


def set_worker_thread(t: threading.Thread | None) -> None:
    _worker_alive["v"] = t


class SettingsPatch(BaseModel):
    values: dict[str, Any]


# ------------------------------------------------------------------ 容器探测
# 容器里只能看到「挂载进来」的目录，界面上要把这件事讲清楚，别让用户以为程序坏了。
CONTAINER_MOUNT_ROOTS = ("/media", "/mnt", "/recordings", "/audio", "/data")


def in_container() -> bool:
    """判断是不是跑在 Docker 里（/.dockerenv 在官方镜像里一定存在）。"""
    if os.environ.get("FLOW_IN_DOCKER") == "1":
        return True
    return Path("/.dockerenv").exists()


def container_mounts() -> list[dict]:
    """容器里已经挂进来的媒体目录（约定挂到 /media、/mnt 之类，整盘挂到 /host/<盘符>）。"""
    if not in_container():
        return []
    out: list[dict] = []
    for base in CONTAINER_MOUNT_ROOTS:
        p = Path(base)
        try:
            if p.is_dir():
                out.append({"name": base, "path": base, "audio": _count_audio(p)})
        except OSError:
            continue
    # 整盘挂载约定：宿主机 D:\ → 容器 /host/d
    host_root = Path("/host")
    try:
        if host_root.is_dir():
            for p in sorted(host_root.iterdir()):
                if p.is_dir():
                    out.append({"name": str(p), "path": str(p), "audio": _count_audio(p)})
    except OSError:
        pass
    return out


# ------------------------------------------------------------------ 目录浏览
def _roots() -> list[dict]:
    """可选的起点：Windows 给盘符，其他系统给 / 和家目录。"""
    out: list[dict] = []
    if os.name == "nt":
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZAB":
            drive = f"{letter}:\\"
            if os.path.isdir(drive):
                out.append({"name": drive.rstrip("\\"), "path": drive})
    else:
        out.append({"name": "/", "path": "/"})
        home = str(Path.home())
        if home != "/":
            out.append({"name": "~", "path": home})
    return out


def _count_audio(path: Path, limit: int = 20000) -> int:
    """数当前目录里（不递归）有多少个音频文件，最多数 limit 个条目。"""
    n = 0
    try:
        with os.scandir(path) as it:
            for i, entry in enumerate(it):
                if i >= limit:
                    break
                try:
                    if entry.is_file() and Path(entry.name).suffix.lower() in AUDIO_EXTS:
                        n += 1
                except OSError:
                    continue
    except OSError:
        return 0
    return n


@router.get("/browse")
def browse(path: str | None = None) -> dict:
    """列出目录，供界面里的「选择录音目录」用（本机工具，默认不限制范围）。"""
    roots = _roots()
    mounts = container_mounts()
    # 容器里最该点的就是挂载点，把它们放到最前面
    if mounts:
        roots = [{"name": m["path"], "path": m["path"]} for m in mounts] + roots
    raw = path or str(get_setting_safe("input_dir") or "")
    target = Path(raw).expanduser() if raw else None
    if target is None or not target.is_dir():
        # 回退：路径无效时给第一个可访问的根
        for r in roots:
            if Path(r["path"]).is_dir():
                target = Path(r["path"])
                break
    if target is None or not target.is_dir():
        raise HTTPException(400, f"目录不存在: {raw}")

    try:
        target = target.resolve()
    except OSError:
        pass

    entries: list[dict] = []
    try:
        with os.scandir(target) as it:
            for entry in it:
                try:
                    if not entry.is_dir():
                        continue
                except OSError:
                    continue
                # 跳过隐藏目录和 Windows 系统目录（挂整盘时否则满屏 $RECYCLE.BIN）
                if entry.name.startswith(".") or entry.name.startswith("$"):
                    continue
                if entry.name in {"System Volume Information", "Recovery", "Config.Msi"}:
                    continue
                child = Path(entry.path)
                entries.append(
                    {
                        "name": entry.name,
                        "path": str(child),
                        "audio": _count_audio(child),
                    }
                )
    except PermissionError as exc:
        raise HTTPException(403, f"没有权限读取: {target}") from exc
    except OSError as exc:
        raise HTTPException(400, f"读取失败: {exc}") from exc

    # 有录音的子目录排前面，其次按名字
    entries.sort(key=lambda e: (-e["audio"], e["name"].lower()))
    try:
        parent = str(target.parent) if target.parent != target else None
    except OSError:
        parent = None

    container = in_container()
    return {
        "path": str(target),
        "parent": parent,
        "sep": os.sep,
        "roots": roots,
        "entries": entries,
        "audio": _count_audio(target),
        "container": container,
        "mounts": mounts,
        "image": os.environ.get("FLOW_IMAGE") or "sunzroad/funasr-flow:latest",
        "hint": (
            "容器里只能浏览已经挂载进来的目录（"
            + "、".join(m["path"] for m in mounts)
            + "）。要加别的宿主机目录，需要带 -v 参数重新启动容器。"
            if container
            else ""
        ),
    }


def get_setting_safe(key: str) -> Any:
    try:
        return all_settings().get(key)
    except Exception:  # noqa: BLE001
        return None


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
        "container": in_container(),
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
        """SELECT COUNT(*) files,
                  COALESCE(SUM(duration),0) total_seconds,
                  COALESCE(SUM(CASE WHEN status='done' THEN 1 ELSE 0 END),0) done_files,
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
        "done_files": row["done_files"],
        "total_seconds": round(row["total_seconds"], 1),
        "done_seconds": round(row["done_seconds"], 1),
        "segments": seg["n"],
        "speakers": spk["n"],
        "speech_hours": round(hours["h"], 2),
        "rtf_est": round(rtf, 4),
        "eta_seconds": round(max(row["total_seconds"] - row["done_seconds"], 0) * (rtf or 0.1), 0),
    }
