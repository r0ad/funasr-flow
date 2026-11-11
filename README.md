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
docker compose up -d --build
```

等 1–3 分钟（第一次要拉依赖），打开 **http://127.0.0.1:8000** → 点右上角「导入录音」→ **选择文件夹** → 勾选要转写的录音 → 「开始转写」。

> 音频是在**浏览器里**读你电脑上的文件再上传的，Docker **不需要挂载任何目录**。

### 有 NVIDIA 显卡（快 20 倍以上）

```bash
# 只构建一次（约 2.5GB 下载）
docker build --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cu121 \
  -t funasr-flow:cu121 .

docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

前提：宿主机装了 NVIDIA 驱动 + Container Toolkit。验证：`docker run --rm --gpus all nvidia/cuda:12.1.1-base-ubuntu22.04 nvidia-smi` 能打出显卡信息。
起来后进「设置」页，显卡那一行应该显示你的显卡型号；仍是 CPU 时页面会直接告诉你原因。

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

打开 http://127.0.0.1:8000。想先自检环境：`funasr-flow doctor`。

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
默认通用中文模型。设置里可切到四川话微调模型 `lukeewin01/paraformer-large-sichuan-offline`，实测对西南官话明显更准。

## 五、更多

- 想用脚本 / AI 助手调用：`llms.txt`、`docs/llm-usage.md`（HTTP、CLI `funasr-flow api …`、MCP `funasr-flow mcp`）
- 接口细节：`docs/api.md`　架构与设计取舍：`docs/architecture.md`　模型说明：`docs/models.md`
- **开发、构建、发布、配置项、代码约定**：见 [AGENTS.md](AGENTS.md)

MIT License。
