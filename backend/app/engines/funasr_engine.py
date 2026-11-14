"""FunASR 引擎实现：Paraformer 系列 + FSMN-VAD + CT-Punc + CAM++ 说话人分离。

这是默认引擎，全部本地推理，不联网（模型首次下载除外）。
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Any

import numpy as np

from .base import AsrResult, ProgressCb, Utterance
from .registry import register

log = logging.getLogger(__name__)


def _iter_centers(centers: Any):
    """把 FunASR 各种质心形态统一成 (标签, 向量) 迭代。

    可能是 None / dict{"spk0": tensor} / ndarray(n,192) / list[ndarray]。
    ndarray 的行序与聚类标签一致（label 从 0 连续编号）。
    """
    if centers is None:
        return
    if isinstance(centers, dict):
        for k, v in centers.items():
            if isinstance(k, str) and k.startswith("spk"):
                k = k[3:]
            try:
                yield int(k), v
            except (TypeError, ValueError):
                continue
        return
    if isinstance(centers, np.ndarray):
        rows = 1 if centers.ndim == 1 else centers.shape[0]
        for i in range(rows):
            yield i, centers[i] if centers.ndim > 1 else centers
        return
    if isinstance(centers, list | tuple):
        for i, v in enumerate(centers):
            yield i, v


def _as_float32_numpy(x: Any) -> np.ndarray:
    """把 torch.Tensor / list / ndarray 统一成 float32 numpy 数组。"""
    if hasattr(x, "detach"):
        x = x.detach().cpu().numpy()
    return np.asarray(x, dtype=np.float32)


def _patch_cluster_backend() -> None:
    """修 FunASR 处理长通话时必崩的问题（幂等）。

    `ClusterBackend.forward` 拿到的 X 是 `spk_embedding.cpu()` 的 torch.Tensor。
    声纹条数 < 2048 时走 sklearn 的谱聚类（内部会自己转 numpy），所以短录音没事；
    ≥ 2048 条时改走 UMAP+HDBSCAN，而 umap 会读 `X.dtype` 交给 numpy 判断，
    直接抛 `TypeError: Cannot interpret 'torch.float32' as a data type`。
    30 分钟以上的通话（语料里有一批 33–35 分钟）必然踩到。

    这里把入口包一层，先转成 numpy float32 再交给原实现，三条聚类路径都能正常跑。
    """
    try:
        from funasr.models.campplus.cluster_backend import ClusterBackend
    except Exception:  # 没装 funasr / 版本没有这个类：静默跳过
        return
    if getattr(ClusterBackend, "_flow_np_patched", False):
        return
    original = ClusterBackend.forward

    def forward(self, X, **params):  # noqa: N807 - 与原方法同名便于排查
        return original(self, _as_float32_numpy(X), **params)

    ClusterBackend.forward = forward
    ClusterBackend._flow_np_patched = True
    log.debug("已给 FunASR ClusterBackend 打 numpy 兼容补丁")


@register
class FunAsrEngine:
    name = "funasr"
    label = "FunASR（Paraformer + CAM++，本地离线）"

    def __init__(self) -> None:
        self._model = None
        self._sv = None
        self._sig: tuple | None = None
        self._hub_sig: str | None = None
        self._sv_sig: str | None = None

    # ------------------------------------------------------------------ 可用性
    @classmethod
    def available(cls) -> tuple[bool, str]:
        try:
            import funasr  # noqa: F401
            import torch  # noqa: F401
        except Exception as e:  # pragma: no cover - 环境问题
            return False, f"缺少依赖: {e}"
        return True, ""

    # ------------------------------------------------------------------ 加载
    def _device(self, cfg: dict) -> str:
        dev = str(cfg.get("device") or "cuda:0")
        try:
            import torch

            if dev.startswith("cuda") and not torch.cuda.is_available():
                return "cpu"
        except Exception:
            return "cpu"
        return dev

    def _hub(self, cfg: dict) -> str:
        """模型下载来源：ms（ModelScope，默认）/ hf（HuggingFace）。

        `auto` 由 `_hub_auto()` 处理：先按 ms 试，失败再换 hf。
        """
        hub = str(cfg.get("model_hub") or "ms").strip().lower()
        return "hf" if hub in {"hf", "huggingface"} else "ms"

    def _hub_auto(self, cfg: dict) -> bool:
        return str(cfg.get("model_hub") or "ms").strip().lower() == "auto"

    def _build_model(self, sig: tuple, hub: str):
        from funasr import AutoModel

        _patch_cluster_backend()

        return AutoModel(
            model=sig[0],
            vad_model=sig[1],
            punc_model=sig[2],
            spk_model=sig[3],
            # punc_segment: 按标点句归属说话人，粒度最细；缺 punc 时 FunASR 会自动回退 vad_segment
            spk_mode="punc_segment",
            max_num_spks=15,
            device=sig[4],
            hub=hub,
            disable_update=True,
            log_level="ERROR",
            ncpu=4,
        )

    def load(self, cfg: dict) -> None:
        hub = self._hub(cfg)
        sig = (
            cfg.get("asr_model", "paraformer-zh"),
            cfg.get("vad_model", "fsmn-vad"),
            cfg.get("punc_model", "ct-punc"),
            cfg.get("spk_model", "cam++"),
            self._device(cfg),
        )
        if self._model is not None and sig == self._sig and hub == self._hub_sig:
            return

        self.release()
        try:
            self._model = self._build_model(sig, hub)
        except Exception as e:
            # auto：ModelScope 上没有 / 网络不通时自动换 HuggingFace（前提是用户选过 auto）
            if hub != "ms" or not self._hub_auto(cfg):
                raise
            log.warning("ModelScope 加载失败（%s），改试 HuggingFace", e)
            hub = "hf"
            self._model = self._build_model(sig, hub)
        self._sig = sig
        self._hub_sig = hub
        log.info("loading funasr models %s hub=%s", sig, hub)

    def _load_sv(self, cfg: dict) -> Any:
        spk = str(cfg.get("spk_model", "cam++"))
        dev = self._device(cfg)
        hub = self._hub(cfg)
        sig = f"{spk}|{dev}|{hub}"
        if self._sv is not None and self._sv_sig == sig:
            return self._sv
        from funasr import AutoModel

        self._sv = AutoModel(model=spk, device=dev, hub=hub, disable_update=True, log_level="ERROR")
        self._sv_sig = sig
        return self._sv

    # ------------------------------------------------------------------ 转写
    def transcribe(self, wav_path: str, cfg: dict, progress: ProgressCb | None = None) -> AsrResult:
        self.load(cfg)
        # funasr 内部会把 input 原样塞进 torch 张量，Path 对象会报
        # "expected Tensor as element 1 in argument 0, but got WindowsPath"
        wav_path = str(wav_path)
        if progress:
            progress("asr", 0.0, "模型推理中")

        # 电话录音静音多，关掉 VAD 合并以保留细粒度
        kwargs: dict[str, Any] = {
            "batch_size_s": int(cfg.get("batch_size_s", 300)),
            "merge_vad": False,
            "return_spk_res": True,
            "return_spk_center": True,
            "sentence_timestamp": True,
        }
        hot = str(cfg.get("hotwords") or "").strip()
        if hot:
            kwargs["hotword"] = hot
        preset = int(cfg.get("spk_num") or 0)
        if preset > 0:
            kwargs["preset_spk_num"] = preset

        res, centers = self._generate(wav_path, kwargs)
        if progress:
            progress("asr", 1.0, "推理完成")

        item = res
        utterances: list[Utterance] = []
        raw = item.get("sentence_info") or []
        if raw:
            for s in raw:
                text = str(s.get("text") or "").strip()
                if not text:
                    continue
                spk = s.get("spk", 0)
                utterances.append(
                    Utterance(
                        start_ms=int(s.get("start", 0)),
                        end_ms=int(s.get("end", 0)),
                        text=text,
                        cluster=f"spk{spk}",
                    )
                )
        else:  # 退化：无说话人信息时按整段文本输出
            text = str(item.get("text") or "").strip()
            ts = item.get("timestamp") or []
            start = int(ts[0][0]) if ts else 0
            end = int(ts[-1][1]) if ts else 0
            if text:
                utterances.append(Utterance(start_ms=start, end_ms=end, text=text))

        centroids: dict[str, np.ndarray] = {}
        # FunASR 不同版本/聚类路径给出的质心形态不同：
        #   dict {"spk0": tensor} | ndarray (n_spk, 192) | list[ndarray]
        for k, v in _iter_centers(centers):
            arr = (
                v.detach().float().cpu().numpy()
                if hasattr(v, "detach")
                else np.asarray(v, dtype=np.float32)
            )
            arr = np.asarray(arr, dtype=np.float32).reshape(-1)
            n = float(np.linalg.norm(arr))
            if arr.size and n > 0:
                centroids[f"spk{k}" if isinstance(k, int) else str(k)] = arr / n

        # 质心标签必须能对上句子的 spk，否则宁可不用（避免把 A 的声纹记到 B 名下）
        labels = {
            int(u.cluster[3:])
            for u in utterances
            if u.cluster.startswith("spk") and u.cluster[3:].isdigit()
        }
        got = {int(c[3:]) for c in centroids if c.startswith("spk") and c[3:].isdigit()}
        if centroids and labels and not labels <= got:
            log.warning(
                "声纹质心标签与句子标签不一致，丢弃质心: labels=%s centers=%s",
                sorted(labels),
                sorted(got),
            )
            centroids = {}

        return AsrResult(
            utterances=utterances,
            clusters=sorted({u.cluster for u in utterances}),
            centroids=centroids,
            model=str(cfg.get("asr_model", "")),
            engine=self.name,
            language="zh",
        )

    @staticmethod
    def _is_kwargs_error(e: TypeError) -> bool:
        """判断 TypeError 是不是"该版本不接受这个参数"（否则不该降级重试）。"""
        msg = str(e).lower()
        return "unexpected keyword" in msg or "unexpected argument" in msg

    def _generate(self, wav_path: str, kwargs: dict[str, Any]):
        """兼容不同 FunASR 版本的参数集：逐步降级而不是直接失败。"""
        attempts = [
            dict(kwargs),
            {k: v for k, v in kwargs.items() if k != "preset_spk_num"},
            {k: v for k, v in kwargs.items() if k not in {"preset_spk_num", "hotword"}},
            {"batch_size_s": kwargs["batch_size_s"]},
        ]
        last: Exception | None = None
        for kw in attempts:
            try:
                res = self._model.generate(input=wav_path, **kw)
                item = res[0] if isinstance(res, list) else res
                centers = item.get("spk_embedding_center") if isinstance(item, dict) else None
                return item, centers
            except TypeError as e:  # 只有"参数不被该版本接受"才值得降级重试
                if not self._is_kwargs_error(e):
                    raise
                last = e
                continue
        raise RuntimeError(f"FunASR 推理失败: {last}")

    # ------------------------------------------------------------------ 声纹
    def embed(self, wav_path: str) -> np.ndarray:
        import soundfile as sf

        audio, _sr = sf.read(wav_path, dtype="float32", always_2d=False)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        from ..db import all_settings

        return self.embed_audio(audio, all_settings())

    def embed_audio(self, audio: np.ndarray, cfg: dict) -> np.ndarray:
        """对一段 16k 单声道 float32 波形提取 192 维声纹。"""
        sv = self._load_sv(cfg)
        audio = np.ascontiguousarray(audio, dtype=np.float32)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if audio.size < 8000:  # <0.5s 直接补零，避免模型报错
            audio = np.pad(audio, (0, 8000 - audio.size))
        try:
            res = sv.generate(input=audio)
        except Exception:
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            tmp.close()
            try:
                import soundfile as sf

                sf.write(tmp.name, audio, 16000)
                res = sv.generate(input=tmp.name)
            finally:
                os.unlink(tmp.name)
        item = res[0] if isinstance(res, list) else res
        emb = item["spk_embedding"]
        if hasattr(emb, "detach"):
            emb = emb.detach().float().cpu().numpy()
        emb = np.asarray(emb, dtype=np.float32).reshape(-1)
        n = np.linalg.norm(emb)
        return emb / n if n > 0 else emb

    # ------------------------------------------------------------------ 释放
    def release(self) -> None:
        self._model = None
        self._sv = None
        self._sig = None
        self._sv_sig = None
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
