# 模型说明

FunASR Flow 不做模型训练，只负责调度。所有模型都由 [FunASR](https://github.com/modelscope/FunASR) 的 `AutoModel` 在本地加载，权重来自 ModelScope，首次使用时下载到项目内 `.cache/`。

## 默认组合

一次转写会同时用到四个组件，它们由同一个 `AutoModel` 实例管理：

| 角色 | 默认模型 | 作用 | 可否替换 |
| --- | --- | --- | --- |
| ASR | `paraformer-zh`（SeACo-Paraformer） | 语音转文字，输出逐句时间戳 | 设置页「识别模型」，或任务 `options.asr_model` |
| VAD | `fsmn-vad` | 语音端点检测，切掉长静音 | 设置页「高级」`vad_model` |
| 标点 | `ct-punc` | 恢复标点与大小写 | 设置页「高级」`punc_model` |
| 声纹 | `cam++` | 句子级说话人聚类、跨文件声纹匹配 | 设置页「高级」`spk_model` |

`cam++` 还会被单独加载一次（`funasr_engine._load_sv()`），用于 `embed_audio()`：给一段波形算 192 维声纹，供质心合并、声纹库登记与匹配使用。

## 模型清单

大小是本地缓存目录的实际占用（`du` 口径，含配置文件），未缓存的模型给出的是量级估计。

| 别名 / 配置值 | 用途 | 大小 | License |
| --- | --- | --- | --- |
| `paraformer-zh` | 中文通用 ASR，SeACo 热词，默认首选 | 954 MB | Apache License 2.0 |
| `iic/speech_paraformer-large-contextual_asr_nat-zh-cn-16k-common-vocab8404` | 热词/上下文增强版，识别略慢 | 约 900 MB | Apache License 2.0 |
| `iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch` | 基础对照版，无热词 | 约 850 MB | Apache License 2.0 |
| `iic/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-pytorch` | 一体版：自带 VAD + 标点，启动更快；句子偏长 | 约 1.1 GB | Apache License 2.0 |
| `lukeewin01/paraformer-large-sichuan-offline` | 四川话微调 ASR（205 小时方言数据） | 852 MB | **未声明**（模型卡无 License 行） |
| `dengcunqin/speech_paraformer_large_asr_mtl-16k-common-vocab11666-pytorch` | 多方言通用（粤/闽/吴/川等），热词不生效 | 约 900 MB | Apache License 2.0 |
| `FunAudioLLM/Fun-ASR-Nano-2512` | 7 大方言/多语种，实验性质；时间戳支持未验证 | 约 800 MB | Apache License 2.0 |
| `paraformer-en` | 英文通用 | 约 900 MB | Apache License 2.0 |
| `fsmn-vad` | VAD | 4.4 MB | Apache License 2.0 |
| `ct-punc` | 中文/英文标点恢复（大，最准） | 1.2 GB | Apache License 2.0 |
| `ct-punc-c` | 中文标点（小、快） | 约 300 MB | Apache License 2.0 |
| `cam++` | 说话人验证/声纹（192 维） | 29 MB（`campplus_cn_common.bin` 28,036,335 B） | Apache License 2.0 |

设置页的「识别模型 / 声纹模型 / 标点」下拉就是按这份清单渲染的（数据来自 `backend/app/config.py` 的
`MODEL_CHOICES`，`/api/health` 的 `ml.models` 带出来）；配置值也可以手填清单之外的仓库 id。

补充：

- `paraformer-zh` 是**别名**，FunASR 内部通过 `name_maps_ms` 映射到上面的完整 id；配置里写别名或完整 id 都可以。
- 也支持直接写本地模型目录（FunASR 的 `AutoModel(model="/path/to/model")`），适合内网离线部署。
- 四川话模型的 `configuration.json` 把 `model_name_in_hub.ms` 指向 `iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch`，说明它是 Paraformer-large 的微调权重，而不是独立架构。
- 声纹模型 `cam++` 的 `emb_size=192`、`fbank=80`；`file_speakers.centroid` 与 `speakers.embedding` 存的都是这个维度的 float32。

## 下载与缓存

### 下载来源：ModelScope 优先，HuggingFace 兜底

模型是**首次使用时在线拉取**的，之后走本地缓存、可完全断网。来源由设置项 `model_hub` 控制：

| `model_hub` | 行为 | 说明 |
| --- | --- | --- |
| `ms`（默认） | 只走 ModelScope | 国内直连快；FunASR 的默认渠道 |
| `hf` | 只走 HuggingFace | 部分模型（`FunAudioLLM/*` 等）首发在 HF |
| `auto` | 先 ModelScope，报错后自动换 HuggingFace | 新模型常常一边有一边没有 |

实现上就是把 `hub=` 透传给 `funasr.AutoModel(...)`（FunASR 支持 `ms`/`hf`/`openai`，取值见
`funasr/download/download_model_from_hub.py`）；`hf` 时 HF 官方别名会映射成 `funasr/paraformer-zh`、
`funasr/campplus` 这类 HuggingFace 仓库。

国内访问不了 huggingface.co 时，用镜像端点（标准 `huggingface_hub` 环境变量，本项目原样透传）：

```bash
# Linux / macOS
HF_ENDPOINT=https://hf-mirror.com funasr-flow serve
# Windows PowerShell
$env:HF_ENDPOINT = "https://hf-mirror.com"; funasr-flow serve
# Docker
docker run ... -e HF_ENDPOINT=https://hf-mirror.com sunzroad/funasr-flow:latest
```

`GET /api/health` 的 `ml.hub` 与 `ml.hf_endpoint` 会告诉你当前走哪边、有没有配镜像。

### 缓存目录

```text
MODELSCOPE_CACHE  → <项目根>/.cache/modelscope
HF_HOME           → <项目根>/.cache/huggingface
FUNASR_CACHE      → <项目根>/.cache/funasr
```

这三个环境变量由 `backend/app/config.py` 通过 `os.environ.setdefault` 设置：如果外部已经设了，就以外部为准。`AutoModel(..., disable_update=True)` 关闭了每次启动的版本检查，因此模型齐了之后可以完全断网运行。

预下载（不动数据库、只拉权重）：

```bash
python -c "from funasr import AutoModel; AutoModel(model='paraformer-zh', vad_model='fsmn-vad', punc_model='ct-punc', spk_model='cam++', disable_update=True)"
```

想放到别的盘：

```bash
# Linux / macOS
FLOW_CACHE_DIR=/data/funasr-cache funasr-flow serve
# Windows PowerShell
$env:FLOW_CACHE_DIR = "D:\funasr-cache"; funasr-flow serve
```

换缓存目录后模型要重新下载一遍（缓存不共享时）。

## 热词

热词只对 **SeACo 系模型**（`paraformer-zh`）生效。配置项 `hotwords` 是一行字符串，**用空格分隔**：

```text
妈妈 爸爸 哥哥 姐姐 外婆 外公 幺爸 幺妈 舅舅 嬢嬢
```

- 引擎把整串作为 `hotword=` 传给 `AutoModel.generate()`，由 SeACo 在解码时对热词做偏置，不会改写最终文本的格式。
- 词表越长，偏置越分散；建议只放通话里高频、且通用模型容易听错的人名/地名/方言词，控制在几十个以内。
- 切到 `paraformer`（非 SeACo）或四川话模型时，热词不会报错，但也不起作用。
- 热词是任务级参数：修改后只影响**之后入队**的任务，已完成的转写不会自动重跑。

## 方言模型与通用模型的取舍

四川话模型（`lukeewin01/paraformer-large-sichuan-offline`）的卖点很直接：模型卡自述用 205 小时四川话数据微调，字错率从 10% 降到 6%。但它有三个必须知道的限制：

1. **License 未声明**。仓库与模型卡都没有 License 行，公开的只有 3 个 epoch 的权重（完整 50 epoch 需联系作者）。因此本项目只把它作为"可选、自担风险"的入口：**不要商用、不要再分发权重**，个人自用前也请自行确认授权。
2. **不兼容 `spk_model`**。模型卡明确写了不能直接搭配 CAM++ 做说话人区分。而本项目的说话人聚类、声纹库、逐句说话人标签都依赖 CAM++，所以切到它之后，说话人相关能力可能完全不可用（最坏情况是整段只出一个 `spk0`）。
3. **通用场景未必更好**。它是通话/方言域微调，普通话近场录音可能不如 `paraformer-zh`。

实践建议：

- 先全量用 `paraformer-zh` 跑通流程、建立声纹库，确认界面与导出可用。
- 如果方言句子的识别质量确实是瓶颈，挑 5–10 个最典型的文件，用两个模型各跑一遍，人工对比字错率，再决定是否整体切换。
- 切换前把 `spk_num` 设为 `0`，并且明白"这一批可能没有说话人标签"；切换后如果 `segments` 全是 `spk0`，这是模型限制，不是流水线 bug。

## 资源占用

- 默认组合（SeACo + VAD + CT-Punc + CAM++）在 16 GB 显存的消费级卡上余量充足；`batch_size_s` 从 300 往上调会明显抬高峰值显存，OOM 时优先降它。
- CPU 也能跑，只是 `rtf` 会从 0.0x 变成 0.3–1.0 量级（视 CPU 而定），980 个文件不建议在 CPU 上全量跑。
- 模型只在 worker 内加载一次；切换 `asr_model`/`device` 会触发重新加载（旧实例先 `release()` 并 `torch.cuda.empty_cache()`）。
- 队列是串行的（`docs/architecture.md`），不会出现两个任务各加载一份模型的情况。

## License 汇总与合规

| 组件 | License | 说明 |
| --- | --- | --- |
| FunASR 代码 | MIT | 本项目通过 pip 依赖，不复制其代码 |
| SeACo-Paraformer / Paraformer-large | Apache-2.0 | 模型卡声明 |
| FSMN-VAD | Apache-2.0 | 模型卡声明 |
| CT-Punc | Apache-2.0 | 模型卡声明 |
| CAM++ | Apache-2.0 | 模型卡声明 |
| 四川话微调模型 | 未声明 | 仅个人/研究用途，禁止再分发与商用 |
| 本项目代码 | MIT | 见 `LICENSE` |

权重在运行时下载，不随本仓库分发；如果你的产品要内置权重，请按上表逐个确认 License 与署名要求。
