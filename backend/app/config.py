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
LIBRARY_DIR = DATA_DIR / "library"  # 导入/上传的录音都复制到这里（应用自己管理，跟着 /data 卷走）
UPLOAD_DIR = LIBRARY_DIR  # 旧名字保留，上传直接落资料库
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
    # 模型下载来源：ms = ModelScope（默认，国内快）；hf = HuggingFace；
    # auto = 先 ModelScope，失败再试 HuggingFace（新模型常常只有一边有）
    "model_hub": "ms",
    "hotwords": "妈妈 爸爸 哥哥 姐姐 外婆 外公 幺爸 幺妈 舅舅 嬢嬢",
    "spk_num": 0,  # 0 = 自动估计；2 = 强制双人
    "batch_size_s": 300,
    "auto_map_speakers": True,
    "min_speaker_sim": 0.62,  # 声纹匹配阈值
    "merge_threshold": 0.72,  # 同一个人被拆成两簇时的合并阈值
    "absorb_threshold": 0.45,  # 碎片簇（噪声/短句）被吸收的最低相似度
    "absorb_max_seconds": 6.0,  # 累计时长不超过此值（且 ≤3 句）才算碎片簇
    "concurrency": 1,  # GPU 串行，勿改
    "ingest_mode": "copy",  # 导入方式：copy=复制进资料库（默认）；link=硬链接（同一磁盘时才生效）
    "keep_wav": False,
    "auto_scan_minutes": 0,  # 0 = 关闭定时扫描
}

# 可选模型清单，前端“设置”页展示。
# 字段：id（配置值）/ label（下拉显示）/ note（补充说明）/ size（缓存占用，量级估计）/ tags（能力标签）
MODEL_CHOICES = {
    "asr": [
        {
            "id": "paraformer-zh",
            "label": "SeACo-Paraformer 中文通用（推荐）",
            "note": "中文/中英混说，支持热词；带字级时间戳，说话人归属最细",
            "size": "约 950 MB",
            "tags": ["中文", "热词", "时间戳"],
        },
        {
            "id": "iic/speech_paraformer-large-contextual_asr_nat-zh-cn-16k-common-vocab8404",
            "label": "Paraformer-large 热词增强版",
            "note": "热词/上下文偏置更强，识别略慢；同样带时间戳",
            "size": "约 900 MB",
            "tags": ["中文", "热词", "时间戳"],
        },
        {
            "id": "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
            "label": "Paraformer-large 基础版",
            "note": "无热词，作为对照/排障用；带时间戳",
            "size": "约 850 MB",
            "tags": ["中文", "时间戳"],
        },
        {
            "id": "iic/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
            "label": "Paraformer 一体版（自带 VAD + 标点）",
            "note": "模型自带端点与标点，少加载两个子模型、启动更快；句子较长，归属粒度略粗",
            "size": "约 1.1 GB",
            "tags": ["中文", "一体", "时间戳"],
        },
        {
            "id": "lukeewin01/paraformer-large-sichuan-offline",
            "label": "四川话微调（205h 方言数据）",
            "note": "四川/重庆通话明显更准；社区模型，未声明 License",
            "size": "约 850 MB",
            "tags": ["方言", "四川话", "时间戳"],
        },
        {
            "id": "dengcunqin/speech_paraformer_large_asr_mtl-16k-common-vocab11666-pytorch",
            "label": "多方言通用（粤/闽/吴/川等）",
            "note": "多方言混合训练；词表与通用版不同，热词不生效",
            "size": "约 900 MB",
            "tags": ["方言", "多语种"],
        },
        {
            "id": "FunAudioLLM/Fun-ASR-Nano-2512",
            "label": "Fun-ASR Nano（7 大方言，实验）",
            "note": "800M，多语种/方言；时间戳支不支看实现，缺时间戳时说话人归属会退化成 VAD 粒度",
            "size": "约 800 MB",
            "tags": ["方言", "多语种", "实验"],
        },
        {
            "id": "paraformer-en",
            "label": "Paraformer 英文通用",
            "note": "英文通话/英文语音；中文录音不要选它",
            "size": "约 900 MB",
            "tags": ["英文", "时间戳"],
        },
    ],
    "spk": [
        {
            "id": "cam++",
            "label": "CAM++ 中文声纹（推荐）",
            "note": "192 维声纹，中英都能用",
            "size": "约 29 MB",
        },
        {
            "id": "iic/speech_campplus_sv_zh-cn_16k-common",
            "label": "CAM++（完整 id，等价）",
            "note": "与上面同一个模型，写全 id 的写法",
            "size": "约 29 MB",
        },
    ],
    "punc": [
        {
            "id": "ct-punc",
            "label": "CT-Punc 大模型（推荐）",
            "note": "中英标点 + 大小写，最准",
            "size": "约 1.2 GB",
        },
        {
            "id": "ct-punc-c",
            "label": "CT-Punc 小模型（快）",
            "note": "中文标点，占用小、速度快",
            "size": "约 300 MB",
        },
    ],
    "vad": [
        {
            "id": "fsmn-vad",
            "label": "FSMN-VAD（推荐）",
            "note": "16k 通用端点检测",
            "size": "约 4 MB",
        },
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
    for d in (DATA_DIR, OUT_DIR, CACHE_DIR, TMP_DIR, LIBRARY_DIR):
        d.mkdir(parents=True, exist_ok=True)
