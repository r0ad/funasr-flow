# FunASR Flow

本地优先的**通话录音批量转写 + 说话人分离**工具：把一整个目录的通话录音丢进去，得到带说话人标签、可校对、可搜索、可导出的文字稿。全程在本机 GPU 上跑，音频不出内网。

[![CI](https://img.shields.io/badge/CI-GitHub_Actions-2088FF?logo=githubactions&logoColor=white)](.github/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)](pyproject.toml)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)]()

## 特性

- **本地离线**：FunASR 模型在本地加载，音频与文字稿只落在本项目的 `data/`，没有上传、没有遥测。
- **批量队列**：一次勾选几百个 `.m4a`，后台串行排队；小文件优先，随时可以停止或取消排队。
- **说话人分离**：一次推理同时拿到句子时间戳、标点与说话人聚类（CAM++ 声纹），不需要额外的分离模型。
- **声纹库**：把某个说话人的声音登记成"人物"，之后所有录音自动匹配并套用名字；支持合并、改名、删除。
- **逐句校对**：网页里点句子直接改文字；点时间码跳转播放，可 0.75×–1.5× 变速。
- **全文搜索**：SQLite FTS5 trigram 索引，直接搜"说过的话"，点结果跳到对应录音的对应秒数。
- **多格式导出**：TXT / Markdown / SRT / JSON，单个文件直接下载，多选打包成 zip。
- **方言模型**：除通用中文模型外，内置四川话微调模型入口（见[模型说明](#模型说明)），适合西南官话通话。
- **单卡串行调度**：一个 worker 线程、一个 GPU、一次一个任务，显存占用可预测，不会互相打架。
- **实时进度**：`GET /api/events` 用 SSE 推送队列与进度，网页上的进度条和"预计还需多久"都来自它。

## 快速开始

前置要求：

| 依赖 | 版本 / 说明 |
| --- | --- |
| Python | 3.10–3.12（3.11 最稳） |
| ffmpeg | 任意较新版本，必须在 `PATH` 里（`ffmpeg -version` 有输出） |
| GPU | 可选。NVIDIA 显卡 + CUDA 版 PyTorch 才走 GPU，否则自动回退 CPU（很慢） |
| Node.js | 只在开发前端时需要，20.x |

### Windows（PowerShell）

```powershell
git clone https://github.com/r0ad/funasr-flow.git
cd funasr-flow

py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1          # 若提示禁止运行脚本：Set-ExecutionPolicy -Scope Process Bypass

# 1) 先按你的 CUDA 版本装 PyTorch（示例 CUDA 12.4；纯 CPU 用 cpu 索引）
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124

# 2) 再装本项目与 ASR 依赖
pip install -e ".[asr]"

# 3) 检查环境（ffmpeg / 依赖 / GPU 是否能识别）
$env:PYTHONUTF8=1
funasr-flow doctor
```

### macOS / Linux（bash）

```bash
git clone https://github.com/r0ad/funasr-flow.git
cd funasr-flow

python3 -m venv .venv
source .venv/bin/activate

# 1) 先装 PyTorch（Linux + CUDA 12.4；macOS 或纯 CPU 去掉 --index-url）
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124

# 2) 再装本项目
pip install -e ".[asr]"

# 3) 检查环境
funasr-flow doctor
```

> **国内镜像注意**：`funasr` 在部分国内镜像上没有，`pip install -e ".[asr]"` 可能报找不到包。此时对这一步显式指定官方源：`pip install -e ".[asr]" -i https://pypi.org/simple`。PyTorch 仍然建议走上面的官方 wheel 索引（体积大、镜像常缺 CUDA 变体）。

### 跑起来

1. 把录音放进项目下的 `CallRecord/`（没有就新建；支持 `.m4a .mp3 .wav .amr .aac .opus .ogg .flac .mp4 .wma .3gp .m4b .webm`）。
   也可以启动后在「设置 → 录音目录」里改成任意目录。
2. 启动服务：

   ```bash
   funasr-flow serve
   # 等价于
   python run.py
   ```

   首次启动会从 ModelScope 下载模型到项目的 `.cache/`（约 2 GB），之后完全离线。
3. 浏览器打开 <http://127.0.0.1:8000>。
4. 点右上角「选录音目录」→ 在弹窗里挑盘符/子目录（有录音的目录会标出「N 个录音」）→「用这个目录」；
   也可以直接点「上传录音」或把音频文件拖进页面，文件会复制到 `data/uploads/` 并自动入库。
5. 转写完成后点某一行「查看转写」：左边播放、右边逐句文字，点文字即可修改。

不想用网页也可以只扫描：



```bash
funasr-flow scan                 # 扫描默认目录
funasr-flow scan --dir D:\Calls   # 扫描指定目录
```

后台常驻参数：

```bash
funasr-flow serve --host 0.0.0.0 --port 8000   # 局域网访问
funasr-flow serve --no-browser                 # 不自动开浏览器
funasr-flow serve --no-worker                  # 只跑 Web，不起内置 worker
funasr-flow worker                             # 单独起一个 worker 进程
funasr-flow serve --reload                     # 开发用，代码热重载
```

`--port` 被占用时，`serve` 会自动往后找 20 个可用端口，并以日志里打印的 `[ready] http://...` 为准。

> 如果 `funasr-flow` 没有进 `PATH`（例如只装了依赖、没装 console script），用 `python run.py serve`、`python run.py scan`、`python run.py doctor` 完全等价。

### 前端开发

网页源码在 `frontend/`（Vue 3 + Vite）。仓库里带了一份已构建的 `frontend/dist`，所以只跑后端也能直接用界面。要改前端：

```bash
cd frontend
npm i
npm run dev          # http://localhost:5173，/api 代理到 http://127.0.0.1:8000
```

- 后端不在 8000 端口时：`FLOW_API=http://127.0.0.1:8010 npm run dev`。
- 构建生产产物：`npm run build`，输出到 `frontend/dist`，`funasr-flow serve` 会自动托管它。

### Docker（可选，CPU 镜像）

```bash
mkdir -p data cache CallRecord
sudo chown -R 10001:10001 data cache      # 容器内以 uid 10001 运行
docker compose up -d --build
# 打开 http://127.0.0.1:8000
```

> **数据库要放在卷里，不要绑定到网络盘。** SQLite 的 WAL 模式要求本地文件系统，把 `/data`
> 绑定到 Windows 的映射盘（如 `H:`）会报 `sqlite3.OperationalError: disk I/O error`。`docker-compose.yml`
> 因此用命名卷 `flow-data` 存数据库，只把录音目录只读绑进去：
>
> ```bash
> docker volume create flow-data
> docker run -d --name funasr-flow -p 8000:8000 \
>   -v flow-data:/data \
>   -v /path/to/CallRecord:/data/CallRecord:ro \
>   sunzroad/funasr-flow:latest
> ```
>
> **想让容器读到宿主机任意目录**：多挂一个只读卷，然后在网页右上角「选录音目录」里选到它即可
> （选目录弹窗列的是**容器内**的文件系统，所以挂载点会出现在里面）：
>
> ```bash
> docker run -d --name funasr-flow -p 8000:8000 \
>   -v flow-data:/data \
>   -v "D:/我的通话录音:/media:ro" \
>   sunzroad/funasr-flow:latest
> # 打开网页 → 选录音目录 → 点 D: 之外的根目录/直接输入 /media → 用这个目录
> ```
> Windows 路径里的盘符用 `D:/...` 或 `/d/...` 都行，记得加引号。
>
> 上传的文件会落在命名卷 `flow-data` 里（`/data/uploads`），不占用宿主机的绑定目录；
> 删除记录时这些上传副本会被一起删掉，扫描进来的原始录音永远不动。

镜像基于 CPU 版 PyTorch，适合跑通流程或小批量；GPU 需要把 `Dockerfile` 换成 CUDA 基镜像并打开 `docker-compose.yml` 里注释掉的 `deploy.resources` 段（见文件头部注释）。

## 目录结构

```text
funasr-flow/
├── backend/app/
│   ├── main.py              # FastAPI 入口、静态托管、内置 worker 线程
│   ├── cli.py               # funasr-flow 命令：serve / scan / worker / doctor
│   ├── config.py            # 路径、DEFAULT_SETTINGS、模型候选
│   ├── db.py                # SQLite 连接（WAL）、建表、settings/metrics 读写
│   ├── worker.py            # worker 主循环（线程内或独立进程）
│   ├── api/                 # files / transcripts / speakers / system 四组路由
│   ├── engines/             # base.py 引擎协议、registry.py 注册表、funasr_engine.py
│   └── services/            # scanner / queue / pipeline / speakers（聚类与声纹）
├── frontend/                # Vue 3 + Vite 源码，dist/ 为构建产物
├── tests/                   # pytest 纯逻辑测试（不下载模型）
├── scripts/                 # 手工探针脚本（probe_pipeline.py 等）
├── docs/                    # architecture.md / api.md / models.md
├── CallRecord/              # 默认录音目录（git 忽略）
├── data/                    # flow.db 与导出 JSON（git 忽略）
├── .cache/                  # 模型缓存与中间 wav（git 忽略）
├── run.py                   # 等价 funasr-flow serve
├── pyproject.toml
├── Dockerfile
└── docker-compose.yml
```

## 配置说明

配置存在 SQLite 的 `settings` 表里，网页「设置」页、`PATCH /api/settings` 都能改，重启后保留。没写过的键回退到 `backend/app/config.py` 的 `DEFAULT_SETTINGS`。

| 键 | 默认值 | 含义 | 怎么改 |
| --- | --- | --- | --- |
| `input_dir` | `<项目根>/CallRecord` | 扫描与播放的录音根目录 | 设置页「录音目录」→ 应用并扫描；或 `POST /api/files/scan` 带 `{"dir": "..."}` |
| `device` | `cuda:0` | 推理设备；CUDA 不可用时引擎自动回退 `cpu` | 设置页「计算设备」 |
| `asr_model` | `paraformer-zh` | ASR 模型别名或 ModelScope id | 设置页「识别模型」（候选来自 `/api/health` 的 `ml.models.asr`） |
| `vad_model` | `fsmn-vad` | 语音端点检测模型 | 设置页「高级」 |
| `punc_model` | `ct-punc` | 标点恢复模型 | 设置页「高级」 |
| `spk_model` | `cam++` | 声纹模型（说话人聚类与声纹库都用它） | 设置页「高级」 |
| `hotwords` | `妈妈 爸爸 哥哥 姐姐 外婆 外公 幺爸 幺妈 舅舅 嬢嬢` | 热词，空格分隔；只对 SeACo 系模型（`paraformer-zh`）生效 | 设置页「热词」 |
| `spk_num` | `0` | 预设说话人数；`0` = 自动判断 | 设置页「说话人数」 |
| `batch_size_s` | `300` | 单批最长音频秒数，越大越快也越吃显存 | 设置页「批处理长度」 |
| `auto_map_speakers` | `true` | 转写后自动与声纹库比对并绑定已有名字 | 设置页 checkbox |
| `min_speaker_sim` | `0.62` | 绑定已有声纹的最小余弦相似度，调高更保守 | 设置页「声纹匹配阈值」 |
| `merge_threshold` | `0.72` | 两个说话人簇余弦 ≥ 此值就视为同一个人并合并 | 设置页暂未暴露，用 `PATCH /api/settings` |
| `absorb_threshold` | `0.45` | 碎片簇被并入大簇的最低相似度 | 同上 |
| `absorb_max_seconds` | `6.0` | 累计时长不超过此值（且 ≤3 句）才算碎片簇 | 同上 |
| `concurrency` | `1` | 并发任务数；当前实现固定单卡串行，此键暂不生效 | 留 `1`（见 `docs/architecture.md`） |
| `keep_wav` | `false` | 是否保留转码后的 16 kHz 中间 wav（`data/`、`.cache/tmp/`） | 设置页 checkbox |
| `auto_scan_minutes` | `0` | 定时自动扫描间隔（分钟），`0` 关闭；到点后台扫描一次 `input_dir`，新文件自动入库 | 留 `0` 手动扫描 |

环境变量（都在 `config.py` 里读取，进程启动时生效）：

| 变量 | 作用 |
| --- | --- |
| `FLOW_DATA_DIR` | 覆盖 `data/`（数据库与导出 JSON） |
| `FLOW_CACHE_DIR` | 覆盖 `.cache/`（模型与临时 wav） |
| `FLOW_WORKER` | `0` = 不启动内置 worker（Web 与 worker 分进程部署时用） |
| `FLOW_LOG_LEVEL` | 应用日志级别，默认 `INFO` |
| `FLOW_UVICORN_LOG` | uvicorn 日志级别，默认 `info` |
| `FLOW_API` | 仅前端开发：Vite 代理的目标地址 |
| `VITE_API_BASE` | 仅前端构建：API 基础路径，默认同源 |
| `MODELSCOPE_CACHE` / `HF_HOME` / `FUNASR_CACHE` | 未设置时由 `config.py` 指向项目内 `.cache/` |

## 模型说明

默认组合是 **SeACo-Paraformer（`paraformer-zh`，带时间戳与热词）+ FSMN-VAD + CT-Punc + CAM++**，首次使用自动下载到 `.cache/modelscope/`。

- `paraformer-zh`：中文通用、速度最快，支持热词（SeACo），日常首选。
- `iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch`：完整版 Paraformer-large，通用性略好，速度和显存占用更高。
- `lukeewin01/paraformer-large-sichuan-offline`：四川话微调模型（205 小时数据）。**模型卡没有声明 License，请仅作个人/研究用途，商用前自行确认授权**；同时模型卡明确说明它不兼容 `spk_model`，不能直接搭配 CAM++ 做说话人区分，切到它之后说话人标注可能不可用。建议先用默认模型跑通，再拿它对比方言识别效果。
- `cam++`：CAM++ 中文声纹，192 维 embedding，用来做句子聚类、跨文件声纹匹配与声纹库。

细节（大小、License、热词机制、方言与通用模型的取舍）见 [`docs/models.md`](docs/models.md)。

## 工作原理

```text
 CallRecord/  (m4a / mp3 / amr ...)
      │
      │  POST /api/files/scan  ──► files 表（idle）
      ▼
 选中文件 ──► POST /api/files/batch {action:"enqueue"}
      │
      ▼
  jobs 表（queued）── worker 循环 claim_next()：小文件优先
      │
      ▼
 ffmpeg 转 16 kHz 单声道 wav（.cache/tmp/job_<id>.wav，非 wav 源才转）
      │
      ▼
 FunASR 一体化推理：SeACo-Paraformer 识别 + 标点 + CAM++ 说话人聚类
（返回逐句时间戳、文本、cluster 标签、每簇声纹质心）
      │
      ▼
 质心合并：≥ 0.72 直接合并 → 按 spk_num 收敛人数 → 吸收碎片簇；
 再与声纹库比对，≥ 0.62 则套用已有名字
      │
      ▼
 落库：transcripts / segments / file_speakers，files.status=done
      │
      ▼
 SSE 推送进度 ──► 网页实时刷新；导出 TXT / Markdown / SRT / JSON
```

## 给脚本 / LLM 用

界面不是唯一入口：同一套能力还提供 HTTP API、CLI 和 MCP server，方便写脚本或直接让 agent 操作
（详见 [docs/llm-usage.md](docs/llm-usage.md)，机器可读索引在 [llms.txt](llms.txt) 与服务端的 `/llms.txt`）。

```bash
# CLI：默认输出人读文本，加 --json 输出原始结构
funasr-flow api list --status idle --json
funasr-flow api enqueue 11 23 27
funasr-flow api state
funasr-flow api transcript 23
funasr-flow api search 排骨
funasr-flow api export 23 -f srt -o 23.srt

# MCP（stdio）：给 Claude Desktop / DSH 等客户端
funasr-flow mcp --server http://127.0.0.1:8000
```

`--server` 默认读 `FLOW_SERVER` 环境变量，再默认 `http://127.0.0.1:8000`。MCP 暴露 10 个工具：
`list_files`、`get_file`、`get_transcript`、`search`、`queue_state`、`enqueue_transcribe`、
`cancel_jobs`、`scan_library`、`list_speakers`、`health`。

### 某条录音的人数识别不准怎么办

打开这条录音的详情页，右上角「人数」选 2（或 3），再点「重新转写」——这次任务的说话人数会固定下来，
聚类会按人数收敛。也可以只调全局默认：设置页「说话人数」。

如果自动结果多出一个人（常见于短通话），在详情页左侧说话人卡片的「合并到…」里并过去即可，
不需要重新转写。

## 常见问题

**显存不足 / 没有 GPU？**
把 `device` 改成 `cpu` 即可跑，但一条 10 分钟通话可能要几分钟到十几分钟。GPU 上如果 OOM，先把 `batch_size_s` 从 `300` 降到 `120` 或 `60`，再考虑换更小的模型。队列本身是串行的，不会叠加显存。

**Windows 控制台报 `UnicodeEncodeError: 'gbk' codec can't encode character`？**
这是 GBK 控制台无法输出 `✔`/`✘` 之类字符。先设 `$env:PYTHONUTF8=1`（cmd 用 `set PYTHONUTF8=1`）再运行 `funasr-flow doctor`；或 `chcp 65001` 切到 UTF-8 代码页。服务本身不受影响。

**m4a / amr 转码失败？**
先确认 `ffmpeg -version` 能跑，且 `ffmpeg` 在 `PATH` 里（Windows 装完要重开终端）。微信的 `.silk` 语音不在支持列表内，需要先转成 wav/mp3。

**说话人被拆得太碎（过聚类）？**
先设 `spk_num` 为真实人数（例如 2），合并阶段会反复合并最相似的一对直到达标。之后还有两道闸：`merge_threshold`（默认 0.72）把余弦足够高的簇直接合并，`absorb_max_seconds` + `absorb_threshold`（默认 6.0 秒 / 0.45）把只有两三句的噪声簇吸收进大簇；
多出来的说话人不影响识别，可以在详情页用「合并到…」手动并掉。`min_speaker_sim`（默认 0.62）只决定"是否套用声纹库里的名字"，不影响聚类。这几个键都能用 `PATCH /api/settings` 改，设置页目前只暴露了 `spk_num` 与 `min_speaker_sim`。

**识别准确率不理想？**
通话录音常见 8 kHz、窄带、有回声，准确率天然低于近场录音。可尝试：把热词换成通话里高频的人名/地名（只对 `paraformer-zh` 生效）；对四川话换方言模型对比；校对时直接改句子，改动会写回数据库并参与导出。

**`pip install` 之后打开网页是 404 / 没有界面？**
仓库把构建好的 `frontend/dist` 一起提交了，源码仓库里直接 `python run.py` 一定有界面。用 `pip install funasr-flow`
（非 editable）从 PyPI 装时不会带上网页资源，此时请用 Docker 镜像，或从源码 `pip install -e .` 后 `cd frontend && npm run build`。

**数据会被上传吗？**
不会。推理、聚类、存储全部在本机完成。唯一需要联网的是首次下载模型；之后可以把 `MODELSCOPE_CACHE` 指向已有缓存离线运行。`data/` 里有全部文字稿，注意别提交到公开仓库（`.gitignore` 已忽略）。

## Roadmap

- [x] 定时自动扫描（`auto_scan_minutes`）
- [ ] 转写结果的纠正学习（把人工改的说话人写回声纹库）
- [x] 设置页暴露 `merge_threshold` / `absorb_threshold` / `absorb_max_seconds`
- [ ] 多 GPU / 多 worker 并行（当前刻意串行）
- [ ] 微信 `.silk` 与更多容器格式解码
- [ ] 波形时间轴、批量重命名说话人、导出 Word

## 许可证

[MIT](LICENSE)。模型权重遵循各自的 License，详见 [`docs/models.md`](docs/models.md)。

## 致谢

- [FunASR](https://github.com/modelscope/FunASR)：Paraformer / SeACo / FSMN-VAD / CT-Punc / CAM++ 的推理框架。
- [ModelScope](https://modelscope.cn)：模型托管与下载。
- [CAM++](https://arxiv.org/abs/2303.00332)：说话人验证模型。
- Vue 3、Vite、FastAPI、SQLite FTS5。

---

## English TL;DR

FunASR Flow is a local-first batch transcription tool for call recordings, built on FunASR (Paraformer + CAM++), FastAPI and SQLite.
Drop your `.m4a` files into `CallRecord/`, run `funasr-flow serve`, open <http://127.0.0.1:8000>, scan the folder and queue everything.
Each job is transcribed with sentence timestamps, punctuation and speaker clustering on your own GPU; jobs run one at a time on a single card.
A speaker library lets you enroll a voice once and have the same person recognized across hundreds of recordings.
Transcripts can be corrected sentence by sentence in the browser, searched with SQLite FTS5, and exported as TXT / Markdown / SRT / JSON.
Audio never leaves the machine; only the first run needs network access to download models into `.cache/`.
License: MIT. Models keep their own licenses — see `docs/models.md`.

```bash
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
pip install -e ".[asr]"
funasr-flow serve   # then open http://127.0.0.1:8000
```
