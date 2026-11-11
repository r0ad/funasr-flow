"""FastAPI 应用入口：挂 API、托管前端构建产物、拉起后台 worker。"""

from __future__ import annotations

import logging
import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import worker as worker_mod
from .api import files, speakers, system, transcripts
from .config import FRONTEND_DIST, ROOT, ensure_dirs
from .db import init_db

log = logging.getLogger("server")
_worker_thread: threading.Thread | None = None


def _start_worker() -> None:
    global _worker_thread
    if os.environ.get("FLOW_WORKER", "1") == "0":
        log.info("FLOW_WORKER=0，不启动内置 worker")
        return
    if _worker_thread and _worker_thread.is_alive():
        return
    _worker_thread = threading.Thread(
        target=worker_mod.run_forever, name="flow-worker", daemon=True
    )
    _worker_thread.start()
    system.set_worker_thread(_worker_thread)
    log.info("内置 worker 已启动（线程）")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=os.environ.get("FLOW_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    ensure_dirs()
    init_db()
    # 补齐未知时长，让列表能显示时长（后台跑，不阻塞启动）
    from .config import LIBRARY_DIR
    from .services.scanner import start_auto_scan, start_duration_probe, stop_auto_scan

    start_duration_probe(str(LIBRARY_DIR))
    start_auto_scan()
    _start_worker()
    yield
    stop_auto_scan()
    worker_mod.stop()
    if _worker_thread:
        _worker_thread.join(timeout=10)


app = FastAPI(
    title="FunASR Flow",
    description="本地通话录音批量转写与说话人分离",
    version="0.1.0",
    lifespan=lifespan,
)

# 开发态 Vite 直连后端；生产同源部署也允许
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (files.router, transcripts.router, speakers.router, system.router):
    app.include_router(r)


@app.get("/api/version")
def version() -> dict:
    return {"name": "funasr-flow", "version": app.version, "root": str(ROOT)}


@app.get("/llms.txt", include_in_schema=False)
def llms_txt():
    """给 LLM/agent 的项目索引（llms.txt 约定），与仓库根目录同一份文件。"""
    from fastapi.responses import PlainTextResponse

    path = ROOT / "llms.txt"
    if path.exists():
        return PlainTextResponse(
            path.read_text(encoding="utf-8"), media_type="text/plain; charset=utf-8"
        )
    return PlainTextResponse(
        "# FunASR Flow\n\n"
        "本机通话录音批量转写服务。API 见 /openapi.json（交互文档 /docs）。\n"
        "健康检查：GET /api/health\n状态：GET /api/state\n文件列表：GET /api/files\n"
        "转写结果：GET /api/transcripts/{file_id}\n全文搜索：GET /api/search?q=\n"
        '入队：POST /api/files/batch {"ids":[...],"action":"enqueue"}\n'
        "导出：GET /api/export?ids=1,2&format=txt|md|srt|json\n"
        "CLI：funasr-flow api <list|show|transcript|search|state|enqueue|export> --json\n"
        "MCP：funasr-flow mcp\n",
        media_type="text/plain; charset=utf-8",
    )


@app.exception_handler(404)
async def spa_fallback(request: Request, exc):
    """前端是单页应用：非 /api 的 404 一律回 index.html，交给 vue-router。"""
    if request.url.path.startswith("/api"):
        return JSONResponse({"detail": getattr(exc, "detail", "Not Found")}, status_code=404)
    index = FRONTEND_DIST / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse(
        {
            "detail": "前端尚未构建。开发模式请访问 http://localhost:5173 ，"
            "或运行 `cd frontend && npm run build`。"
        },
        status_code=404,
    )


def _mount_static() -> None:
    assets = FRONTEND_DIST / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")
    if FRONTEND_DIST.exists():
        app.mount("/static", StaticFiles(directory=str(FRONTEND_DIST)), name="static")


_mount_static()
