"""从一条通话录音里提取每个说话人的声纹（192 维 cam++ 向量）并导出到目录。

用法：
    python scripts/export_voiceprints.py "CallRecord/10月23日 妈 2.m4a" \
        --out H:/ws-ai/test-res/call-res-voice --ref-name 妈妈

做法：
1. ffmpeg 转 16k 单声道 wav；
2. FunASR 一体化转写（VAD + 标点 + cam++ 聚类），拿到逐句时间轴、簇标签与质心；
3. 用三段式聚类把过分裂的簇收成真正的说话人数（默认 target=2）；
4. 用声纹库里已登记的「妈妈」向量判断哪个簇是妈妈，其余簇按说话时长命名为「我」；
5. 导出 `<名字>.npy`（192 维 float32，L2 归一化）+ `<名字>.json`（元信息/相似度/例句）
   + `manifest.json` + 每个说话人一段 15 秒试听 wav。

输出是可直接喂给声纹库/比对脚本的标准向量，不依赖本项目数据库。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app import db  # noqa: E402
from backend.app.config import temp_path  # noqa: E402
from backend.app.engines.funasr_engine import FunAsrEngine  # noqa: E402
from backend.app.services.pipeline import merge_similar_clusters  # noqa: E402


def to_wav(src: Path) -> Path:
    out = temp_path(".wav", "vp-")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(src),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(out),
        ],
        check=True,
    )
    return out


def normalize(vec: np.ndarray) -> np.ndarray:
    vec = np.asarray(vec, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm else vec


def ref_embedding(name: str | None) -> tuple[str, np.ndarray] | None:
    """从本地声纹库里取参考向量（默认取名字里含「妈」的那条）。"""
    if not name:
        return None
    row = (
        db.get_conn()
        .execute(
            "SELECT name, embedding FROM speakers WHERE embedding IS NOT NULL AND name LIKE ? "
            "ORDER BY id LIMIT 1",
            (f"%{name}%",),
        )
        .fetchone()
    )
    if not row:
        return None
    return row["name"], normalize(np.frombuffer(row["embedding"], dtype=np.float32))


def slice_wav(wav: Path, start_ms: int, end_ms: int, dest: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-ss",
            f"{start_ms / 1000:.3f}",
            "-to",
            f"{end_ms / 1000:.3f}",
            "-i",
            str(wav),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(dest),
        ],
        check=True,
    )


def pick_sample(utts, cluster: str, target_ms: int = 15000):
    """挑一段连续、只有这个人说话的区间做试听。"""
    mine = [u for u in utts if u.cluster == cluster]
    if not mine:
        return None
    best = max(mine, key=lambda u: u.end_ms - u.start_ms)
    start = best.start_ms
    for u in mine:  # 向前后补足到 target_ms
        if u.start_ms <= start and u.end_ms > best.end_ms:
            best = u
    end = max(best.end_ms, start + target_ms)
    return start, end


def main() -> int:
    ap = argparse.ArgumentParser(description="导出通话录音里每个说话人的声纹")
    ap.add_argument("audio", help="音频路径（m4a/mp3/wav 都行）")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--ref-name", default="妈妈", help="声纹库里用来认人的参考名字（默认 妈妈）")
    ap.add_argument("--target", type=int, default=2, help="这条录音里有几个人（默认 2）")
    ap.add_argument("--model", default=None, help="覆盖 ASR 模型（默认用设置里的）")
    args = ap.parse_args()

    src = Path(args.audio)
    if not src.exists():
        print(f"[error] 找不到音频：{src}")
        return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    cfg = dict(db.all_settings())
    if args.model:
        cfg["asr_model"] = args.model
    cfg["spk_num"] = args.target

    print(f"[1/4] 转码 {src.name} → 16k wav")
    wav = to_wav(src)

    print(f"[2/4] 转写 + 说话人分离（模型 {cfg.get('asr_model')}，目标 {args.target} 人）")
    engine = FunAsrEngine()
    engine.load(cfg)
    res = engine.transcribe(
        str(wav), cfg, progress=lambda stage, ratio, msg: print(f"      {stage} {ratio:.0%}")
    )
    if not res.duration:  # 引擎不回填时长时用 wav 头里的
        import soundfile as sf

        res.duration = float(sf.info(str(wav)).duration)
    print(f"      {len(res.utterances)} 句，{len(res.clusters)} 簇，音频 {res.duration:.1f}s")

    print("[3/4] 收拾过聚类、认人")
    centroids, alias = merge_similar_clusters(
        {k: normalize(v) for k, v in res.centroids.items()},
        res.utterances,
        threshold=float(cfg.get("merge_threshold", 0.72)),
        absorb_thr=float(cfg.get("absorb_threshold", 0.45)),
        absorb_max_seconds=float(cfg.get("absorb_max_seconds", 6.0)),
        target=args.target,
    )
    for u in res.utterances:
        u.cluster = alias.get(u.cluster, u.cluster)
    centroids = {k: normalize(v) for k, v in centroids.items()}

    ref = ref_embedding(args.ref_name)
    ref_cluster = None
    if ref:
        ref_name, ref_vec = ref
        sims = {k: float(np.dot(v, ref_vec)) for k, v in centroids.items()}
        ref_cluster = max(sims, key=sims.get)
        print(f"      参考声纹「{ref_name}」→ {ref_cluster}（相似度 {sims[ref_cluster]:.3f}）")
        if sims[ref_cluster] < 0.4:
            print("      [warn] 相似度偏低，认人结果可能不准，请人工核对试听片段")
    else:
        print(f"      [warn] 声纹库里没有像「{args.ref_name}」的记录，按说话时长排第一/第二")

    # 分配名字：参考簇 = 参考名字，其余按说话时长从多到少依次命名
    stats = {}
    for cluster in centroids:
        mine = [u for u in res.utterances if u.cluster == cluster]
        stats[cluster] = {
            "utt_count": len(mine),
            "speech_seconds": round(sum((u.end_ms - u.start_ms) for u in mine) / 1000, 1),
            "text": " ".join(u.text for u in mine)[:200],
        }
    order = sorted(centroids, key=lambda k: stats[k]["speech_seconds"], reverse=True)
    names: dict[str, str] = {}
    if ref_cluster:
        names[ref_cluster] = ref_name
    others = [c for c in order if c not in names]
    fallback = [n for n in ("我", "对方", "说话人3", "说话人4") if n not in names.values()]
    for cluster, name in zip(others, fallback, strict=False):
        names[cluster] = name

    print("[4/4] 写文件")
    manifest = {
        "source": str(src),
        "duration_seconds": round(res.duration, 2),
        "model": res.model or cfg.get("asr_model"),
        "engine": res.engine or "funasr",
        "speakers": {},
    }
    for cluster, vec in sorted(centroids.items(), key=lambda kv: -stats[kv[0]]["speech_seconds"]):
        name = names.get(cluster, cluster)
        vec = normalize(vec)
        npy = out / f"{name}.npy"
        np.save(npy, vec)
        sims = {
            f"cos_vs_{other}": round(float(np.dot(vec, other_vec)), 4)
            for other, other_vec in centroids.items()
            if other != cluster
        }
        sample = pick_sample(res.utterances, cluster)
        sample_name = None
        if sample:
            sample_name = f"{name}-sample.wav"
            slice_wav(wav, sample[0], sample[1], out / sample_name)
        info = {
            "name": name,
            "cluster": cluster,
            "dim": int(vec.shape[0]),
            "source": str(src),
            "speech_seconds": stats[cluster]["speech_seconds"],
            "utt_count": stats[cluster]["utt_count"],
            "model": res.model or cfg.get("asr_model"),
            "vector_file": npy.name,
            "sample_audio": sample_name,
            "similarities": sims,
            "samples": [u.text for u in res.utterances if u.cluster == cluster][:8],
        }
        (out / f"{name}.json").write_text(
            json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        manifest["speakers"][name] = info
        print(
            f"      {name}: {stats[cluster]['utt_count']} 句 / "
            f"{stats[cluster]['speech_seconds']}s → {npy.name}"
        )

    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n完成，输出目录：{out}")
    for name in manifest["speakers"]:
        print(f"  {out / (name + '.npy')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
