# FunASR Flow

把手机里的**通话录音**批量转成「谁、什么时候、说了什么」的文字稿。全部在你自己电脑上跑，音频不出本机。

- 批量：选中一整个文件夹，排队转写
- 说话人：自动区分两个人，可以改名、合并
- 校对：网页里逐句改字、点时间码跳着听
- 搜索/导出：搜任意一句话，导出 TXT / Markdown / SRT / JSON

[![CI](https://img.shields.io/badge/CI-GitHub_Actions-2088FF?logo=githubactions&logoColor=white)](.github/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)](pyproject.toml)

## 一、最快跑起来：Docker

装了 Docker Desktop 就行，不用装 Python、不用装 ffmpeg：

```bash
git clone https://github.com/r0ad/funasr-flow.git
cd funasr-flow
docker compose up -d            # 拉取现成镜像并启动（约 720MB 压缩，第一次几分钟）
```

等它就绪，打开 <http://127.0.0.1:8000> → 点右上角「导入录音」→ **选择文件夹** → 勾选要转写的录音 → 「开始转写」。

> 音频是在**浏览器里**读你电脑上的文件再上传的，Docker **不需要挂载任何目录**。

想从当前源码自己构建而不是拉镜像：`docker compose up -d --build`（多等几分钟）。
端口 8000 被占用时：`FLOW_PORT=8011 docker compose up -d`，然后开 <http://127.0.0.1:8011>。
日志：`docker compose logs -f funasr-flow`。

### 有 NVIDIA 显卡（更快）

一条命令就够 —— 它会拉取构建好的 CUDA 镜像，并把显卡交给容器：

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

想自己从源码构建 CUDA 镜像则加 `--build`（要多下约 2.5GB 的 torch cu121）：

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

实测同一台机器（RTX 4060 Ti）：识别本身在显卡上 rtf≈0.01，端到端还要跑「逐句声纹 + 说话人聚类」，那部分吃 CPU，整体大约比纯 CPU 快 3 倍。

前提：宿主机装了 NVIDIA 驱动 + Container Toolkit（Windows 走 WSL2 后端）。先验证一句：
`docker run --rm --gpus all nvidia/cuda:12.1.1-base-ubuntu22.04 nvidia-smi` 能打出显卡信息。

### 想同时跑两个（比如 CPU 一个、GPU 一个，对比效果）

每个实例给不同的名字、端口和数据卷，就不会互相抢容器名和数据库：

```bash
# CPU 实例 → http://127.0.0.1:8000
FLOW_NAME=ff-cpu FLOW_PORT=8000 docker compose -p ff-cpu up -d

# GPU 实例 → http://127.0.0.1:8090
FLOW_NAME=ff-gpu FLOW_PORT=8090 \
docker compose -p ff-gpu -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

`-p` 不同的项目名会让 compose 自动给卷加不同前缀，所以两个实例的资料库天然分开；模型缓存也各下一份（想共用就把其中一个的 `flow-cache` 改成 `external` 卷）。

起来后进「设置」页，显卡那一行应该显示你的显卡型号。**如果显示的是 CPU**，页面会直接告诉你原因（`gpu.hint`），常见三种：

1. 启动时没带 GPU 覆盖文件 —— 用上面带 `-f docker-compose.gpu.yml` 的命令；
2. 镜像是 CPU 版 —— 确认 `docker compose ... config | grep image` 是 `sunzroad/funasr-flow:latest-gpu`；
3. 宿主机没装 Container Toolkit —— 第一条验证命令会失败。

也可以直接用 `docker run`（发布好的两套镜像，master 与 `v*` tag 自动更新）：

```bash
docker pull sunzroad/funasr-flow:latest       # CPU 版
docker pull sunzroad/funasr-flow:latest-gpu   # GPU 版

# GPU 版必须给显卡，否则容器里 torch 看不到卡
docker run -d --name funasr-flow --gpus all -p 8000:8000 \
  -v funasr-data:/data -v funasr-cache:/cache \
  sunzroad/funasr-flow:latest-gpu
```

## 二、不用 Docker：直接装

需要 Python 3.10–3.12 和 ffmpeg（`ffmpeg -version` 有输出即可）。

```bash
git clone https://github.com/r0ad/funasr-flow.git
cd funasr-flow

python -m venv .venv
# Windows: .\.venv\Scripts\Activate.ps1     macOS/Linux: source .venv/bin/activate

# 有 N 卡：按你的 CUDA 版本装 torch（示例 cu124）；纯 CPU 用 https://download.pytorch.org/whl/cpu
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
pip install -e ".[asr]"

python run.py
```

打开 <http://127.0.0.1:8000>。想先自检环境：`funasr-flow doctor`。

## 三、用起来是什么样

| 想做的事 | 在哪 |
| --- | --- |
| 导入录音 | 右上角「导入录音」；也可以直接把音频拖进页面 |
| 开始转写 | 录音库勾选 → 底部「开始转写」 |
| 看进度 | 右下角浮窗（可以拖动，双击回到角落） |
| 改文字 | 进某条录音 → 点句子直接改，回车保存 |
| 跳着听 | 点左侧时间码；播放器支持 0.75×–1.5× |
| 说话人 | 详情页左侧改名字、绑定声纹；分错了点「合并到…」 |
| 找某句话 | 「全文搜索」；点结果跳到对应录音的对应秒 |
| 导出 | 详情页「导出」，或录音库多选后导出（多文件打包 zip） |

第一次转写会下载模型（中文通用约 1GB），之后离线可用。

## 四、常见问题

**转写很慢？**
CPU 大约 1 分钟音频要 20–60 秒；有 GPU 会降到 1–2 秒。设置页能看到当前用的是 CPU 还是 GPU 及原因。

**点了开始转写没反应？**
看左下角是否显示「已连接」，再看右下角进度浮窗。容器里首次请求要等十几秒（加载模型）。

**转写结果是空的 / 显示失败？**
0 秒或纯静音会被判失败并说明原因（`没有检测到语音`）。也确认 `ffmpeg` 可用。

**8000 端口被占用？**
`docker compose` 里把 `"8000:8000"` 改成 `"8010:8000"`，然后访问 8010；直接安装的话 `python run.py --port 8010`。

**录音和文字稿存在哪？**
Docker 默认在命名卷 `flow-data` 里；直接安装在项目的 `data/`：`data/library/` 是音频副本，`data/flow.db` 是数据库。想放在本机可见的文件夹里，把 compose 里 `- flow-data:/data` 换成 `- ./data:/data`。

**能识别方言吗？**
默认通用中文模型。设置里可切到四川话微调模型 `lukeewin01/paraformer-large-sichuan-offline`，实测对西南官话明显更准。设置页还有多方言、一体版、英文等 8 个可选模型，带说明和体积。

**模型是在线拉取吗？**
是，第一次用到时自动下载到缓存目录，之后离线可用。默认从 ModelScope 拉；设置页「模型来源」可切 HuggingFace 或「先 ModelScope 失败再 HuggingFace」，国内访问不了 HF 时加 `HF_ENDPOINT=https://hf-mirror.com`。

## 五、更多

- 想用脚本 / AI 助手调用：`llms.txt`、`docs/llm-usage.md`（HTTP、CLI `funasr-flow api …`、MCP `funasr-flow mcp`）
- 接口细节：`docs/api.md`　架构与设计取舍：`docs/architecture.md`　模型说明：`docs/models.md`
- **开发、构建、发布、配置项、代码约定**：见 [AGENTS.md](AGENTS.md)

MIT License。
