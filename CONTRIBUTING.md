# 贡献指南

感谢愿意一起改进 FunASR Flow。这个项目面向"把自己的通话录音转成文字稿"的个人用户，所以改动优先保证三件事：**不联网、不丢数据、不把简单流程做复杂**。

## 开发环境

```bash
git clone https://github.com/your-name/funasr-flow.git
cd funasr-flow
python -m venv .venv
source .venv/bin/activate        # Windows: .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

- `.[dev]` 只装 `pytest`、`ruff`、`httpx`，不需要下载模型，也不需要 GPU。
- 要真正跑一次转写，再按 README 装 `.[asr]` 与 CUDA 版 PyTorch。
- 前端：`cd frontend && npm i`，开发时 `npm run dev`（5173 端口，`/api` 代理到 8000）。

## 分支与提交

- 从 `main` 切分支：`feat/短描述`、`fix/短描述`、`docs/短描述`、`refactor/短描述`、`chore/短描述`。
- 提交信息用 [Conventional Commits](https://www.conventionalcommits.org/)：`<type>(<scope>): <subject>`，一行说完"改了什么"，正文写"为什么"。
- 一个 PR 只做一件事；格式化、重命名这类纯噪声改动不要混进功能 PR。

| type | 用途 |
| --- | --- |
| `feat` | 新功能（接口、页面、配置项） |
| `fix` | 修 bug |
| `perf` | 性能（转码、推理、查询） |
| `refactor` | 不改行为的重构 |
| `docs` | 文档 |
| `test` | 测试 |
| `build` / `ci` | 依赖、Dockerfile、workflow |
| `chore` | 杂项 |

常见 scope：`api`、`pipeline`、`queue`、`scanner`、`speakers`、`engines`、`frontend`、`docs`。

```text
feat(api): 支持按状态与时长组合筛选文件
fix(queue): 取消排队任务时同步刷新 files.status
docs(models): 补充四川话模型的 License 说明
```

## 代码风格

- Python 3.10+ 语法，`ruff` 配置在 `pyproject.toml`（行宽 100，规则 `E,F,W,I,UP,B`，忽略 `E501`）。
- 提交前至少跑：

```bash
ruff check backend run.py scripts
ruff format backend
```

- 仓库带 `.pre-commit-config.yaml`，推荐启用：

```bash
pip install pre-commit
pre-commit install
pre-commit run --all-files
```

- 前端沿用现有风格：单文件组件、`<script setup>`、2 空格缩进，不引入新的 UI 框架。

## 测试

```bash
python -m pytest -q
```

`tests/` 里的用例只测纯逻辑（时间格式化、说话人命名、目录扫描、队列、导出、声纹簇合并），**不下载模型、不联网、不调用 ffmpeg、不需要 GPU**。新增逻辑请沿用这个约束：临时数据库用 `tests/conftest.py` 里的 `temp_db` fixture，它会 monkeypatch `db.DB_PATH`，不要碰真实的 `data/flow.db`。

需要覆盖 GPU 推理的验证请放到 `scripts/` 下的手工探针脚本，不要塞进 pytest。

## 新增一个识别引擎

引擎是一组"把 wav 变成带说话人标签的句子"的实现，接口在 `backend/app/engines/base.py`，注册表在 `backend/app/engines/registry.py`。内置的 `funasr_engine.py` 可以作为完整参考。

步骤：

1. 在 `backend/app/engines/` 下新建 `your_engine.py`，实现协议里的属性和方法。
2. 用 `@register` 注册，`name` 就是用户在配置里写的引擎名（例如 `funasr`、`whisper`）。
3. 把模块加进 `registry.load_builtin()` 的 import 列表，否则 `get_engine("your")` 会抛 `KeyError`。
4. 通过任务选项选中它：`POST /api/files/batch {"ids": [...], "action": "enqueue", "options": {"engine": "your"}}`。`pipeline` 会把 `options` 合并进配置，再 `get_engine(cfg.get("engine", "funasr"))`；注意 `settings` 表目前没有 `engine` 键，`PATCH /api/settings` 写它会返回 400。
5. 补一条纯逻辑测试（例如注册表能取到、`available()` 在缺依赖时返回 `False`），并在 `docs/models.md` 记录模型来源与 License。

最小骨架（字段与签名以 `base.py` 为准；注意 `AsrResult` 没有 `text`/`runtime_ms` 字段，句子用 `cluster` 而不是 `speaker`）：

```python
from __future__ import annotations

import numpy as np

from .base import AsrResult, Utterance
from .registry import register


