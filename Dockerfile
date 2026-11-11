# syntax=docker/dockerfile:1.7
#
# 多阶段构建：
#   stage 1 (node:20-alpine)    → frontend/dist
#   stage 2 (python:3.11-slim)  → 运行时镜像
#
# ── GPU 说明（重要）──────────────────────────────────────────────────────────
# 本镜像装的是 PyTorch **CPU 版**（见第二阶段的 --index-url .../whl/cpu），
# 适合"一台小服务器/家里旧机器，慢慢跑"的场景，镜像可以跨机器搬运。
# 需要 GPU 加速时，本 Dockerfile 不能直接改一个 ARG 了事，做法是：
#   1) 基础镜像换成 nvidia/cuda 系列，并自带 Python：
#        FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04
#      （该镜像没有 python，需要 apt-get install python3.11 python3.11-venv python3-pip，
#        并把下面的 python / pip 命令换成 python3.11 / python3.11 -m pip）
#   2) torch 安装改为 CUDA 轮子，并把 .[asr] 的 torch 约束保持满足：
#        pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
#   3) 宿主机安装 NVIDIA 驱动 + nvidia-container-toolkit，
#      并在 docker-compose.yml 里打开预留的 GPU 段（deploy.resources.reservations.devices）
#   4) 容器内 device 设置保持默认 cuda:0（backend/app/config.py 的 DEFAULT_SETTINGS）
# 只用 CPU 时，建议在前端设置页把 device 从 cuda:0 改成 cpu，
# 虽然引擎会自动回退（funasr_engine._device 检测 torch.cuda.is_available），但显式设置更清晰。
#
ARG NODE_IMAGE=node:20-alpine
ARG PYTHON_IMAGE=python:3.11-slim

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

# 先拷依赖声明，利用层缓存。LICENSE 必须拷（pyproject 的 license-files 指向它，
# 缺失时 setuptools 会直接报错）；llms.txt 给 /llms.txt 路由用（缺失时有内联兜底）。
COPY pyproject.toml run.py LICENSE llms.txt ./
COPY backend ./backend

# CPU 版 torch/torchaudio：先用 CPU 索引装好，后面 .[asr] 里的 torch>=2.1 已被满足，
# 不会再从 PyPI 拉一份 ~2GB 的 CUDA 版。
RUN python -m pip install --upgrade pip \
 && python -m pip install --index-url https://download.pytorch.org/whl/cpu torch torchaudio \
 && python -m pip install -e ".[asr]" --extra-index-url https://download.pytorch.org/whl/cpu

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
