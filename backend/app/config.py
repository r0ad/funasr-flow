"""全局配置：路径、模型目录、默认参数。

所有可变路径都可通过环境变量覆盖，便于把模型/输出放到别的盘。
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("FLOW_DATA_DIR", ROOT / "data"))
OUT_DIR = DATA_DIR / "transcripts"
CACHE_DIR = Path(os.environ.get("FLOW_CACHE_DIR", ROOT / ".cache"))
TMP_DIR = CACHE_DIR / "tmp"
DB_PATH = DATA_DIR / "flow.db"
FRONTEND_DIST = ROOT / "frontend" / "dist"

# 模型缓存全部落在项目内，避免占用 C 盘
os.environ.setdefault("MODELSCOPE_CACHE", str(CACHE_DIR / "modelscope"))
os.environ.setdefault("HF_HOME", str(CACHE_DIR / "huggingface"))
os.environ.setdefault("FUNASR_CACHE", str(CACHE_DIR / "funasr"))

DEFAULT_SETTINGS = {
    "input_dir": str(ROOT / "CallRecord"),
    "device": "cuda:0",
    "asr_model": "paraformer-zh",
    "vad_model": "fsmn-vad",
    "punc_model": "ct-punc",
    "spk_model": "cam++",
    "hotwords": "妈妈 爸爸 哥哥 姐姐 外婆 外公 幺爸 幺妈 舅舅 嬢嬢",
    "spk_num": 0,  # 0 = 自动估计；2 = 强制双人
    "batch_size_s": 300,
    "auto_map_speakers": True,
    "min_speaker_sim": 0.62,  # 声纹匹配阈值
    "merge_threshold": 0.72,  # 同一个人被拆成两簇时的合并阈值
    "absorb_threshold": 0.45,  # 碎片簇（噪声/短句）被吸收的最低相似度
    "absorb_max_seconds": 6.0,  # 累计时长不超过此值（且 ≤3 句）才算碎片簇
    "concurrency": 1,  # GPU 串行，勿改
    "keep_wav": False,
    "auto_scan_minutes": 0,  # 0 = 关闭定时扫描
}

# 可选模型清单，前端“设置”页展示
MODEL_CHOICES = {
    "asr": [
        {"id": "paraformer-zh", "label": "Paraformer 中文通用（快，推荐）"},
        {
            "id": "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
            "label": "Paraformer-large 完整版",
        },
        {
            "id": "lukeewin01/paraformer-large-sichuan-offline",
            "label": "Paraformer 四川话微调（205h，方言更准）",
        },
    ],
    "spk": [
        {"id": "cam++", "label": "CAM++ 中文声纹（推荐）"},
    ],
}


def temp_path(suffix: str = "", prefix: str = "flow-") -> Path:
    """在 TMP_DIR 下取一个临时文件路径并立即关闭句柄。

    Windows 上 mkstemp 返回的 fd 不关掉，之后 unlink 会报 WinError 32，
    所以调用方拿到的总是已关闭的路径。
    """
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(suffix=suffix, prefix=prefix, dir=str(TMP_DIR))
    os.close(fd)
    return Path(name)


def safe_unlink(path: Path) -> None:
    """删除临时文件；被占用时不抛异常（留给下次 / tmp 清理）。"""
    try:
        path.unlink(missing_ok=True)
    except OSError as e:
        logging.getLogger(__name__).debug("临时文件删除失败 %s: %s", path, e)


def ensure_dirs() -> None:
    for d in (DATA_DIR, OUT_DIR, CACHE_DIR, TMP_DIR):
        d.mkdir(parents=True, exist_ok=True)
