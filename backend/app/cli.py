"""命令行入口：`funasr-flow` / `python run.py`。

子命令：
    funasr-flow serve     启动 Web 服务（默认）
    funasr-flow scan      扫描录音目录
    funasr-flow worker    只跑后台转写进程（headless / 服务器部署）
    funasr-flow doctor    检查环境依赖
    funasr-flow api       以 HTTP 客户端方式调用已运行的服务（agent 友好）
    funasr-flow mcp       作为 MCP server 运行（stdio，供 LLM 客户端调用）
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import webbrowser

from . import cli_client, mcp_server


def _free_port(preferred: int) -> int:
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return preferred


def _open_later(url: str, delay: float = 1.5) -> None:
    def go() -> None:
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:
            pass

    threading.Thread(target=go, daemon=True).start()


def cmd_serve(args: argparse.Namespace) -> int:
    if args.no_worker:
        os.environ["FLOW_WORKER"] = "0"
    port = _free_port(args.port)
    url = f"http://{args.host}:{port}"
    if port != args.port:
        print(f"[info] 端口 {args.port} 被占用，改用 {port}")
    print(f"[ready] {url}")
    if not args.no_browser and not args.reload:
        _open_later(url)

    import uvicorn

    uvicorn.run(
        "backend.app.main:app",
        host=args.host,
        port=port,
        reload=args.reload,
        log_level=os.environ.get("FLOW_UVICORN_LOG", "info"),
    )
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    from .db import get_setting, init_db
    from .services import library

    init_db()
    root = args.dir or str(get_setting("input_dir"))
    print(f"导入 {root} → {library.LIBRARY_DIR}")
    res = library.import_dir(root, mode=str(get_setting("ingest_mode") or "copy"))
    print(
        f"导入 {res['added']} 个，跳过 {res['skipped']} 个（已在资料库），"
        f"失败 {res['failed']} 个，共发现 {res['total']} 个音频"
    )
    return 0


def cmd_worker(_args: argparse.Namespace) -> int:
    from .worker import main

    return main()


def cmd_doctor(_args: argparse.Namespace) -> int:
    import importlib.util
    import shutil

    rows = [
        ("python", sys.version.split()[0], True),
        ("ffmpeg", shutil.which("ffmpeg") or "未找到", bool(shutil.which("ffmpeg"))),
    ]
    for mod in ("fastapi", "uvicorn", "numpy", "soundfile", "funasr", "torch"):
        ok = importlib.util.find_spec(mod) is not None
        rows.append((mod, "已安装" if ok else "缺失", ok))
    try:
        import torch

        rows.append(
            (
                "cuda",
                torch.cuda.get_device_name(0) if torch.cuda.is_available() else "不可用",
                torch.cuda.is_available(),
            )
        )
    except Exception:
        rows.append(("cuda", "torch 未安装", False))
    width = max(len(r[0]) for r in rows) + 2
    for name, value, ok in rows:
        print(f"{'✔' if ok else '✘'} {name:<{width}} {value}")
    return 0 if all(r[2] for r in rows) else 1


def _first_positional(argv: list[str]) -> str | None:
    """跳过写在子命令前的全局选项（--server/--json），返回第一个子命令。"""
    idx = 0
    while idx < len(argv):
        token = argv[idx]
        if token == "--server":
            idx += 2
        elif token.startswith("--server=") or token == "--json":
            idx += 1
        else:
            break
    return argv[idx] if idx < len(argv) else None


def main(argv: list[str] | None = None) -> int:
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    ap = argparse.ArgumentParser(
        prog="funasr-flow",
        description="本地通话录音批量转写",
        parents=[cli_client.common_options()],
    )
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("serve", help="启动 Web 服务（默认）")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true", help="代码热重载（开发用）")
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--no-worker", action="store_true", help="只起 Web，不起转写线程")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("scan", help="扫描录音目录")
    p.add_argument("--dir", default=None)
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("worker", help="只跑后台转写进程")
    p.set_defaults(func=cmd_worker)

    p = sub.add_parser("doctor", help="检查环境")
    p.set_defaults(func=cmd_doctor)

    # 只做注册：serve/scan/worker/doctor 的行为保持不变
    cli_client.register(sub)
    mcp_server.register(sub)

    args_in = list(sys.argv[1:] if argv is None else argv)
    if args_in and args_in[0] in ("-h", "--help"):
        ap.print_help()
        return 0
    known = {"serve", "scan", "worker", "doctor", "api", "mcp"}
    # 没写子命令时默认 serve，这样 `funasr-flow --port 8000` 也能用；
    # 允许全局选项写在子命令前，例如 `funasr-flow --server URL api list`
    if not args_in or args_in[0] not in known:
        if _first_positional(args_in) not in known:
            args_in = ["serve"] + args_in
    args = ap.parse_args(args_in)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
