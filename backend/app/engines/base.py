"""ASR 引擎抽象层。

新增引擎（如 faster-whisper、SenseVoice、云端 API）只需：
  1. 实现 `AsrEngine` 协议
  2. 在 `registry.py` 里注册
不需要改动队列、流水线或前端。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import numpy as np


@dataclass
class Utterance:
    """一句带时间戳与说话人簇标签的话。时间单位统一为毫秒。"""

    start_ms: int
    end_ms: int
    text: str
    cluster: str = "spk0"


@dataclass
class AsrResult:
    utterances: list[Utterance] = field(default_factory=list)
    clusters: list[str] = field(default_factory=list)
    # cluster -> 声纹向量（L2 归一化前的原始向量即可）
    centroids: dict[str, np.ndarray] = field(default_factory=dict)
    duration: float = 0.0
    model: str = ""
    engine: str = ""
    language: str = "zh"

    @property
    def full_text(self) -> str:
        return "\n".join(u.text for u in self.utterances)


# progress_cb(stage: str, ratio: float, message: str) -> None
ProgressCb = Callable[[str, float, str], None]


@runtime_checkable
class AsrEngine(Protocol):
    """所有引擎实现的最小接口。"""

    name: str

    @classmethod
    def available(cls) -> tuple[bool, str]:
        """返回 (是否可用, 不可用原因)。用于设置页展示。"""
        ...

    def load(self, cfg: dict) -> None:
        """加载/切换模型。实现方负责幂等缓存。"""
        ...

    def transcribe(self, wav_path: str, cfg: dict, progress: ProgressCb | None = None) -> AsrResult:
        """整段音频 -> 带说话人簇的句子列表。"""
        ...

    def embed(self, wav_path: str) -> np.ndarray:
        """提取一段音频的声纹向量（一维 float32）。"""
        ...

    def release(self) -> None:
        """释放显存。"""
        ...
