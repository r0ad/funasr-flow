# syntax=docker/dockerfile:1.7
#
# 多阶段构建：
#   stage 1 (node:20-alpine)    → frontend/dist
#   stage 2 (python:3.11-slim)  → 运行时镜像
#
# ── GPU：两种镜像，一个 ARG 切换 ─────────────────────────────────────────────
# 默认构建出来的是 **CPU 版**（torch 从 whl/cpu 装），任何机器都能跑；
# 想用上宿主机的 NVIDIA 显卡，构建时换 CUDA 轮子即可（镜像会大 3~4 GB）：
#
#   docker build -t funasr-flow:0.1.0-gpu \
#     --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cu121 .
#   docker run --gpus all -p 8000:8000 -v funasr-data:/data -v funasr-cache:/cache funasr-flow:0.1.0-gpu
#
# 宿主机前置条件：装好 NVIDIA 驱动 + NVIDIA Container Toolkit（WSL2 里也能用），
# 用 `docker run --rm --gpus all nvidia/cuda:12.1.1-base-ubuntu22.04 nvidia-smi` 先验证。
# CUDA 版 torch 轮子会把需要的 cuda/cudnn 运行库一起装进镜像，所以基础镜像仍用 python:3.11-slim。
# 容器里没加 --gpus all 时 torch.cuda.is_available() 为 False，引擎会自动回退 CPU
# （funasr_engine._device），/api/health 的 gpu.hint 也会说明原因。
#
ARG NODE_IMAGE=node:20-alpine
ARG PYTHON_IMAGE=python:3.11-slim
# CPU：https://download.pytorch.org/whl/cpu ；CUDA 12.1：https://download.pytorch.org/whl/cu121
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu

# ================================================================ stage 1: 前端
FROM ${NODE_IMAGE} AS frontend-build

WORKDIR /build

# 这里复制整个构建上下文（.dockerignore 已排除 data/、.cache/、CallRecord/ 等重型目录）。
# 不用 `COPY frontend ./frontend` 是因为前端目录可能还没提交，那样 COPY 会直接失败；
# 用整个上下文 + 下面的条件分支，可以保证前端缺位时镜像仍然可构建。
COPY . /build

RUN set -eux; \
    if [ -f frontend/package.json ]; then \
        cd frontend; \
        if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; \
        else npm install --no-audit --no-fund; fi; \
        npm run build; \
        test -f dist/index.html; \
    else \
        echo "!! frontend/package.json 不存在：跳过前端构建，镜像只提供 API" \
             "（浏览器访问 / 会提示前端尚未构建）"; \
        mkdir -p frontend/dist; \
    fi

# ================================================================ stage 2: 运行时
FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FLOW_DATA_DIR=/data \
    FLOW_CACHE_DIR=/cache \
    FLOW_LOG_LEVEL=INFO \
    MODELSCOPE_CACHE=/cache/modelscope \
    HF_HOME=/cache/huggingface

# ffmpeg/ffprobe：转码与时长探测（services/pipeline.convert_to_wav、ffprobe_duration）
# libgomp1     ：torch CPU 轮子的 OpenMP 运行库
# libsndfile1  ：soundfile 的兜底系统库（新版 wheel 通常已自带）
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ffmpeg libgomp1 libsndfile1 ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先拷依赖声明，利用层缓存。LICENSE / README.md 必须拷（pyproject 的 license-files、readme
# 指向它们，缺失时 setuptools 会报错或产生空描述）；llms.txt 给 /llms.txt 路由用（缺失时有内联兜底）。
COPY pyproject.toml run.py README.md LICENSE llms.txt ./
COPY backend ./backend

# torch/torchaudio 先按 TORCH_INDEX_URL 装好（默认 CPU 版），后面 .[asr] 里的 torch>=2.1
# 已被满足，不会又去 PyPI 拉一份大轮子。想用 GPU 就 --build-arg TORCH_INDEX_URL=.../cu121
RUN python -m pip install --upgrade pip \
 && python -m pip install --index-url ${TORCH_INDEX_URL} torch torchaudio \
 && python -m pip install -e ".[asr]"

# 前端产物：FastAPI 在此路径托管静态文件（config.FRONTEND_DIST = ROOT/frontend/dist）
COPY --from=frontend-build /build/frontend/dist ./frontend/dist

# 非 root 运行。uid 固定为 10001，方便宿主机 chown 数据目录（见 docker-compose.yml 注释）。
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin flow \
 && mkdir -p /data /cache \
 && chown -R flow:flow /data /cache /app \
 && ln -s /data/CallRecord /app/CallRecord

USER flow

# 数据卷：/data 放 SQLite 与导出结果，/cache 放模型缓存（数 GB，务必持久化）
VOLUME ["/data", "/cache"]
EXPOSE 8000

# 健康检查走 /api/health（backend/app/api/system.py）
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

# 命令行入口见 pyproject.toml [project.scripts] -> backend.app.cli:main
CMD ["funasr-flow", "serve", "--host", "0.0.0.0", "--no-browser"]
