"""开发期探针：验证 FunASR 方言转写 + 说话人分离在本机可用。

用法: python scripts/probe_pipeline.py [音频路径 ...]
不写入数据库、不影响正式流程，只打印结构化结果与耗时。
"""
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("MODELSCOPE_CACHE", os.path.join(ROOT, ".cache", "modelscope"))
os.environ.setdefault("HF_HOME", os.path.join(ROOT, ".cache", "huggingface"))
os.environ.setdefault("FUNASR_CACHE", os.path.join(ROOT, ".cache", "funasr"))

DEFAULT_SAMPLES = [
    "CallRecord/010 5683 5555_20240603145324.m4a",   # 17s 短
    "CallRecord/10月13日 妈.m4a",                     # 67s 中
    "CallRecord/10月22日 妈.m4a",                     # 669s 长
]


def to_wav(src: str, dst: str) -> float:
    t0 = time.time()
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", src, "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", dst],
        check=True,
    )
    return time.time() - t0


def main() -> int:
    from funasr import AutoModel

    files = sys.argv[1:] or DEFAULT_SAMPLES
    print("== loading models ==", flush=True)
    t0 = time.time()
    model = AutoModel(
        model="paraformer-zh",
        vad_model="fsmn-vad",
        punc_model="ct-punc",
        spk_model="cam++",
        device="cuda:0",
        disable_update=True,
        log_level="ERROR",
    )
    print(f"load: {time.time() - t0:.1f}s", flush=True)

    tmpdir = os.path.join(ROOT, ".cache", "tmp")
    os.makedirs(tmpdir, exist_ok=True)

    for f in files:
        p = f if os.path.isabs(f) else os.path.join(ROOT, f)
        if not os.path.exists(p):
            print(f"!! missing {p}")
            continue
        wav = os.path.join(tmpdir, "probe.wav")
        cvt = to_wav(p, wav)
        t1 = time.time()
        res = model.generate(
            input=wav,
            batch_size_s=300,
            hotword="妈妈 爸爸 哥哥 外婆 幺爸 郫县 华西医院",
        )
        dur = time.time() - t1
        item = res[0] if isinstance(res, list) else res
        print(f"\n== {os.path.basename(p)}  conv={cvt:.1f}s asr={dur:.1f}s")
        print("keys:", list(item.keys()))
        si = item.get("sentence_info")
        if si:
            print("sentence_info[0]:", json.dumps(si[0], ensure_ascii=False, default=str)[:500])
            spks = {}
            for s in si:
                spks.setdefault(s.get("spk"), 0)
                spks[s.get("spk")] += 1
            print("spk dist:", spks)
            for s in si[:6]:
                print(f"  [{s['start']:>7}-{s['end']:>7}] spk={s.get('spk')} {s['text']}")
        else:
            print("text:", str(item.get("text"))[:300])
    print("\n== done ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