@register
class YourEngine:
    name = "your"
    label = "Your Engine（一句话说明）"

    @classmethod
    def available(cls) -> tuple[bool, str]:
        """返回 (是否可用, 原因)。缺依赖时不要抛异常，返回 False 与提示。"""
        try:
            import your_sdk  # noqa: F401
        except Exception as e:  # pragma: no cover - 依赖缺失路径
            return False, f"未安装 your_sdk：{e}"
        return True, ""

    def load(self, cfg: dict) -> None:
        """加载/切换模型。同样参数重复调用应直接复用，不要重复占显存。"""
        self._model = ...  # 例如 AutoModel(...)
        self._device = cfg.get("device", "cpu")

    def transcribe(self, wav_path: str, cfg: dict, progress=None) -> AsrResult:
        """识别一个 16 kHz 单声道 wav，返回逐句结果与每簇声纹。"""
        raw = self._model(wav_path)
        utterances = [
            Utterance(
                start_ms=int(s["start"]),
                end_ms=int(s["end"]),
                text=str(s["text"]).strip(),
                cluster=f"spk{s['spk']}",
            )
            for s in raw["sentences"]
            if str(s.get("text") or "").strip()
        ]
        centroids = {
            f"spk{i}": np.asarray(v, dtype=np.float32) / (np.linalg.norm(v) + 1e-9)
            for i, v in raw["centers"].items()
        }
        return AsrResult(
            utterances=utterances,
            clusters=sorted({u.cluster for u in utterances}),
            centroids=centroids,   # key 必须与句子的 cluster 对得上
            duration=float(raw.get("duration", 0.0)),
            model=str(cfg.get("asr_model", "")),
            engine=self.name,
            language="zh",
        )

    def embed(self, wav_path: str) -> np.ndarray:
        """从 wav 文件提取声纹向量（声纹登记用）。"""
        import soundfile as sf

        audio, _sr = sf.read(wav_path, dtype="float32", always_2d=False)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        return self.embed_audio(audio, {})

    def embed_audio(self, audio: np.ndarray, cfg: dict) -> np.ndarray:
        """从 float32 单声道音频提取声纹向量。

        注意：base.py 的 Protocol 没有声明这个方法，但 pipeline 的质心计算
        与 services/speakers.py 的声纹登记、声纹库匹配都会直接调用它——
        新引擎必须实现，否则说话人功能不可用。
        """
        vec = np.asarray(self._spk_model(audio), dtype=np.float32)
        return vec / (np.linalg.norm(vec) + 1e-9)

    def release(self) -> None:
        """释放显存/模型。换模型前由引擎自己调用。"""
        self._model = None
```

约定与坑：

- `transcribe` 的输入永远是 **16 kHz、单声道、pcm_s16le 的 wav**，转码由 `pipeline.convert_to_wav()` 完成，引擎不要自己调 ffmpeg。
- `utterances` 的 `start_ms/end_ms` 是相对整段音频的毫秒数；`cluster` 用 `spk0`、`spk1` 这样的稳定标签，它同时是 `file_speakers.cluster` 与前端着色的依据。
- `centroids` 的 key 必须与 `cluster` 严格一致（内置引擎发现对不上会整体丢弃质心，避免把 A 的声纹记到 B 名下）；向量归一化前后都行，pipeline 会重新取平均并归一化。没有声纹能力的引擎返回空 dict，聚类合并与声纹匹配会自动跳过。
- `available()` 必须是无副作用的探测，`/api/health` 与设置页的引擎列表会调用它，不要在里下载模型。
- `progress` 参数保留在协议里，但当前 pipeline 不传它——`asr` 阶段进度是用 `rtf_est` 估算的。直接调用引擎时可以自己传，用于调试输出。
- `release()` 不会被 worker 在每个任务结束时强制调用；内置引擎在 `load()` 换模型前自己释放。引擎要长期占显存就保持幂等加载，不要在 `transcribe` 里重建模型。
- 引擎抛出的异常会被 worker 记为任务失败（`files.status = failed`），异常信息进 `jobs.error`；可预期的问题请抛带中文说明的 `RuntimeError`。

## PR 检查清单

提交前对照一遍（`.github/pull_request_template.md` 里也有同样一份）：

- [ ] `ruff check backend run.py scripts` 通过；`ruff format backend` 之后无多余 diff。
- [ ] `python -m pytest -q` 通过；新增逻辑带测试，且测试不下载模型、不联网、不依赖 GPU。
- [ ] 接口有变化时，前端调用已同步，或在 PR 里说明"仅 API、界面后续跟进"。
- [ ] 新增/改名配置项时，`config.py` 的 `DEFAULT_SETTINGS`、设置页、`docs/` 与 README 表格同步。
- [ ] 数据库 schema 变化兼容已有的 `data/flow.db`，或附带迁移脚本与说明。
- [ ] 前端改动跑过 `npm run build`，`frontend/dist` 仍是可用的构建产物。
- [ ] 没有提交录音、转写结果、`data/`、`.cache/`、`CallRecord/`；日志里不含可识别个人的内容。
- [ ] 不引入默认联网上传音频或遥测的行为。

## 文档

- `README.md`：门面，命令与配置必须与代码一致。
- `docs/architecture.md`：进程模型、状态机、数据表、SSE。
- `docs/api.md`：每个接口的方法、参数、返回字段。
- `docs/models.md`：模型清单、License、热词与方言取舍。

改代码时顺手确认文档没有过期；文档里不要写"计划中的按钮"这类与界面不符的描述。
