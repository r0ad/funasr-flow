"""HTTP 客户端：`funasr-flow api` 子命令的全部实现。

目标：让 LLM / agent 不懂 HTTP 细节也能直接 `funasr-flow api list --json` 拿到结构化数据。
只依赖标准库 urllib，不引入 requests / httpx；`mcp_server.py` 复用这里的请求与渲染函数。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

# Windows 控制台默认 GBK，中文输出会 UnicodeEncodeError；统一切到 utf-8（失败也不影响主流程）
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover - 输出被重定向成非 TextIOWrapper 时才会失败
    pass

DEFAULT_SERVER = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT = 60.0


class ApiError(RuntimeError):
    """服务端返回非 2xx，或网络层失败。message 优先是后端返回的 detail。"""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


# --------------------------------------------------------------------- 传输层
def build_url(server: str, path: str, query: dict[str, Any] | None = None) -> str:
    url = f"{server.rstrip('/')}{path}"
    if query:
        clean = {k: v for k, v in query.items() if v is not None and v != ""}
        if clean:
            url = f"{url}?{urllib.parse.urlencode(clean)}"
    return url


def request(
    server: str,
    method: str,
    path: str,
    *,
    query: dict[str, Any] | None = None,
    payload: Any = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[bytes, str]:
    """发一个请求，返回 (响应字节, Content-Type)。非 2xx 抛 ApiError。"""
    url = build_url(server, path, query)
    data: bytes | None = None
    headers = {"Accept": "application/json, text/plain, */*"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(), resp.headers.get("Content-Type") or ""
    except urllib.error.HTTPError as exc:
        detail = _detail_text(exc.read())
        exc.close()
        raise ApiError(detail, status=exc.code) from exc
    except urllib.error.URLError as exc:
        raise ApiError(f"无法连接 {server}：{exc.reason}") from exc
    except OSError as exc:
        raise ApiError(f"网络错误：{exc}") from exc


def _detail_text(body: bytes) -> str:
    """把 FastAPI 的错误响应压成一行，优先取 detail。"""
    text = body.decode("utf-8", "replace").strip()
    if not text:
        return "服务端没有返回错误详情"
    try:
        payload = json.loads(text)
    except ValueError:
        return text[:800]
    detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
    if isinstance(detail, str):
        return detail
    return json.dumps(detail, ensure_ascii=False)


def _decode(raw: bytes) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise ApiError(f"服务端返回的不是 JSON：{raw[:200]!r}") from exc


def get_json(server: str, path: str, query: dict[str, Any] | None = None) -> Any:
    raw, _ctype = request(server, "GET", path, query=query)
    return _decode(raw)


def post_json(
    server: str,
    path: str,
    payload: Any = None,
    *,
    query: dict[str, Any] | None = None,
) -> Any:
    raw, _ctype = request(server, "POST", path, query=query, payload=payload)
    return _decode(raw)


# ------------------------------------------------------------------ 接口封装
def fetch_files(
    server: str,
    *,
    status: str | None = None,
    q: str | None = None,
    limit: int = 20,
    offset: int = 0,
    sort: str = "updated",
    order: str = "desc",
) -> dict:
    return get_json(
        server,
        "/api/files",
        {
            "status": status,
            "q": q,
            "sort": sort,
            "order": order,
            "limit": int(limit),
            "offset": int(offset),
        },
    )


def fetch_file(server: str, file_id: int) -> dict:
    return get_json(server, f"/api/files/{int(file_id)}")


def fetch_transcript(server: str, file_id: int) -> dict:
    return get_json(server, f"/api/transcripts/{int(file_id)}")


def fetch_search(server: str, query: str, limit: int = 10) -> dict:
    return get_json(server, "/api/search", {"q": query, "limit": int(limit)})


def fetch_state(server: str) -> dict:
    return get_json(server, "/api/state")


def fetch_health(server: str) -> dict:
    return get_json(server, "/api/health")


def fetch_speakers(server: str) -> dict:
    return get_json(server, "/api/speakers")


def enqueue(server: str, ids: list[int], reset: bool = False) -> dict:
    action = "reset" if reset else "enqueue"
    return post_json(server, "/api/files/batch", {"ids": [int(i) for i in ids], "action": action})


def transcribe_all(server: str, redo: bool = False, dry_run: bool = False) -> dict:
    return post_json(
        server, "/api/files/transcribe-all", {"redo": bool(redo), "dry_run": bool(dry_run)}
    )


def render_transcribe_all(data: dict) -> str:
    hours = float(data.get("seconds") or 0) / 3600
    eta = float(data.get("eta_seconds") or 0)
    if data.get("dry_run"):
        return (
            f"待转写 {data.get('candidates', 0)} 条，共 {hours:.1f} 小时音频，"
            f"预计约 {eta / 3600:.1f} 小时（加 --apply 真正入队）"
        )
    return (
        f"已加入队列 {data.get('queued', 0)} 条"
        f"（候选 {data.get('candidates', 0)} 条，跳过 {data.get('skipped', 0)} 条）"
    )


def _cmd_transcribe_all(args: argparse.Namespace, server: str) -> int:
    data = transcribe_all(server, redo=args.redo, dry_run=not args.apply)
    data["dry_run"] = not args.apply
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(render_transcribe_all(data))
    return 0


def cancel(server: str, mode: str = "queued") -> dict:
    return post_json(server, "/api/queue/cancel", query={"mode": mode})


def scan(server: str, directory: str | None = None) -> dict:
    return post_json(server, "/api/files/scan", {"dir": directory} if directory else {})


def fetch_export(server: str, ids: list[int], fmt: str) -> tuple[bytes, str]:
    return request(
        server,
        "GET",
        "/api/export",
        query={"ids": ",".join(str(int(i)) for i in ids), "format": fmt},
    )


def resolve_server(args: Any) -> str:
    """--server > FLOW_SERVER > 默认 127.0.0.1:8000。"""
    value = getattr(args, "server", None)
    return (value or os.environ.get("FLOW_SERVER") or DEFAULT_SERVER).rstrip("/")


# -------------------------------------------------------------------- 格式化
def fmt_clock(ms: Any) -> str:
    """毫秒 → mm:ss，超过 1 小时用 h:mm:ss。"""
    total = max(int(float(ms or 0) // 1000), 0)
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def fmt_seconds(seconds: Any) -> str:
    """秒 → mm:ss / h:mm:ss（时长字段用）。"""
    return fmt_clock(float(seconds or 0) * 1000)


def srt_clock(ms: Any) -> str:
    """毫秒 → SRT 时间轴 HH:MM:SS,mmm。"""
    total = max(int(round(float(ms or 0))), 0)
    hours, rest = divmod(total, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def fmt_size(size: Any) -> str:
    value = float(size or 0)
    if value < 1024:
        return f"{int(value)} B"
    for unit in ("KB", "MB", "GB"):
        value /= 1024
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
    return f"{value:.1f} GB"


def speaker_names(data: dict) -> dict[str, str]:
    """cluster → 展示名；没有转写结果时返回空表，调用方回落到 cluster。"""
    names: dict[str, str] = {}
    for item in data.get("speakers") or []:
        cluster = item.get("cluster")
        if cluster:
            names[str(cluster)] = item.get("name") or str(cluster)
    return names


def render_files(data: dict) -> str:
    items = data.get("items") or []
    total = int(data.get("total") or len(items))
    if not items:
        return f"共 {total} 个文件，当前筛选下没有匹配项。"
    lines = [f"共 {total} 个文件，显示 {len(items)} 个："]
    for item in items:
        status = item.get("status") or "?"
        parts = [
            f"#{item.get('id')}",
            str(item.get("name") or "?"),
            fmt_seconds(item.get("duration")),
            str(status),
        ]
        if item.get("has_transcript"):
            parts.append(f"{item.get('segment_count', 0)} 句")
        if item.get("speakers"):
            parts.append(f"{len(item['speakers'])} 人")
        if status == "failed" and item.get("error"):
            parts.append(f"错误：{item['error']}")
        lines.append("  ".join(parts))
    return "\n".join(lines)


def render_file(data: dict) -> str:
    lines = [f"#{data.get('id')}  {data.get('name')}"]
    lines.append(
        f"  状态 {data.get('status')} · 时长 {fmt_seconds(data.get('duration'))}"
        f" · 大小 {fmt_size(data.get('size'))} · 更新 {data.get('updated_at') or '-'}"
    )
    lines.append(f"  路径 {data.get('rel_path') or data.get('name')}")
    if data.get("has_transcript"):
        count = data.get("segment_count")
        detail = f"{count} 句 · " if count is not None else ""
        lines.append(f"  转写 {detail}已完成 · 模型 {data.get('model') or '-'}")
    else:
        lines.append("  转写 未完成")
    if data.get("error"):
        lines.append(f"  错误 {data['error']}")
    for speaker in data.get("speakers") or []:
        lines.append(
            f"  说话人 {speaker.get('name') or speaker.get('cluster')}"
            f" {fmt_seconds(speaker.get('seconds'))} · {speaker.get('utt_count', 0)} 句"
        )
    return "\n".join(lines)


def render_transcript(data: dict) -> str:
    file_info = data.get("file") or {}
    transcript = data.get("transcript")
    segments = data.get("segments") or []
    speakers = data.get("speakers") or []
    head = f"#{file_info.get('id')}  {file_info.get('name')}"
    if not transcript:
        return f"{head}\n  还没有转写结果。"
    lines = [
        head,
        f"  时长 {fmt_seconds(transcript.get('duration'))} · 模型 {transcript.get('model') or '-'}"
        f" · {len(segments)} 句 · {len(speakers)} 位说话人",
        "",
    ]
    names = speaker_names(data)
    for segment in segments:
        who = names.get(str(segment.get("speaker")), segment.get("speaker") or "?")
        lines.append(f"[{fmt_clock(segment.get('start_ms'))}] {who}：{segment.get('text') or ''}")
    return "\n".join(lines)


def render_srt(data: dict) -> str:
    segments = data.get("segments") or []
    if not segments:
        return ""
    names = speaker_names(data)
    lines: list[str] = []
    for index, segment in enumerate(segments, 1):
        who = names.get(str(segment.get("speaker")), segment.get("speaker") or "?")
        lines.append(str(index))
        lines.append(f"{srt_clock(segment.get('start_ms'))} --> {srt_clock(segment.get('end_ms'))}")
        lines.append(f"{who}：{segment.get('text') or ''}")
        lines.append("")
    return "\n".join(lines)


def render_search(data: dict, query: str) -> str:
    items = data.get("items") or []
    if not items:
        return f"没有找到包含「{query}」的句子。"
    lines = [f"找到 {len(items)} 条包含「{query}」的句子："]
    for item in items:
        lines.append(
            f"#{item.get('file_id')}  {item.get('name')}  [{fmt_clock(item.get('start_ms'))}]"
            f" {item.get('cluster') or '?'}：{item.get('text') or ''}"
        )
    return "\n".join(lines)


def render_state(data: dict) -> str:
    queue = data.get("queue") or {}
    files = data.get("files") or {}
    lines = [
        f"队列：排队 {queue.get('queued', 0)} · 运行 {queue.get('running', 0)}"
        f" · 完成 {queue.get('done', 0)} · 失败 {queue.get('failed', 0)}"
        f" · 取消 {queue.get('canceled', 0)}"
        f"（待转写音频 {fmt_seconds(queue.get('queued_seconds'))}）",
        "文件：" + (" · ".join(f"{key} {value}" for key, value in files.items()) or "无"),
    ]
    active = data.get("active")
    if active:
        progress = float(active.get("progress") or 0)
        progress = min(max(progress, 0.0), 1.0)
        lines.append(
            f"当前：#{active.get('file_id')} {active.get('name')}"
            f" · {active.get('stage')} · {progress:.0%}"
        )
    else:
        lines.append("当前：没有正在运行的任务")
    age = data.get("worker_age")
    lines.append("工作进程：未启动" if age is None else f"工作进程：心跳 {float(age):.1f} 秒前")
    return "\n".join(lines)


def render_speakers(data: dict) -> str:
    items = data.get("items") or []
    if not items:
        return "声纹库还是空的。"
    lines = [f"声纹库共 {len(items)} 人："]
    for item in items:
        embedding = "已登记声纹" if item.get("has_embedding") else "暂无声纹"
        notes = f" · {item['notes']}" if item.get("notes") else ""
        lines.append(
            f"#{item.get('id')}  {item.get('name')}  {embedding}"
            f" · 关联 {item.get('file_count', 0)} 个文件{notes}"
        )
    return "\n".join(lines)


def render_health(data: dict) -> str:
    gpu = data.get("gpu") or {}
    worker = data.get("worker") or {}
    engines = data.get("engines") or []
    models = (data.get("ml") or {}).get("models") or {}
    gpu_name = (gpu.get("name") or "未知型号") if gpu.get("available") else "不可用"
    if gpu.get("available") and gpu.get("vram_mb"):
        gpu_name = f"{gpu_name}（{gpu['vram_mb']} MB）"
    lines = [
        f"服务状态：{'正常' if data.get('ok') else '异常'}（根目录 {data.get('root') or '-'}）",
        f"依赖：ffmpeg {'可用' if data.get('ffmpeg') else '缺失'}"
        f" · funasr {'可用' if data.get('funasr') else '缺失'}"
        f" · torch {'可用' if data.get('torch') else '缺失'}",
        f"GPU：{gpu_name}",
        "引擎："
        + (
            " · ".join(
                f"{engine.get('id')} {'可用' if engine.get('available') else '不可用'}"
                for engine in engines
            )
            or "无"
        ),
        "模型："
        + (" · ".join(f"{kind} {len(items)} 个" for kind, items in models.items()) or "无"),
    ]
    if worker.get("thread"):
        age = worker.get("heartbeat_age")
        lines.append("工作线程：运行中" if age is None else f"工作线程：运行中（心跳 {age} 秒前）")
    else:
        lines.append("工作线程：未运行")
    return "\n".join(lines)


def render_scan(result: dict) -> str:
    root = result.get("root") or ""
    prefix = f"扫描 {root} 完成" if root else "扫描完成"
    return (
        f"{prefix}：新增 {result.get('added', 0)} · 更新 {result.get('updated', 0)}"
        f" · 缺失 {result.get('missing', 0)} · 共 {result.get('total', 0)} 个"
    )


def render_enqueue(result: dict, reset: bool = False) -> str:
    if reset and "reset" in result:
        return f"已重置 {result.get('reset', 0)} 个文件的转写结果，并重新加入队列。"
    return (
        f"已加入队列 {result.get('queued', 0)} 个文件，跳过 {result.get('skipped', 0)} 个"
        "（已在队列/运行中或不存在）。"
    )


def render_cancel(result: dict) -> str:
    return (
        f"已取消排队 {result.get('canceled', 0)} 个；"
        f"请求中断正在运行的任务 {result.get('interrupted', 0)} 个。"
    )


def _emit(as_json: bool, data: Any, render: Callable[[dict], str]) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(render(data))


# ------------------------------------------------------------------ 子命令实现
def _cmd_list(args: argparse.Namespace, server: str) -> int:
    data = fetch_files(server, status=args.status, q=args.q, limit=args.limit)
    _emit(args.json, data, render_files)
    return 0


def _cmd_show(args: argparse.Namespace, server: str) -> int:
    data = fetch_file(server, args.file_id)
    _emit(args.json, data, render_file)
    return 0


def _cmd_transcript(args: argparse.Namespace, server: str) -> int:
    data = fetch_transcript(server, args.file_id)
    _emit(args.json, data, render_transcript)
    return 0


def _cmd_search(args: argparse.Namespace, server: str) -> int:
    if not args.query.strip():
        print("[error] 搜索关键词不能为空", file=sys.stderr)
        return 1
    data = fetch_search(server, args.query, limit=args.limit)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(render_search(data, args.query))
    return 0


def _cmd_state(args: argparse.Namespace, server: str) -> int:
    data = fetch_state(server)
    _emit(args.json, data, render_state)
    return 0


def _cmd_enqueue(args: argparse.Namespace, server: str) -> int:
    data = enqueue(server, args.ids, reset=args.reset)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(render_enqueue(data, reset=args.reset))
    return 0


def _cmd_cancel(args: argparse.Namespace, server: str) -> int:
    data = cancel(server, mode=args.mode)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(render_cancel(data))
    return 0


def _cmd_scan(args: argparse.Namespace, server: str) -> int:
    data = scan(server, args.dir)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(render_scan(data))
    return 0


def _cmd_speakers(args: argparse.Namespace, server: str) -> int:
    data = fetch_speakers(server)
    _emit(args.json, data, render_speakers)
    return 0


def _adjust_suffix(out: Path, fmt: str, is_zip: bool) -> Path:
    suffix = out.suffix.lower()
    if is_zip:
        if suffix == ".zip":
            return out
        if suffix in {".txt", ".md", ".json", ".srt"}:
            return out.with_suffix(".zip")
        return out.with_name(out.name + ".zip")
    if not suffix:
        return out.with_suffix(f".{fmt}")
    return out


def _cmd_export(args: argparse.Namespace, server: str) -> int:
    blob, ctype = fetch_export(server, args.ids, args.format)
    is_zip = "zip" in (ctype or "").lower() or blob[:2] == b"PK"
    default_name = f"funasr-flow-export-{len(args.ids)}.{args.format}"
    out = _adjust_suffix(
        Path(args.output) if args.output else Path(default_name), args.format, is_zip
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(blob)
    kind = "zip 压缩包" if is_zip else args.format.upper()
    print(f"已导出 {len(args.ids)} 个文件（{kind}）→ {out}（{fmt_size(len(blob))}）")
    return 0


def _cmd_health(args: argparse.Namespace, server: str) -> int:
    data = fetch_health(server)
    _emit(args.json, data, render_health)
    return 0


def run(args: argparse.Namespace) -> int:
    """`api` 子命令入口：统一处理 ApiError → stderr + 退出码 1。"""
    server = resolve_server(args)
    try:
        return int(args.handler(args, server))
    except ApiError as exc:
        prefix = f"HTTP {exc.status}：" if exc.status else ""
        print(f"[error] {prefix}{exc.message}", file=sys.stderr)
        return 1


# -------------------------------------------------------------------- 注册
def common_options(suppress: bool = False) -> argparse.ArgumentParser:
    """--server / --json。

    suppress=True 用于子命令：默认值设成 SUPPRESS，这样写在子命令里的选项
    不会覆盖写在子命令前的同名全局选项。
    """
    parser = argparse.ArgumentParser(add_help=False)
    default = argparse.SUPPRESS if suppress else None
    parser.add_argument(
        "--server",
        metavar="URL",
        default=default,
        help="服务地址（默认读环境变量 FLOW_SERVER，否则 http://127.0.0.1:8000）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=default,
        help="输出原始 JSON，便于程序/agent 解析（默认输出紧凑文本）",
    )
    return parser


def register(subparsers: Any) -> None:
    """把 `api` 子命令挂到顶层 subparsers（在 serve/scan/worker/doctor 之后）。"""
    parser = subparsers.add_parser(
        "api",
        parents=[common_options(suppress=True)],
        help="以 HTTP 客户端方式调用正在运行的服务（agent 友好）",
        description="调用已运行的 FunASR Flow 服务，不需要了解 HTTP 细节。",
    )
    parser.set_defaults(func=run)
    actions = parser.add_subparsers(dest="action", metavar="<命令>")
    actions.required = True

    def add(name: str, help_text: str, handler: Callable[..., int]) -> argparse.ArgumentParser:
        sub = actions.add_parser(name, parents=[common_options(suppress=True)], help=help_text)
        sub.set_defaults(handler=handler)
        return sub

    sub = add("list", "列出录音文件", _cmd_list)
    sub.add_argument("--status", default=None, help="按状态过滤，可逗号分隔：done,idle")
    sub.add_argument("--q", default=None, help="按文件名模糊过滤")
    sub.add_argument("--limit", type=int, default=20, help="返回条数（默认 20）")

    sub = add("show", "查看单个文件详情", _cmd_show)
    sub.add_argument("file_id", type=int, help="文件 ID")

    sub = add("transcript", "打印转写文本（[mm:ss] 说话人：文本）", _cmd_transcript)
    sub.add_argument("file_id", type=int, help="文件 ID")

    sub = add("search", "在全部转写里搜索关键词", _cmd_search)
    sub.add_argument("query", help="关键词")
    sub.add_argument("--limit", type=int, default=10, help="返回条数（默认 10）")

    add("state", "查看队列与进度", _cmd_state)

    sub = add("enqueue", "把文件加入转写队列", _cmd_enqueue)
    sub.add_argument("ids", type=int, nargs="+", help="文件 ID，可多个")
    sub.add_argument("--reset", action="store_true", help="先清掉旧转写结果再重跑")

    sub = add("transcribe-all", "把所有没转写的录音一次入队", _cmd_transcribe_all)
    sub.add_argument("--redo", action="store_true", help="连已完成的也重新转写")
    sub.add_argument(
        "--apply",
        action="store_true",
        help="真正入队；不加时只估算条数与耗时（防止误触发长任务）",
    )

    sub = add("cancel", "取消排队任务", _cmd_cancel)
    sub.add_argument(
        "--mode",
        choices=("queued", "all"),
        default="queued",
        help="queued 只取消排队；all 连正在运行的也请求中断",
    )

    sub = add("scan", "扫描录音目录", _cmd_scan)
    sub.add_argument("--dir", default=None, help="要扫描的目录（默认用服务端设置）")

    add("speakers", "查看声纹库", _cmd_speakers)

    sub = add("export", "导出转写文本（多文件自动打包 zip）", _cmd_export)
    sub.add_argument("ids", type=int, nargs="+", help="文件 ID，可多个")
    sub.add_argument(
        "-f",
        "--format",
        choices=("txt", "md", "srt", "json"),
        default="txt",
        help="导出格式（默认 txt）",
    )
    sub.add_argument(
        "-o", "--output", default=None, help="输出路径；多文件会按 Content-Type 存成 .zip"
    )

    add("health", "服务健康检查", _cmd_health)
