"""单文件处理流水线：转码 -> 识别 -> 说话人聚类/声纹映射 -> 落库。

与 Web 层解耦：worker 进程直接调用 `process_job`。
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from collections import defaultdict
from collections.abc import Callable

import numpy as np

from ..config import OUT_DIR, TMP_DIR
from ..db import get_conn, get_metric, get_setting, set_metric, tx
from ..engines.base import AsrResult
from ..engines.registry import get_engine, load_builtin
from .library import file_path

log = logging.getLogger(__name__)


class Canceled(Exception):
    pass


_SURROGATE = re.compile(r"[\ud800-\udfff]")
_MEANINGFUL = re.compile(r"[0-9A-Za-z\u4e00-\u9fff]")


def clean_text(text: str) -> str:
    """清掉模型在噪声上吐出的非法字符（孤立代理对、控制符），否则会污染 SQLite/JSON。"""
    out = _SURROGATE.sub("", text or "")
    out = "".join(ch for ch in out if ch >= " " or ch in "\n\t")
    return out.strip()


def meaningful_chars(text: str) -> int:
    return len(_MEANINGFUL.findall(text or ""))


def ffprobe_duration(path: str) -> float:
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return float(out.stdout.strip())
    except Exception:
        return 0.0


def convert_to_wav(src: str, dst: str) -> None:
    """统一为 16k 单声道 PCM，所有模型的前提。"""
    cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-i",
        src,
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        dst,
    ]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0 or not os.path.exists(dst):
        raise RuntimeError(f"ffmpeg 转码失败: {(p.stderr or '')[-400:]}")


def _wav_seconds(path: str) -> float:
    """读 wav 时长（秒），用于识别空文件/坏文件。"""
    try:
        import soundfile as sf

        info = sf.info(path)
        return float(info.frames) / float(info.samplerate or 1)
    except Exception:
        return 0.0


def _load_wav(path: str) -> tuple[np.ndarray, int]:
    import soundfile as sf

    data, sr = sf.read(path, dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    return data, sr


def build_centroids(
    engine, audio: np.ndarray, sr: int, utts, cfg: dict, max_per_cluster: int = 6
) -> dict[str, np.ndarray]:
    """对每个说话人簇，取若干条最长的语音算平均声纹。"""
    from collections import defaultdict

    buckets: dict[str, list] = defaultdict(list)
    for u in utts:
        buckets[u.cluster].append(u)

    centroids: dict[str, np.ndarray] = {}
    for cluster, items in buckets.items():
        ordered = sorted(items, key=lambda u: u.end_ms - u.start_ms, reverse=True)
        picked = [u for u in ordered if (u.end_ms - u.start_ms) >= 1200][:max_per_cluster]
        if not picked:
            picked = ordered[:2]
        embs = []
        for u in picked:
            seg = audio[int(u.start_ms * sr / 1000) : int(u.end_ms * sr / 1000)]
            if seg.size < sr * 0.4:
                continue
            try:
                embs.append(engine.embed_audio(seg, cfg))
            except Exception as e:  # 单条失败不影响整体
                log.warning("embed failed %s: %s", cluster, e)
        if embs:
            c = np.mean(np.stack(embs), axis=0)
            n = np.linalg.norm(c)
            centroids[cluster] = (c / n).astype(np.float32)
    return centroids


def merge_similar_clusters(
    centroids: dict[str, np.ndarray],
    utts,
    threshold: float = 0.72,
    absorb_thr: float = 0.5,
    absorb_max_seconds: float = 5.0,
    absorb_max_utts: int = 2,
    target: int | None = None,
):
    """把"一个人被拆成多个簇"的情况收拾干净。

    四步：
    0. 没有任何句子的簇直接丢弃（不参与合并，也不会被当成吸收目标）；
    1. 余弦 ≥ threshold 的簇直接合并；
    2. 若指定了目标人数（设置里的"说话人数"），反复合并最相似的一对直到达标；
    3. 吸收碎片簇：只说了一两句、累计时长 ≤ absorb_max_seconds、且与某个大簇相似度
       ≥ absorb_thr 的簇并入该大簇（电话录音里 VAD 切出的噪声片段常自成一簇）。

    第 3 步刻意保守：早先按"时长占比"判断碎片，会把真实存在的第二说话人（整通电话
    只说了几句）并进主说话人，导致整条录音只剩一个人。宁可多留一个簇让用户手动合并，
    也不能把两个人当成一个。
    """
    keys = list(centroids)
    if len(keys) < 2:
        return centroids, {k: k for k in keys}

    weights: dict[str, float] = defaultdict(float)
    utt_counts: dict[str, int] = defaultdict(int)
    for u in utts:
        weights[u.cluster] += max(0.001, (u.end_ms - u.start_ms) / 1000.0)
        utt_counts[u.cluster] += 1

    # 0) 没有任何句子的簇直接丢掉。它们既给不了任何一行文字贴标签，也不该当吸收目标
    #    ——否则真实说话人的句子会被挂到一个空簇的名字上。
    kept = [k for k in keys if utt_counts[k] > 0] or keys
    dropped_any = len(kept) < len(keys)
    keys = kept
    if len(keys) < 2:
        return {k: centroids[k] for k in keys}, {k: k for k in keys}

    groups: dict[str, list[str]] = {k: [k] for k in keys}
    vec: dict[str, np.ndarray] = {k: centroids[k] for k in keys}

    merged_any = False

    def _merge(src: str, dst: str) -> None:
        nonlocal merged_any
        merged_any = True
        groups[dst].extend(groups.pop(src))
        v = np.mean(np.stack([vec.pop(src), vec[dst]]), axis=0)
        n = float(np.linalg.norm(v))
        vec[dst] = (v / n).astype(np.float32)

    def _best_pair() -> tuple[float, str, str] | None:
        roots = list(vec)
        best = None
        for i in range(len(roots)):
            for j in range(i + 1, len(roots)):
                s = float(np.dot(vec[roots[i]], vec[roots[j]]))
                if best is None or s > best[0]:
                    best = (s, roots[i], roots[j])
        return best

    # 1) 高相似直接合并
    while True:
        pair = _best_pair()
        if not pair or pair[0] < threshold:
            break
        _merge(pair[2], pair[1])

    # 2) 目标人数约束
    if target and target > 0:
        while len(vec) > target:
            pair = _best_pair()
            if not pair:
                break
            _merge(pair[2], pair[1])

    # 3) 碎片簇吸收（只吃"两三句的噪声"，不吃说话少的人）
    changed = True
    floor = max(1, target or 1)  # 设置里指定了人数就绝不少于此数
    while changed and len(vec) > floor:
        changed = False
        for small in sorted(vec, key=lambda k: sum(weights[x] for x in groups[k])):
            members = groups[small]
            cnt = sum(utt_counts[x] for x in members)
            secs = sum(weights[x] for x in members)
            if cnt > absorb_max_utts or secs > absorb_max_seconds:
                continue
            cands = [
                (float(np.dot(vec[small], vec[o])), o)
                for o in vec
                if o != small and sum(weights[x] for x in groups[o]) > max(secs * 2.0, 1.0)
            ]
            cands = [c for c in cands if c[0] >= absorb_thr]
            if cands:
                _merge(small, max(cands)[1])
                changed = True
                break

    if not merged_any and not dropped_any:  # 完全没动时保持入参对象不变（调用方按需判断）
        return centroids, {k: k for k in keys}

    alias: dict[str, str] = {}
    for root, members in groups.items():
        for m in members:
            alias[m] = root
    out = {root: vec[root] for root in vec}
    for u in utts:
        u.cluster = alias.get(u.cluster, u.cluster)
    return out, alias


def match_speakers(centroids: dict[str, np.ndarray], threshold: float) -> dict[str, dict]:
    """余弦相似度匹配已注册声纹。"""
    rows = (
        get_conn()
        .execute("SELECT id, name, color, embedding FROM speakers WHERE embedding IS NOT NULL")
        .fetchall()
    )
    reg = []
    for r in rows:
        emb = np.frombuffer(r["embedding"], dtype=np.float32)
        if emb.size:
            reg.append((r["id"], r["name"], r["color"], emb))

    out: dict[str, dict] = {}
    for cluster, c in centroids.items():
        best = None
        for sid, name, color, emb in reg:
            sim = float(np.dot(c, emb))
            if best is None or sim > best[0]:
                best = (sim, sid, name, color)
        if best and best[0] >= threshold:
            out[cluster] = {
                "speaker_id": best[1],
                "display_name": best[2],
                "confidence": round(best[0], 4),
            }
        elif best:
            out[cluster] = {
                "speaker_id": None,
                "display_name": None,
                "confidence": round(best[0], 4),
            }
    return out


def process_job(job_id: int, should_cancel: Callable[[], bool] | None = None) -> None:
    """执行一个任务。异常会写入 jobs.error 并置为 failed。"""
    load_builtin()
    conn = get_conn()
    job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if job is None:
        return
    file_row = conn.execute("SELECT * FROM files WHERE id=?", (job["file_id"],)).fetchone()
    if file_row is None:
        raise RuntimeError("文件记录不存在")

    cfg = _effective_cfg(job)
    src = file_path(file_row["rel_path"])
    if not src.exists():
        raise RuntimeError(f"源文件不存在: {src}")

    started = time.time()
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    wav = str(TMP_DIR / f"job_{job_id}.wav")
    duration = float(file_row["duration"] or 0) or ffprobe_duration(str(src))

    def cancel_check() -> None:
        if should_cancel and should_cancel():
            raise Canceled()

    def set_progress(stage: str, ratio: float, message: str = "") -> None:
        with tx() as c:
            c.execute(
                "UPDATE jobs SET stage=?, progress=? WHERE id=?",
                (stage, max(0.0, min(1.0, ratio)), job_id),
            )

    try:
        # 1. 转码
        set_progress("convert", 0.01, "转码为 16kHz 单声道")
        cancel_check()
        convert_to_wav(str(src), wav)
        cancel_check()
        if duration < 1.0 or _wav_seconds(wav) < 1.0:
            raise RuntimeError("音频为空或过短（不足 1 秒），已跳过")

        # 2. 识别（含说话人分离）
        set_progress("asr", 0.06, "加载模型")
        engine = get_engine(str(cfg.get("engine", "funasr")))
        stop = threading.Event()
        t0 = time.time()
        rtf = get_metric("rtf_est", 0.06) or 0.06

        def ticker() -> None:
            while not stop.wait(0.8):
                elapsed = time.time() - t0
                est = max(3.0, duration * rtf)
                frac = min(0.92, 0.06 + 0.84 * (elapsed / est))
                set_progress("asr", frac, f"识别中 {int(elapsed)}s")

        th = threading.Thread(target=ticker, daemon=True)
        th.start()
        try:
            result: AsrResult = engine.transcribe(wav, cfg)
        finally:
            stop.set()
            th.join(timeout=2)
        cancel_check()

        real_rtf = (time.time() - t0) / max(duration, 1.0)
        # 几秒钟的片段算出来的 RTF 没有参考价值（模型加载/预热占大头），别污染全局估计
        if duration >= 20:
            set_metric("rtf_est", min(max(0.7 * real_rtf + 0.3 * rtf, 0.01), 5.0))

        # 清掉噪声字符，并把「一个有效字符都没有」的句子丢掉（极短音频常被模型硬凑一句话）
        cleaned = []
        for u in result.utterances:
            u.text = clean_text(u.text)
            if meaningful_chars(u.text) >= 1:
                cleaned.append(u)
        result.utterances = cleaned
        if not result.utterances:
            # 空结果不是"转写成功"：多半是静音、纯音乐或短到 VAD 都不分段
            raise RuntimeError("没有检测到语音：这条录音可能是静音、纯音乐或过短")

        # 3. 声纹质心 + 合并 + 映射
        set_progress("diarize", 0.94, "声纹匹配")
        centroids: dict[str, np.ndarray] = {}
        mapping: dict[str, dict] = {}
        if bool(cfg.get("auto_map_speakers", True)):
            audio, sr = _load_wav(wav)
            centroids = build_centroids(engine, audio, sr, result.utterances, cfg)
            if centroids:
                centroids, _ = merge_similar_clusters(
                    centroids,
                    result.utterances,
                    threshold=float(cfg.get("merge_threshold", 0.72)),
                    absorb_thr=float(cfg.get("absorb_threshold", 0.5)),
                    absorb_max_seconds=float(cfg.get("absorb_max_seconds", 5.0)),
                    target=int(cfg.get("spk_num") or 0) or None,
                )
                mapping = match_speakers(centroids, float(cfg.get("min_speaker_sim", 0.62)))
            del audio

        # 4. 落库
        set_progress("save", 0.98, "保存结果")
        _save_result(
            job_id,
            file_row,
            cfg,
            result,
            {"centroids": centroids, "mapping": mapping},
            duration,
            time.time() - started,
        )
    finally:
        try:
            if not bool(get_setting("keep_wav", False)) and os.path.exists(wav):
                os.unlink(wav)
        except Exception:
            pass


def _effective_cfg(job) -> dict:
    """任务级 options 覆盖全局设置。"""
    from ..db import all_settings

    cfg = dict(all_settings())
    cfg.setdefault("engine", "funasr")
    if job["options"]:
        try:
            cfg.update(json.loads(job["options"]))
        except Exception:
            pass
    return cfg


def _save_result(
    job_id: int,
    file_row,
    cfg: dict,
    result: AsrResult,
    extra: dict,
    duration: float,
    elapsed: float,
) -> None:
    centroids: dict[str, np.ndarray] = extra.get("centroids") or {}
    mapping: dict[str, dict] = extra.get("mapping") or {}

    with tx() as c:
        c.execute("DELETE FROM transcripts WHERE file_id=?", (file_row["id"],))
        cur = c.execute(
            "INSERT INTO transcripts(file_id, duration, model, asr_ms) VALUES(?,?,?,?)",
            (file_row["id"], duration, result.model, int(elapsed * 1000)),
        )
        tid = cur.lastrowid
        rows = []
        for i, u in enumerate(result.utterances):
            rows.append((tid, i, int(u.start_ms), int(u.end_ms), u.cluster, u.text))
        c.executemany(
            "INSERT INTO segments(transcript_id, idx, start_ms, end_ms, speaker, text) "
            "VALUES(?,?,?,?,?,?)",
            rows,
        )

        c.execute("DELETE FROM file_speakers WHERE file_id=?", (file_row["id"],))
        counts: dict[str, list[int]] = {}
        for u in result.utterances:
            counts.setdefault(u.cluster, [0, 0])
            counts[u.cluster][0] += 1
            counts[u.cluster][1] += max(0, u.end_ms - u.start_ms)
        for cluster in sorted(counts):
            m = mapping.get(cluster, {})
            cen = centroids.get(cluster)
            c.execute(
                "INSERT OR REPLACE INTO file_speakers"
                "(file_id, cluster, speaker_id, display_name, confidence, seconds, utt_count, centroid)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (
                    file_row["id"],
                    cluster,
                    m.get("speaker_id"),
                    m.get("display_name"),
                    m.get("confidence"),
                    counts[cluster][1] / 1000.0,
                    counts[cluster][0],
                    cen.astype(np.float32).tobytes() if cen is not None else None,
                ),
            )

        c.execute(
            "UPDATE files SET status='done', duration=?, error=NULL, "
            "updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (duration, file_row["id"]),
        )
        c.execute(
            "UPDATE jobs SET status='done', progress=1.0, stage='done', "
            "finished_at=CURRENT_TIMESTAMP WHERE id=?",
            (job_id,),
        )

    # 额外导出 JSON，便于离线备份与二次开发
    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "file_id": file_row["id"],
            "name": file_row["name"],
            "rel_path": file_row["rel_path"],
            "duration": duration,
            "model": result.model,
            "engine": result.engine,
            "segments": [
                {"start": u.start_ms, "end": u.end_ms, "speaker": u.cluster, "text": u.text}
                for u in result.utterances
            ],
            "speakers": {
                k: {"display_name": v.get("display_name"), "confidence": v.get("confidence")}
                for k, v in mapping.items()
            },
        }
        (OUT_DIR / f"{file_row['id']}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    except Exception as e:
        log.warning("export json failed: %s", e)
