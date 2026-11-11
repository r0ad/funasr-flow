# AGENTS.md

FunASR Flow：把手机通话录音批量转成「带时间轴 + 说话人标签」的文字稿。FastAPI 后端 + Vue3 前端 + SQLite，
全部本机离线运行（模型首次下载除外）。**面向家人/朋友通话录音，中文方言场景优先**。

技术栈：Python 3.10+ / FastAPI / SQLite（含 FTS5）/ FunASR（Paraformer + FSMN-VAD + CT-Punc + CAM++）/ Vue 3 + Vite。

## 常用命令

```bash
# 安装（默认只装 Web 层；转写需要 asr extra）
pip install -e ".[dev]"           # 开发
pip install -e ".[asr]"           # 转写引擎（torch/funasr/modelscope，体积大）

# 跑起来（默认 http://127.0.0.1:8000，会自动建库、起内置 worker）
python run.py                     # 等价于 funasr-flow serve
python run.py --port 8010 --no-browser
funasr-flow doctor                # 环境自检：ffmpeg / torch / CUDA / funasr

# 导入与队列（命令名保留 scan，动作是复制进资料库）
funasr-flow scan --dir CallRecord
curl -X POST localhost:8000/api/files/scan -H 'Content-Type: application/json' -d '{"dir":"D:/通话录音"}'
curl -s localhost:8000/api/files/library
curl -X POST localhost:8000/api/files/batch -H 'Content-Type: application/json' \
     -d '{"ids":[1,2,3],"action":"enqueue"}'

# 前端
cd frontend && npm install && npm run build     # 产物 frontend/dist，后端自动托管
cd frontend && npm run dev                      # 5173，/api 代理到 8000

# 质量门
ruff check backend tests && ruff format --check backend tests
pytest -q

# 给 agent / 脚本用（JSON 输出）
funasr-flow api list --status done --json
funasr-flow api transcript 23
funasr-flow mcp                   # MCP stdio server，工具见 docs/llm-usage.md
```

Windows 上必须 `PYTHONIOENCODING=utf-8`，否则中文日志在 GBK 控制台会 `UnicodeEncodeError`。

## 目录

```
backend/app/
  config.py           路径与 DEFAULT_SETTINGS（唯一改动默认值的地方）
  db.py               SQLite 建表 + all_settings/get_setting/tx 等小工具
  worker.py           队列消费循环（串行，GPU 只允许 1 并发）
  cli.py / cli_client.py / mcp_server.py   CLI、HTTP 客户端、MCP server
  engines/            base.py(Protocol) + registry.py + funasr_engine.py
  services/           library / scanner / queue / pipeline / speakers
  api/                files / transcripts / speakers / system
frontend/src/         api.js / store.js / router.js / views / components
docs/                 architecture / api / models / llm-usage
```

## 领域规则（改代码前必读）

- **GPU 串行**：`concurrency` 固定 1。不要加线程池/多进程跑推理，显存会炸（三模型 fp32 约 2.5–4 GB）。
- **模型缓存必须留在项目内**：`config.py` 用 `os.environ.setdefault` 把 MODELSCOPE_CACHE/HF_HOME/FUNASR_CACHE
  指到 `.cache/`。不要改成用户目录，C 盘会满。
- **音频统一先转 16k 单声道 wav**：`pipeline.convert_to_wav`（`ffmpeg -ac 1 -ar 16000 -c:a pcm_s16le`）。
  funasr 内部解码 m4a 在 Windows 上不可靠，别绕过这步。
  转码后时长 < 1.0 s 直接判失败（`音频为空或过短（不足 1 秒），已跳过`）；ASR 文本落库前用 `pipeline.clean_text()` 清孤立代理对/控制符，无有效字符的句子丢弃，全丢时按「没有检测到语音」判失败。
- **语音模型是方言相关的**：默认 `paraformer-zh`（通用）；`lukeewin01/paraformer-large-sichuan-offline`
  对四川话明显更准（实测同一段通话：通用模型出「甘肃多多嘛啊路排不多」，四川模型出「准备给就要给你们寄过来的时候没寄」）。
  换 ASR 模型时注意：**只有 SeACo 系（含 `paraformer-zh`）支持 `hotword`**，且 hotword 是空格分隔、无权重语法。
- **说话人分离依赖 punc + 字符时间戳**：`spk_mode="punc_segment"` 需要 punc 模型且 ASR 带 timestamp，
  否则 FunASR 静默回退 `vad_segment`（电话录音里一句可长达 30s，归属会糊）。不要为了省内存关掉 punc。
- **过聚类是常态，靠三步收拾**（`pipeline.merge_similar_clusters`）：高相似合并（`merge_threshold` 0.72）→
  目标人数约束（设置 `spk_num`，0=自动）→ 碎片簇吸收（只有 ≤3 句、累计 ≤ `absorb_max_seconds` 且相似度
  ≥ `absorb_threshold` 的簇才会被吸收）。吸收刻意保守：宁可多留一个簇让用户在详情页「合并到…」手动收拾，
  也不能把只说了几句的真实说话人并进主说话人。
  实测 2 人通话会被 FunASR 切成 3 簇，第三簇往往只有 1–2 句噪声。
- **质心标签必须自证**：`spk_embedding_center` 可能是 dict / ndarray / list，用 `_iter_centers` 归一；
  标签和句子 spk 对不上时宁可丢弃质心，也不能把 A 的声纹记到 B 头上。
- **音频一律进资料库**（`config.LIBRARY_DIR` = `data/library`，容器里是 `/data` 卷下的 `library/`）：外部目录只是
  **导入来源**，`library.import_one()` 把音频复制进来，数据库只记资料库里的绝对路径。不要退回「原地扫描外部目录」，
  否则一换挂载点/目录就会把已有录音全判成 `missing`（旧实现踩过：978 个文件一次性误判）。
