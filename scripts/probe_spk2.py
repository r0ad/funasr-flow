"""诊断 2：看 FunASR 原始句子时间戳/归属是否可信，并尝试用质心重新归属。

用法：python scripts/probe_spk2.py "CallRecord/10月10日 妈.m4a"
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import DEFAULT_SETTINGS, ensure_dirs, temp_path  # noqa: E402
from backend.app.engines.funasr_engine import FunAsrEngine  # noqa: E402


def to_wav(src: Path) -> Path:
    out = temp_path(".wav", "probe2-")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", str(out)],
        check=True,
    )
    return out


def main() -> None:
    src = Path(sys.argv[1])
    ensure_dirs()
    wav = to_wav(src)
    cfg = dict(DEFAULT_SETTINGS)
    cfg["asr_model"] = "lukeewin01/paraformer-large-sichuan-offline"
    eng = FunAsrEngine()
    try:
        eng.load(cfg)
        kw = {
            "batch_size_s": 300,
            "merge_vad": False,
            "return_spk_res": True,
            "return_spk_center": True,
            "sentence_timestamp": True,
        }
        item, centers = eng._generate(str(wav), kw)
        info = item.get("sentence_info") or []
        print(f"句数 {len(info)}，质心类型 {type(centers).__name__} 形状 {getattr(centers, 'shape', None)}")
        for i, s in enumerate(info[:12]):
            ts = s.get("timestamp") or []
            first = ts[0][0] if ts else None
            last = ts[-1][1] if ts else None
            print(
                f"  [{i:02d}] {s.get('start')}-{s.get('end')} spk={s.get('spk')} "
                f"chars_ts={len(ts)} first={first} last={last} | {str(s.get('text'))[:26]}"
            )
        # 用每句话的音频片段重新提声纹，与质心比余弦
        import soundfile as sf

        audio, sr = sf.read(str(wav), dtype="float32", always_2d=False)
        cents = {}
        if isinstance(centers, np.ndarray):
            arr = centers if centers.ndim == 2 else centers.reshape(1, -1)
            for k in range(arr.shape[0]):
                v = np.asarray(arr[k], dtype=np.float32)
                cents[f"spk{k}"] = v / (np.linalg.norm(v) + 1e-9)
        print("重新归属（每句独立提声纹）:")
        for i, s in enumerate(info):
            seg = audio[int(s["start"] * sr / 1000) : int(s["end"] * sr / 1000)]
            if seg.size < sr * 0.2:
                continue
            emb = eng.embed_audio(seg, cfg)
            if emb is None:
                continue
            v = np.asarray(emb, dtype=np.float32).reshape(-1)
            v = v / (np.linalg.norm(v) + 1e-9)
            sims = {k: float(v @ c) for k, c in cents.items()}
            best = max(sims, key=sims.get)
            print(
                f"  [{i:02d}] 原={s.get('spk')} 新={best} "
                + " ".join(f"{k}={val:.3f}" for k, val in sims.items())
                + f" | {str(s.get('text'))[:20]}"
            )
    finally:
        wav.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
