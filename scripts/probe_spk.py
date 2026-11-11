"""诊断：一条录音的原始说话人簇、质心相似度、三段式合并前后的簇数。

用法：python scripts/probe_spk.py "CallRecord/10月10日 妈.m4a"
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import DEFAULT_SETTINGS, ensure_dirs, temp_path  # noqa: E402
from backend.app.engines.base import Utterance  # noqa: E402
from backend.app.engines.funasr_engine import FunAsrEngine  # noqa: E402
from backend.app.services.pipeline import merge_similar_clusters  # noqa: E402


def to_wav(src: Path) -> Path:
    out = temp_path(".wav", "probe-")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", str(out)],
        check=True,
    )
    return out


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    src = Path(sys.argv[1])
    ensure_dirs()
    wav = to_wav(src)
    cfg = dict(DEFAULT_SETTINGS)
    cfg["asr_model"] = "lukeewin01/paraformer-large-sichuan-offline"
    try:
        eng = FunAsrEngine()
        eng.load(cfg)
        res = eng.transcribe(wav, cfg)
        print(f"句数 {len(res.utterances)}，声称簇 {sorted(res.clusters)}")
        counts = {}
        for u in res.utterances:
            counts[u.cluster] = counts.get(u.cluster, 0) + 1
        print("原始分布:", counts)

        keys = sorted(res.centroids)
        print(f"质心数 {len(keys)}: {keys}")
        mat = np.stack([res.centroids[k] for k in keys])
        mat = mat / np.linalg.norm(mat, axis=1, keepdims=True)
        sim = mat @ mat.T
        print("两两余弦:")
        for i, a in enumerate(keys):
            for j in range(i + 1, len(keys)):
                print(f"  {a} ~ {keys[j]}: {sim[i, j]:.4f}")

        utts = [Utterance(u.start_ms, u.end_ms, u.text, u.cluster) for u in res.utterances]
        for thr in (0.60, 0.65, 0.70, 0.72, 0.78):
            cents, alias = merge_similar_clusters(
                res.centroids, utts, threshold=thr, small_ratio=0.15, absorb_thr=0.35, target=2
            )
            print(f"threshold={thr} target=2 -> {len(cents)} 簇 alias={alias}")
        cents, alias = merge_similar_clusters(
            res.centroids, utts, threshold=0.72, small_ratio=0.15, absorb_thr=0.35, target=None
        )
        print(f"threshold=0.72 target=None -> {len(cents)} 簇 alias={alias}")
    finally:
        wav.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
