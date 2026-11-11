"""查看 spk_embedding_center 的真实类型与结构。"""
import sys
from funasr import AutoModel

wav = sys.argv[1] if len(sys.argv) > 1 else ".cache/tmp/cmp.wav"
model = AutoModel(
    model="lukeewin01/paraformer-large-sichuan-offline",
    vad_model="fsmn-vad",
    punc_model="ct-punc",
    spk_model="cam++",
    spk_mode="punc_segment",
    max_num_spks=15,
    device="cuda:0",
    ncpu=4,
    disable_update=True,
    log_level="ERROR",
)
res = model.generate(
    input=wav,
    batch_size_s=300,
    merge_vad=False,
    return_spk_res=True,
    return_spk_center=True,
    sentence_timestamp=True,
)
item = res[0]
print("keys:", list(item.keys()))
c = item.get("spk_embedding_center")
print("type:", type(c))
try:
    import numpy as np

    if isinstance(c, np.ndarray):
        print("shape:", c.shape, "dtype:", c.dtype)
    elif isinstance(c, dict):
        for k, v in c.items():
            print("  ", k, type(v), getattr(v, "shape", None))
    elif isinstance(c, (list, tuple)):
        print("len:", len(c), [getattr(x, "shape", type(x)) for x in c])
except Exception as e:  # noqa: BLE001
    print("err", e)
print("spks:", sorted({u.get("spk") for u in item.get("sentence_info", [])}))
print("n_sent:", len(item.get("sentence_info", [])))
