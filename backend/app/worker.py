"""后台 worker：串行消费任务队列（GPU 只有一个，必须串行）。

启动方式：
    python -m backend.app.worker           # 前台运行，便于看日志
通常由 `run.py` / `funasr-flow serve` 自动拉起子进程。
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time

from .db import init_db, set_metric
from .services import queue as q
from .services.pipeline import Canceled, process_job

log = logging.getLogger("worker")
_stop = False


def _handle_signal(signum, frame):  # pragma: no cover - 信号处理
    global _stop
    log.info("收到信号 %s，处理完当前任务后退出", signum)
    _stop = True


def stop() -> None:
    """给同进程内的 Web 服务用：请求 worker 处理完当前任务后退出。"""
    global _stop
    _stop = True


def run_forever(poll_interval: float = 1.0) -> None:
    global _stop
    init_db()
    n = q.reset_stuck_jobs()
    if n:
        log.info("恢复 %d 个中断任务", n)
    log.info("worker 启动，等待任务…")
    while not _stop:
        item = q.claim_next()
        if item is None:
            set_metric("worker_heartbeat", time.time())
            time.sleep(poll_interval)
            continue
        job_id = item["job_id"]
        log.info("开始任务 #%s (file=%s)", job_id, item["file_id"])
        t0 = time.time()
        try:
            process_job(job_id, should_cancel=lambda jid=job_id: q.is_cancel_requested(jid))
            log.info("任务 #%s 完成，用时 %.1fs", job_id, time.time() - t0)
        except Canceled:
            q.mark_failed(job_id, "用户已取消", canceled=True)
            log.info("任务 #%s 已取消", job_id)
        except Exception as e:  # noqa: BLE001 - 任何异常都要落到数据库
            log.exception("任务 #%s 失败", job_id)
            # 我们自己抛的 RuntimeError 文案已经是给人看的（如"没有检测到语音…"），
            # 别把 "RuntimeError: " 前缀塞进界面；其它异常保留类型方便排查。
            msg = str(e) if isinstance(e, RuntimeError) else f"{type(e).__name__}: {e}"
            q.mark_failed(job_id, msg)
        finally:
            set_metric("worker_heartbeat", time.time())
    log.info("worker 退出")


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("FLOW_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)
    run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
