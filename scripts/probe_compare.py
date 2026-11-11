"""对比：两个 ASR 模型 + vad/punc/spk 全链路，输出 JSON 便于人工比对。"""

import json
import os
import subprocess
import sys
import time

os.environ.setdefault("MODELSCOPE_CACHE", ".cache/modelscope")
os.environ.setdefault("FUNASR_CACHE", ".cache/funasr")

from funasr import AutoModel  # noqa: E402

SRC = sys.argv[1] if len(sys.argv) > 1 else "CallRecord/10月13日 妈.m4a"
WAV = ".cache/tmp/cmp.wav"
subprocess.run(
    [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-i",
        SRC,
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        WAV,
    ],
    check=True,
)

for mid, tag in [("paraformer-zh", "zh"), ("lukeewin01/paraformer-large-sichuan-offline", "sc")]:
    t0 = time.time()
    m = AutoModel(
        model=mid,
        vad_model="fsmn-vad",
        punc_model="ct-punc",
        spk_model="cam++",
        device="cuda:0",
        disable_update=True,
        log_level="ERROR",
    )
    r = m.generate(input=WAV, batch_size_s=300)
    el = time.time() - t0
    item = r[0]
    si = item.get("sentence_info") or []
    data = {
        "model": mid,
        "elapsed": round(el, 2),
        "n_sent": len(si),
        "speakers": sorted({s.get("spk") for s in si}),
        "sentences": [
            {"start": s["start"], "end": s["end"], "spk": s.get("spk"), "text": s["text"]}
            for s in si
        ],
    }
    with open(f".cache/tmp/cmp_{tag}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"{tag}: {el:.1f}s n={len(si)} spk={data['speakers']}")
    del m