- **`rel_path` 一律存绝对路径**（`/` 分隔）：导入（`library.import_one`）、上传、`scanner.abs_key` 都按这个写；
  `missing` 只在「重扫资料库」时判定，且只对落在本次目录范围内的记录，文件回来了会自动恢复 `idle`。
  `source_path` 记导入来源的绝对路径（上传为 NULL），配合 `size` 查重：同来源同大小 → `skipped`，大小变了 →
  覆盖资料库里的副本、更新记录并删掉旧 `transcripts`。改路径语义要同时改 `library.file_path` / `scanner.scan` /
  `api/deps.resolve_audio` / 删除时的 `purge`（只删资料库里的副本，外部原文件永远不动）。
- **导入在后台线程，同时只允许一个任务**：`library.start_import()` / `import_dir()`，进度在 `library._state`，
  经 `GET /api/state` 的 `import` 与 `GET /api/files/library` 暴露；重复触发返回 409，取消走
  `POST /api/files/import/cancel`（协作式，处理下一个文件前停）。单个坏文件只计 `failed`，不中断整批。
  注意 `added` 包含被覆盖的文件，`import_dir()` 没有独立的 `replaced` 键。
- **浏览目录不限制范围**：`GET /api/browse` 直接列本机文件系统（这是本地单机工具的定位），
  只挡无权限（403）和非法路径（400）；Windows 盘符靠 `_roots()` 枚举，POSIX 给 `/` 和 `~`。
- **容器里只能浏览挂载点**：`api/system.py` 用 `/.dockerenv` 判断 Docker，把 `/media`、`/mnt`、`/data` 和整盘约定 `/host/<盘符>` 列进 `roots` 最前面，并返回 `container`/`mounts`/`hint` 给 DirPicker 显示说明与命令生成。别试图在容器里读宿主机路径，只能提示用户加 `-v` 重挂；Windows 上 SQLite 也必须放命名卷（见 README Docker 段）。
- **容器 GPU 要 cu121 构建 + `--gpus all`**：`Dockerfile` 的 `ARG TORCH_INDEX_URL` 默认是 CPU 版；要 GPU 用
  `docker build --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cu121 .`，运行加 `--gpus all`。
  宿主机需 NVIDIA 驱动 + Container Toolkit，先跑
  `docker run --rm --gpus all nvidia/cuda:12.1.1-base-ubuntu22.04 nvidia-smi` 验证。
  `/api/health` 的 `gpu.hint` 会说清是镜像 CPU 版、没加 `--gpus all`，还是宿主机缺驱动（`gpu.torch_cuda` 即 `torch.cuda.is_available()`）。
- **搜索**：FTS5 `trigram` 分词，查询词 < 3 字必须走 LIKE 回退（`api/transcripts.py`），不要改成分词器。
- **导出文件名**：中文文件名必须用 RFC 5987 `filename*=UTF-8''…`，`latin-1` 会乱码。

## 代码风格

- 注释与用户可见文案用中文；标识符用英文。`from __future__ import annotations` + 类型注解。
- 行宽 100（ruff）。不要引入 requests/httpx：HTTP 客户端用标准库 `urllib.request`。
- 新引擎：实现 `engines/base.py` 的 `AsrEngine` Protocol 并用 `@register` 注册，不要在 pipeline 里写 `if model == ...`。
- 新配置项：先加到 `config.DEFAULT_SETTINGS`（`/api/settings` 会按默认值类型强转并校验键名），再在代码里
  `cfg.get("key", default)` 读取；**不要**散落硬编码常量。
- 新表/字段：改 `db.py` 的 `SCHEMA`，并保证老库可平滑升级（`init_db` 里用 `ALTER TABLE` 兜底）。
- 前端：视图内不直接 `fetch`，一律走 `src/api.js`；全局状态走 `store.js`；样式用 `style.css` 的 CSS 变量，不要写行内颜色。

## 测试

- `pytest -q` 必须全绿且**不联网、不下载模型、不调用 ffmpeg**（`tests/conftest.py` 用临时库替换 `db.DB_PATH`）。
- 纯逻辑优先测：`_fmt_clock`/`scan`/`enqueue`/`claim_next`/`export`/`merge_similar_clusters`；资料库导入（`library.import_one`/`import_dir`/`unique_target`）用临时库 + 临时目录离线测，不要碰真实 `data/`。
- 改动推理路径（engines/、pipeline.py）时，至少手工跑通一条真实录音并在 PR 里贴出转写片段与耗时。

## 边界

**Always**：改完跑 `ruff check backend tests && pytest -q`；新配置项写进 `DEFAULT_SETTINGS`；提交前确认 `.gitignore`
没把 `CallRecord/`、`data/`、`.cache/` 里的隐私数据带进来。

**Ask first**：改数据库 schema 或删除 `data/` 内容；换默认 ASR/VAD/PUNC/SPK 模型；把 `concurrency` 调到 > 1；
新增第三方依赖（尤其需要联网下载的）。

**Never**：commit 真实通话录音、转写文本、`data/flow.db`、模型权重、`.cache/`；在日志里打印完整转写文本或电话号码；
把用户目录当模型缓存；绕过 ffmpeg 直接喂 m4a 给 funasr。

## 提交与 PR

- Conventional Commits：`feat:` / `fix:` / `docs:` / `refactor:` / `test:` / `chore:`，首行 ≤ 72 字符，中文正文可以。
- 一个 PR 一件事；涉及 UI 的附截图，涉及识别的附前后对比样例。
- 发布：`v*` tag 触发 `.github/workflows/release.yml`（build + twine check + GitHub Release）；镜像见 `Dockerfile`。
