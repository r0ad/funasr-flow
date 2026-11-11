# AGENTS.md

FunASR Flow：把手机通话录音批量转成「带时间轴 + 说话人标签」的文字稿。FastAPI 后端 + Vue3 前端 + SQLite，
全部本机离线运行（模型首次下载除外）。**面向家人/朋友通话录音，中文方言场景优先**。

技术栈：Python 3.10+ / FastAPI / SQLite（含 FTS5）/ FunASR（Paraformer + FSMN-VAD + CT-Punc + CAM++）/ Vue 3 + Vite。

## 常用命令

```bash
# 安装（默认只装 Web 层；转写需要 asr extra）
pip install -e ".[dev]"           # 开发
pip install -e ".[asr]"           # 转写引擎（torch/funasr/modelscope，体积大）

# 跑起来（默认 http://127.0.0.1:8000，会自动建库、扫描、起内置 worker）
python run.py                     # 等价于 funasr-flow serve
python run.py --port 8010 --no-browser
funasr-flow doctor                # 环境自检：ffmpeg / torch / CUDA / funasr

# 扫描与队列
funasr-flow scan --dir CallRecord
curl -X POST localhost:8000/api/files/scan
curl -X POST localhost:8000/api/files/batch -H 'Content-Type: application/json' \
     -d '{"ids":[1,2,3],"action":"enqueue"}'

# 前端
cd frontend && npm install && npm run build     # 产物 frontend/dist，后端自动托管
cd frontend && npm run dev                      # 5173，/api 代理到 8000

# 质量门
ruff check backend && ruff format --check backend
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
  services/           scanner / queue / pipeline / speakers
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
- **语音模型是方言相关的**：默认 `paraformer-zh`（通用）；`lukeewin01/paraformer-large-sichuan-offline`
  对四川话明显更准（实测同一段通话：通用模型出「甘肃多多嘛啊路排不多」，四川模型出「准备给就要给你们寄过来的时候没寄」）。
  换 ASR 模型时注意：**只有 SeACo 系（含 `paraformer-zh`）支持 `hotword`**，且 hotword 是空格分隔、无权重语法。
- **说话人分离依赖 punc + 字符时间戳**：`spk_mode="punc_segment"` 需要 punc 模型且 ASR 带 timestamp，
  否则 FunASR 静默回退 `vad_segment`（电话录音里一句可长达 30s，归属会糊）。不要为了省内存关掉 punc。
- **过聚类是常态，靠三步收拾**（`pipeline.merge_similar_clusters`）：高相似合并（`merge_threshold` 0.72）→
  目标人数约束（设置 `spk_num`，0=自动）→ 碎片簇吸收（占比 < `small_cluster_ratio` 且相似度 ≥ `absorb_threshold`）。
  实测 2 人通话会被 FunASR 切成 3 簇，第三簇往往只有 1–2 句噪声。
- **质心标签必须自证**：`spk_embedding_center` 可能是 dict / ndarray / list，用 `_iter_centers` 归一；
  标签和句子 spk 对不上时宁可丢弃质心，也不能把 A 的声纹记到 B 头上。
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
- 纯逻辑优先测：`_fmt_clock`/`scan`/`enqueue`/`claim_next`/`export`/`merge_similar_clusters`。
- 改动推理路径（engines/、pipeline.py）时，至少手工跑通一条真实录音并在 PR 里贴出转写片段与耗时。

## 边界

**Always**：改完跑 `ruff check backend && pytest -q`；新配置项写进 `DEFAULT_SETTINGS`；提交前确认 `.gitignore`
没把 `CallRecord/`、`data/`、`.cache/` 里的隐私数据带进来。

**Ask first**：改数据库 schema 或删除 `data/` 内容；换默认 ASR/VAD/PUNC/SPK 模型；把 `concurrency` 调到 > 1；
新增第三方依赖（尤其需要联网下载的）。

**Never**：commit 真实通话录音、转写文本、`data/flow.db`、模型权重、`.cache/`；在日志里打印完整转写文本或电话号码；
把用户目录当模型缓存；绕过 ffmpeg 直接喂 m4a 给 funasr。

## 提交与 PR

- Conventional Commits：`feat:` / `fix:` / `docs:` / `refactor:` / `test:` / `chore:`，首行 ≤ 72 字符，中文正文可以。
- 一个 PR 一件事；涉及 UI 的附截图，涉及识别的附前后对比样例。
- 发布：`v*` tag 触发 `.github/workflows/release.yml`（build + twine check + GitHub Release）；镜像见 `Dockerfile`。
