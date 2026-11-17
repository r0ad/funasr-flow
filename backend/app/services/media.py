"""音视频探测与抽轨。

上传/导入时可以给视频文件：这里负责判断「这个文件里到底有没有画面」，
有画面就用 ffmpeg 把音轨抽成 16k 单声道 m4a，**视频本体不留在资料库里**
（手机录像动辄几百兆，转写只用得到声音）。
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

#: 抽出来的音轨统一存成 m4a：aac 64k / 16k 单声道，1 小时约 28MB，够 ASR 用。
AUDIO_SUFFIX = ".m4a"


def probe(path: str | Path) -> dict:
    """读媒体信息：``{has_video, has_audio, duration}``。

    ffprobe 不存在、文件损坏、格式不认识时抛 ``RuntimeError``（带 ffprobe 原始报错尾部）。
    """
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "stream=codec_type",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as exc:  # 没装 ffmpeg 的环境
        raise RuntimeError("找不到 ffprobe，请先安装 ffmpeg") from exc
    if p.returncode != 0:
        detail = (p.stderr or "").strip().splitlines()
        raise RuntimeError(f"无法解析媒体文件: {detail[-1] if detail else p.returncode}")

    try:
        data = json.loads(p.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("ffprobe 输出无法解析") from exc

    kinds = {stream.get("codec_type") for stream in data.get("streams") or []}
    try:
        duration = float((data.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return {"has_video": "video" in kinds, "has_audio": "audio" in kinds, "duration": duration}


def is_video(path: str | Path) -> bool:
    """真实判断有没有画面（扩展名不可靠：.mp4 也可能是纯音频）。"""
    try:
        return bool(probe(path)["has_video"])
    except RuntimeError as exc:
        log.warning("探测失败，按音频处理: %s (%s)", path, exc)
        return False


def audio_name(name: str) -> str:
    """``ep1.mp4`` → ``ep1.m4a``（抽轨后的文件名）。"""
    return f"{Path(str(name)).stem}{AUDIO_SUFFIX}"


def extract(src: str | Path, dst: str | Path) -> Path:
    """把 ``src`` 的音轨抽到 ``dst``，返回 dst；失败抛 ``RuntimeError`` 且不留半成品。"""
    src_p, dst_p = Path(src), Path(dst)
    dst_p.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-i",
        str(src_p),
        "-vn",  # 丢掉画面
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "aac",
        "-b:a",
        "64k",
        str(dst_p),
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("找不到 ffmpeg，请先安装 ffmpeg") from exc

    if p.returncode != 0 or not dst_p.exists() or dst_p.stat().st_size == 0:
        dst_p.unlink(missing_ok=True)
        detail = (p.stderr or "").strip().splitlines()
        raise RuntimeError(f"视频抽音轨失败: {detail[-1] if detail else p.returncode}")
    return dst_p
