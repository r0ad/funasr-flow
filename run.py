#!/usr/bin/env python
"""便捷启动脚本（等价于 `funasr-flow serve`）。

    python run.py                 # 起服务并自动打开浏览器
    python run.py --scan          # 先扫描录音目录
    python run.py worker          # 只跑转写进程
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.app.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
