"""MCP server（stdio / JSON-RPC 2.0）：把 FunASR Flow 的接口暴露给 LLM 客户端。

启动：`funasr-flow mcp --server http://127.0.0.1:8000`

约定：
- stdin 逐行读 JSON-RPC 消息，stdout 逐行写响应；**stdout 上除 JSON 外不输出任何内容**，日志全部走 stderr。
- 没有 `id` 的消息是通知，不产生响应（如 `notifications/initialized`）。
- 只依赖标准库；底层请求复用 `cli_client`，不另写一套 HTTP。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from . import cli_client
from .cli_client import ApiError

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "funasr-flow", "version": "0.1.0"}

log = logging.getLogger("funasr_flow.mcp")


class ToolError(RuntimeError):
    """工具参数不合法，或工具执行失败（会以 isError=true 返回给客户端）。"""


class RpcError(Exception):
    """JSON-RPC 协议层错误（未知方法 / 非法参数 / 未知工具）。"""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ------------------------------------------------------------------ 工具定义
def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_files",
        "description": "列出录音文件（可按状态、文件名过滤）。返回 id、名称、时长、状态、句数、说话人。",
        "inputSchema": _schema(
            {
                "status": {
                    "type": "string",
                    "description": "状态过滤，可逗号分隔：idle,queued,running,done,failed,canceled",
                },
                "q": {"type": "string", "description": "按文件名模糊过滤"},
                "limit": {
                    "type": "integer",
                    "description": "返回条数，默认 50",
                    "minimum": 1,
                    "maximum": 500,
                },
            }
        ),
    },
    {
        "name": "get_file",
        "description": "查看单个文件的详情：状态、时长、大小、模型、说话人统计。",
        "inputSchema": _schema(
            {"file_id": {"type": "integer", "description": "文件 ID"}},
            ["file_id"],
        ),
    },
    {
        "name": "get_transcript",
        "description": (
            "读取某个文件的转写结果。format=text 输出 [mm:ss] 说话人：文本；"
            "format=srt 输出字幕；format=json 输出原始结构（含每句时间轴）。"
        ),
        "inputSchema": _schema(
            {
                "file_id": {"type": "integer", "description": "文件 ID"},
                "format": {
                    "type": "string",
                    "description": "输出格式，默认 text",
                    "enum": ["text", "srt", "json"],
                },
            },
            ["file_id"],
        ),
    },
    {
        "name": "search",
        "description": "在全部转写文本里搜索关键词，返回命中的句子及其所在文件、时间点。",
        "inputSchema": _schema(
            {
                "query": {"type": "string", "description": "关键词"},
                "limit": {
                    "type": "integer",
                    "description": "返回条数，默认 10",
                    "minimum": 1,
                    "maximum": 200,
                },
            },
            ["query"],
        ),
    },
    {
        "name": "queue_state",
        "description": "查看转写队列与进度：排队/运行/完成/失败数量、当前任务、工作进程心跳。",
        "inputSchema": _schema({}),
    },
    {
        "name": "enqueue_transcribe",
        "description": "把文件加入转写队列；reset=true 时先清掉旧转写结果再重跑。",
        "inputSchema": _schema(
            {
                "file_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "要转写的文件 ID 列表",
                    "minItems": 1,
                },
                "reset": {"type": "boolean", "description": "是否重跑（默认 false）"},
            },
            ["file_ids"],
        ),
    },
    {
        "name": "cancel_jobs",
        "description": "取消排队任务；mode=all 时同时请求中断正在运行的任务。",
        "inputSchema": _schema(
            {
                "mode": {
                    "type": "string",
                    "description": "queued 只清排队（默认）；all 连正在运行的也中断",
                    "enum": ["queued", "all"],
                }
            }
        ),
    },
    {
        "name": "scan_library",
        "description": "扫描录音目录，把新增/变更的音频登记进库。",
        "inputSchema": _schema(
            {"dir": {"type": "string", "description": "要扫描的目录，默认用服务端设置"}}
        ),
    },
    {
        "name": "list_speakers",
        "description": "查看声纹库：人物列表、是否已登记声纹、各自关联的文件数。",
        "inputSchema": _schema({}),
    },
    {
        "name": "health",
        "description": "服务健康检查：GPU、ffmpeg、torch、模型与工作线程状态。",
        "inputSchema": _schema({}),
    },
]


# ------------------------------------------------------------------ 参数校验
def _require_int(args: dict[str, Any], key: str) -> int:
    if key not in args or args[key] is None:
        raise ToolError(f"缺少参数 {key}")
    try:
        return int(args[key])
    except (TypeError, ValueError) as exc:
        raise ToolError(f"参数 {key} 必须是整数") from exc


def _opt_int(
    args: dict[str, Any], key: str, default: int, minimum: int = 0, maximum: int | None = None
) -> int:
    value = args.get(key)
    if value is None:
        return default
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ToolError(f"参数 {key} 必须是整数") from exc
    if number < minimum or (maximum is not None and number > maximum):
        raise ToolError(f"参数 {key} 超出范围")
    return number


def _opt_str(args: dict[str, Any], key: str) -> str | None:
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ToolError(f"参数 {key} 必须是字符串")
    value = value.strip()
    return value or None


def _require_str(args: dict[str, Any], key: str) -> str:
    value = _opt_str(args, key)
    if not value:
        raise ToolError(f"缺少参数 {key}")
    return value


def _opt_bool(args: dict[str, Any], key: str, default: bool = False) -> bool:
    value = args.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes"}:
            return True
        if lowered in {"0", "false", "no"}:
            return False
    raise ToolError(f"参数 {key} 必须是布尔值")


def _require_int_list(args: dict[str, Any], key: str) -> list[int]:
    value = args.get(key)
    if not isinstance(value, list) or not value:
        raise ToolError(f"参数 {key} 必须是非空整数数组")
    result: list[int] = []
    for item in value:
        try:
            result.append(int(item))
        except (TypeError, ValueError) as exc:
            raise ToolError(f"参数 {key} 必须是非空整数数组") from exc
    return result


# ------------------------------------------------------------------ 工具实现
def _tool_list_files(server: str, args: dict[str, Any]) -> str:
    data = cli_client.fetch_files(
        server,
        status=_opt_str(args, "status"),
        q=_opt_str(args, "q"),
        limit=_opt_int(args, "limit", 50, minimum=1, maximum=500),
    )
    return cli_client.render_files(data)


def _tool_get_file(server: str, args: dict[str, Any]) -> str:
    return cli_client.render_file(cli_client.fetch_file(server, _require_int(args, "file_id")))


def _tool_get_transcript(server: str, args: dict[str, Any]) -> str:
    file_id = _require_int(args, "file_id")
    fmt = (_opt_str(args, "format") or "text").lower()
    if fmt not in {"text", "srt", "json"}:
        raise ToolError("参数 format 只能是 text / srt / json")
    data = cli_client.fetch_transcript(server, file_id)
    if fmt == "json":
        return json.dumps(data, ensure_ascii=False, indent=2)
    if fmt == "srt":
        return cli_client.render_srt(data) or "该文件还没有转写结果，无法导出 SRT。"
    return cli_client.render_transcript(data)


def _tool_search(server: str, args: dict[str, Any]) -> str:
    query = _require_str(args, "query")
    limit = _opt_int(args, "limit", 10, minimum=1, maximum=200)
    return cli_client.render_search(cli_client.fetch_search(server, query, limit=limit), query)


def _tool_queue_state(server: str, _args: dict[str, Any]) -> str:
    return cli_client.render_state(cli_client.fetch_state(server))


def _tool_enqueue(server: str, args: dict[str, Any]) -> str:
    ids = _require_int_list(args, "file_ids")
    reset = _opt_bool(args, "reset")
    return cli_client.render_enqueue(cli_client.enqueue(server, ids, reset=reset), reset=reset)


def _tool_cancel(server: str, args: dict[str, Any]) -> str:
    mode = (_opt_str(args, "mode") or "queued").lower()
    if mode not in {"queued", "all"}:
        raise ToolError("参数 mode 只能是 queued / all")
    return cli_client.render_cancel(cli_client.cancel(server, mode=mode))


def _tool_scan(server: str, args: dict[str, Any]) -> str:
    return cli_client.render_scan(cli_client.scan(server, _opt_str(args, "dir")))


def _tool_speakers(server: str, _args: dict[str, Any]) -> str:
    return cli_client.render_speakers(cli_client.fetch_speakers(server))


def _tool_health(server: str, _args: dict[str, Any]) -> str:
    return cli_client.render_health(cli_client.fetch_health(server))


_TOOL_HANDLERS: dict[str, Any] = {
    "list_files": _tool_list_files,
    "get_file": _tool_get_file,
    "get_transcript": _tool_get_transcript,
    "search": _tool_search,
    "queue_state": _tool_queue_state,
    "enqueue_transcribe": _tool_enqueue,
    "cancel_jobs": _tool_cancel,
    "scan_library": _tool_scan,
    "list_speakers": _tool_speakers,
    "health": _tool_health,
}


# ---------------------------------------------------------------- JSON-RPC 层
def _ok(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _fail(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _dispatch(server: str, method: str, params: Any) -> Any:
    if method == "initialize":
        requested = params.get("protocolVersion") if isinstance(params, dict) else None
        version = requested if isinstance(requested, str) and requested else PROTOCOL_VERSION
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": dict(SERVER_INFO),
        }
    if method in ("notifications/initialized", "initialized"):
        return {}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        if not isinstance(params, dict):
            raise RpcError(-32602, "tools/call 需要 params 对象")
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise RpcError(-32602, "tools/call 缺少工具名 name")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise RpcError(-32602, "tools/call 的 arguments 必须是对象")
        handler = _TOOL_HANDLERS.get(name)
        if handler is None:
            raise RpcError(-32602, f"未知工具：{name}")
        try:
            text = handler(server, arguments)
            is_error = False
        except (ApiError, ToolError) as exc:
            text = str(exc)
            is_error = True
        except Exception as exc:  # 工具内部异常也要变成 isError，而不是让整条连接断掉
            log.exception("工具 %s 执行失败", name)
            text = f"工具执行失败：{exc}"
            is_error = True
        return {"content": [{"type": "text", "text": text}], "isError": is_error}
    raise RpcError(-32601, f"不支持的方法：{method}")


def handle_message(server: str, message: Any) -> dict[str, Any] | None:
    """处理一条消息；返回 None 表示这是通知，无需响应。"""
    if not isinstance(message, dict):
        return _fail(None, -32600, "非法的 JSON-RPC 消息")
    method = message.get("method")
    has_id = "id" in message
    req_id = message.get("id")
    if not isinstance(method, str) or not method:
        return _fail(req_id if has_id else None, -32600, "缺少 method 字段")
    if not has_id:
        # 通知：按规范不返回任何响应
        return None
    try:
        result = _dispatch(server, method, message.get("params"))
    except RpcError as exc:
        return _fail(req_id, exc.code, exc.message)
    return _ok(req_id, result)


def _write(response: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _configure_stdio() -> None:
    """MCP 客户端按 UTF-8 收发；Windows 默认 GBK 会把中文参数/结果弄坏。"""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # pragma: no cover - 被重定向成非 TextIOWrapper 时才会失败
            pass


def _setup_logging() -> None:
    if log.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[mcp] %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False


def serve(server: str) -> int:
    """阻塞式主循环：读到 EOF / Ctrl+C 就干净退出。"""
    _configure_stdio()
    _setup_logging()
    log.info("已启动（server=%s），等待 stdin 上的 JSON-RPC 消息", server)
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                _write(_fail(None, -32700, f"JSON 解析失败：{exc.msg}"))
                continue
            response = handle_message(server, message)
            if response is not None:
                _write(response)
    except KeyboardInterrupt:
        log.info("收到 Ctrl+C，退出")
    except BrokenPipeError:
        log.info("stdout 已关闭，退出")
    log.info("已退出")
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    return serve(cli_client.resolve_server(args))


def register(subparsers: Any) -> None:
    """把 `mcp` 子命令挂到顶层 subparsers。"""
    parser = subparsers.add_parser(
        "mcp",
        parents=[cli_client.common_options(suppress=True)],
        help="作为 MCP server 运行（stdio，供 LLM/agent 客户端调用）",
        description="stdio 上的 MCP server，stdout 只输出 JSON-RPC 响应。",
    )
    parser.set_defaults(func=cmd_mcp)
