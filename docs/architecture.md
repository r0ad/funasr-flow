# 架构说明

这份文档描述 FunASR Flow 的运行时结构：进程与线程、SQLite 数据模型、任务状态机、流水线阶段、引擎插件点，以及前端依赖的 SSE 事件流。读它可以回答"为什么只能一次跑一个任务""取消到底停在哪一步""说话人是怎么合并的"。

## 1. 进程模型

```text
funasr-flow serve（一个 Python 进程）
├── uvicorn / FastAPI（asyncio 事件循环）
│   ├── HTTP 路由（同步函数，跑在 Starlette 线程池）
│   │     files / transcripts / speakers / system  ←→ SQLite (WAL)
│   ├── GET /api/events（async generator，长连接，每 0.7 s 查一次快照）
│   └── GET /api/files/{id}/audio（流式响应，支持 Range）
├── daemon 线程 "flow-worker"
│   └── worker.run_forever()：claim_next → process_job → 写库
├── daemon 线程 "import"（只在导入时存在）
│   └── library.import_dir()：逐个复制进 data/library → 写 files
└── （可选）另一个进程：funasr-flow worker
        └── 同一个 worker.run_forever()，与 Web 共用 data/flow.db
```

- `serve` 默认在 lifespan 里启动内置 worker 线程；设置 `FLOW_WORKER=0`（或 `--no-worker`）只跑 Web。
- `funasr-flow worker` 是独立进程模式，用于"Web 与推理分开部署"或调试。多进程之间**不共享内存**，只通过 SQLite 协调，所以不会重复领取同一个任务。
- 每个线程用自己的 SQLite 连接（`threading.local`），连接参数：`timeout=30`、`check_same_thread=False`；PRAGMA：`journal_mode=WAL`、`synchronous=NORMAL`、`busy_timeout=30000`、`foreign_keys=ON`。
- 写操作统一走 `db.tx()`（`BEGIN IMMEDIATE` 语义的短事务，出错回滚）。领取任务时显式 `BEGIN IMMEDIATE`，避免 Web 与 worker 同时抢到同一行。

### 为什么必须串行

1. 目标场景是**单卡家用 GPU**。两个任务同时加载同一套 FunASR 模型会各自占一份显存，很容易 OOM。
2. 模型加载是进程内单例缓存（按 `(asr, vad, punc, spk, device)` 做签名），并发切换模型会把缓存打穿，反而更慢。
3. 串行让 `rtf_est`（实时率）与"预计还需多久"可预测；队列按小文件优先，用户能尽快看到第一批结果。

配置里的 `concurrency` 键保留但当前固定为 1，改大不会产生并发。

## 2. 一次任务的生命周期

```text
导入         POST /api/files/scan {"dir": "..."}
              └─ 后台线程把音频复制进 data/library，files 插入/更新，status = idle
入队         POST /api/files/batch {action:"enqueue"}
              └─ jobs 插入（status=queued），files.status = queued
领取         worker.claim_next()  （BEGIN IMMEDIATE）
              └─ jobs.status = running, stage=start, attempts+1
              └─ files.status = running
转码         stage=convert   0.01 → 0.06
             ffmpeg → .cache/tmp/job_<id>.wav（16 kHz / 单声道 / pcm_s16le）
识别         stage=asr       0.06 → 0.92
             engine.transcribe()：句子时间戳 + 文本 + cluster + 每簇质心
聚类/声纹    stage=diarize  0.94
             质心计算 → 合并（0.72）→ 按 spk_num 收敛人数 → 吸收碎片簇
             → 与声纹库匹配（min_speaker_sim，默认 0.62）
落库         stage=save     0.98
             transcripts / segments / file_speakers 覆盖写；files.status = done
完成         jobs.status = done, progress = 1.0, stage = done
             额外导出 data/transcripts/<file_id>.json
```

- **进度不是引擎回调驱动的**：`pipeline` 起一个 ticker 线程，用 `rtf_est` 估算 `asr` 阶段进度并每 0.8 s 写一次 `jobs.progress`。任务结束后用真实耗时更新 `rtf_est = 0.7 × 实测 + 0.3 × 旧值`。
- `transcripts.duration` 与 `files.duration` 都是**秒**（float）；`segments.start_ms/end_ms` 是**毫秒**（int）。
- **短音频直接判失败**：转码后时长 < 1.0 s（或 wav 实际不足 1 秒）抛 `音频为空或过短（不足 1 秒），已跳过`，不再加载模型。
- **落库前清脏字符**：`pipeline.clean_text()` 去掉孤立代理对与控制符，`pipeline.meaningful_chars()` 数有效字符；一个有效字符都没有的句子丢弃，全丢时按 `没有检测到语音：这条录音可能是静音、纯音乐或过短` 判失败。
- 保存是"先删后插"：同一文件重新转写会清掉旧的 `transcripts`（级联删 `segments`）与 `file_speakers`，再写入新结果。校对时改的文本会被覆盖，这是"重新转写"的预期行为。
- 转写结果为空（整段静音）也会写一条 `transcripts`，只是 `segments` 为空，`files.status` 仍是 `done`。

### 状态机

`files.status`：

| 状态 | 进入时机 | 离开 |
| --- | --- | --- |
| `idle` | 导入或重扫资料库发现；取消排队；取消运行中的任务 | 入队 → `queued` |
| `queued` | `enqueue()` 成功 | 被领取 → `running`；被取消 → `idle` |
| `running` | `claim_next()` | 完成 → `done`；异常 → `failed`；取消 → `idle` |
| `done` | `_save_result()` | 重新转写（reset）→ `queued` |
| `failed` | worker 捕获异常 | 重新入队 → `queued` |
| `missing` | 重扫资料库时副本已不存在 | 文件恢复后再次重扫 → `idle` |

`jobs.status`：`queued → running → done | failed | canceled`，另有布尔列 `cancel_requested`。

- 取消**排队中**的任务：`jobs.status = canceled`、`files.status = idle`，立即生效。
- 取消**运行中**的任务：只置 `cancel_requested = 1`；pipeline 在 4 个检查点（转码前、转码后、识别后、写库前）查询该标志，命中就抛 `Canceled`；worker 捕获后写 `jobs.status = canceled`、`error = "用户已取消"`，并把 `files.status` 置回 `idle`。
- 因此取消是"阶段级"的：正在跑的 FunASR 推理不会被强行中断，最长要等当前阶段结束。
- 注意：`files` 没有 `canceled` 状态，`jobs` 才有。取消后文件回到 `idle`，所以网页的"待处理"里能直接重跑；"失败"页只列真正失败的。
- 进程异常退出后，`worker.run_forever()` 启动时调用 `reset_stuck_jobs()`，把卡在 `running` 的任务放回 `queued`（`progress` 归零、`stage` 清空），不会被永久占用。

### 失败与重试

worker 捕获任何异常后写 `jobs.status = failed`、`jobs.error = "<异常类型>: <消息>"`（截断 2000 字符），同时 `files.status = failed`、`files.error` 同步。网页上「重新转写」会删掉该文件的 `transcripts` 与 `file_speakers`、把文件置回 `idle` 再入队；「加入转写队列」对 `failed` 文件也有效，会直接重跑。

## 3. 数据表关系

```text
files (1) ───< (N) jobs
  │ 1                 （每次入队一条，历史保留）
  │
  ├── 1:1 transcripts ───< (N) segments ─── segments_fts（外部内容 FTS5）
  │                                   └─ segments_fts 由触发器与 segments 同步
  │
  └── (N) file_speakers >── (1) speakers
        （file_id, cluster 唯一）        （跨文件的"人物"与声纹向量）
```

| 表 | 关键列 | 说明 |
| --- | --- | --- |
| `files` | `rel_path UNIQUE`、`source_path`、`name`、`ext`、`size`、`mtime`、`duration`（秒）、`status`、`error` | 录音清单。`rel_path` 是**资料库里的绝对路径**（上传与导入统一写）；`source_path` 记导入来源的绝对路径，用于查重，上传的文件为 `NULL` |
| `jobs` | `file_id`、`status`、`stage`、`progress`、`attempts`、`cancel_requested`、`options`(JSON)、时间戳 | 任务与参数快照；`options` 覆盖全局设置 |
| `transcripts` | `file_id UNIQUE`、`duration`、`model`、`language`、`asr_ms` | 一次转写一行，重转即覆盖 |
| `segments` | `transcript_id`、`idx`、`start_ms`、`end_ms`、`speaker`、`text` | `speaker` 存的是 cluster 标签（`spk0`…），不是人名 |
| `segments_fts` | `text` | FTS5 外部内容表，`tokenize='trigram'`，配合 `seg_ai/seg_ad/seg_au` 触发器 |
| `speakers` | `name UNIQUE`、`color`、`embedding`(BLOB)、`notes` | 声纹库；`embedding` 为 L2 归一化后的 float32 |
| `file_speakers` | `file_id`+`cluster` 唯一、`speaker_id`、`display_name`、`confidence`、`seconds`、`utt_count`、`centroid` | 某个文件里某个簇的展示名、绑定关系与质心 |
| `settings` | `key`、`value`(JSON 文本) | 读时回退 `DEFAULT_SETTINGS` |
| `metrics` | `key`、`value`(REAL) | 仅两类：`worker_heartbeat`、`rtf_est` |

- `file_speakers.speaker_id` 外键 `ON DELETE SET NULL`：删掉声纹库里的"人物"不会连带删转写结果，只是解绑。
- `segments_fts` 用 trigram 分词，所以少于 3 个字的查询会 `MATCH` 失败，`/api/search` 会自动回退到 `LIKE %q%`。
- 所有时间列由 SQLite `CURRENT_TIMESTAMP` 写入，接口返回 UTC 字符串。

## 4. 引擎插件点

引擎把"wav → 带说话人簇的句子"这一层抽象出来，接口在 `backend/app/engines/base.py`，注册表在 `backend/app/engines/registry.py`：

```text
pipeline.process_job
      │  cfg = all_settings() + job.options
      ▼
get_engine(cfg.get("engine", "funasr"))     # registry._ENGINES[name] → 引擎实例
      │
      ▼
engine.load(cfg) → engine.transcribe(wav, cfg)
      │
      ├── AsrResult.utterances : [{start_ms, end_ms, text, cluster}]
      ├── AsrResult.centroids  : cluster → 声纹向量（key 必须与 cluster 对齐）
      └── engine.embed_audio(audio, cfg)  → 质心计算、声纹登记、声纹库匹配共用
```

- 内置引擎只有 `funasr`（`funasr_engine.py`），`registry.load_builtin()` 负责 import 它，因此新引擎也必须在该函数里 import 一次，否则 `get_engine()` 抛 `KeyError`。
- `AsrEngine` 是 `Protocol`，不是基类；不需要继承，按结构实现即可。`available()` 会被 `/api/health` 的 `engines` 列表调用，用于设置页展示"可用/不可用原因"。
- `embed_audio()` **没有**写进 Protocol，但 pipeline 与声纹服务直接调用它。没有声纹能力的引擎返回空 `centroids`，聚类合并与声纹匹配自动跳过，句子只剩 `spk0`。
- `cfg["engine"]` 只来自任务 `options`；`settings` 表没有这个键（`PATCH /api/settings` 会以"未知配置项"拒绝）。

新增引擎的完整步骤与代码骨架见 [`CONTRIBUTING.md`](../CONTRIBUTING.md#新增一个识别引擎)。

## 5. 说话人聚类与声纹匹配

全部在 `backend/app/services/pipeline.py`，按顺序：

1. **质心计算** `build_centroids()`：按 cluster 分组，取时长最长的句子，**最多 6 条、每句 ≥ 1200 ms**；一条都不满足时退化为最短的前 2 句。单句音频短于 0.4 s 跳过。对每段调用 `engine.embed_audio()`，取平均后 L2 归一化。
2. **簇合并** `merge_similar_clusters(centroids, utts, threshold, absorb_thr, absorb_max_seconds, target)`，四步收敛（先丢掉没有任何句子的空簇，再按下面的三步收敛；指定了 `spk_num` 时吸收不会把人数降到该值以下）：
   - 反复找最相似的一对，余弦 ≥ `merge_threshold`（默认 0.72）就合并，质心取平均后重新归一化；
   - 若 `target`（来自 `spk_num`，0 表示不限制）小于当前簇数，继续合并最相似的一对直到达标；
   - 吸收碎片：只有 ≤3 句、累计时长 ≤ `absorb_max_seconds`（默认 6.0 秒），且余弦 ≥ `absorb_threshold`（默认 0.45）的簇才会被并进大它两倍以上的簇。这一步刻意保守——早期版本按「时长占比」判断，会把只说了一两句话的真实第二说话人（例如子女在电话里只应了几句）并进主说话人，整条录音只剩一个人。
   合并后返回新的质心表与 `alias` 映射，并**同步改写 `utterance.cluster`**；只影响本次转写，不动数据库里已有的记录。
3. **声纹匹配** `match_speakers(centroids, min_speaker_sim)`：把每个簇质心与 `speakers.embedding` 逐条点积，取最大者；≥ 阈值（默认 0.62）才写 `speaker_id`、`display_name`、`confidence`。低于阈值仍记录 `confidence`，但不绑定。

阈值含义不同，不要混：

| 键 | 默认 | 作用 |
| --- | --- | --- |
| `merge_threshold` | 0.72 | 两个簇"是不是同一个人"，直接合并 |
| `absorb_threshold` | 0.45 | 碎片簇"够不够像"某个大簇，决定是否被吸收 |
| `absorb_max_seconds` | 6.0 | 累计时长不超过多少（且 ≤3 句）才算碎片簇 |
| `spk_num` | 0 | 目标人数；>0 时强制合并到该人数 |
| `min_speaker_sim` | 0.62 | 簇"像不像声纹库里的某个人"，决定是否套用名字 |

前四个键影响聚类结果，`min_speaker_sim` 只影响命名。它们都能通过 `PATCH /api/settings` 修改；设置页目前只暴露 `spk_num` 与 `min_speaker_sim`。

## 6. SSE 事件流

`GET /api/events` 是一个永不主动结束的 `text/event-stream`：

```text
每 0.7 s：
  snapshot = {"queue": {...}, "files": {...}, "import": {...}, "active": {...}|null, "worker_age": 12.3, "ts": 1762...}
  与上次序列化结果比较，不同才发：
    data: {"queue":{"queued":12,"running":1,...},"files":{"idle":967,"done":13,...},...}\n\n
  相同则计数，每 15 次（约 10.5 s）发一行注释保活：
    : keepalive\n\n
```

- 没有 `id:` 字段、没有 `Last-Event-ID`、没有历史重放；断线重连后只能拿到"当前状态"，不需要增量补齐。
- 快照是**轮询 + 比较**得到的，而不是由 worker 主动 push；好处是 worker 与事件流完全解耦（甚至可以在另一个进程），代价是最多 0.7 s 的延迟。
- 前端 `store.startLive()` 用 `EventSource('/api/events')` 消费，断线 5 s 后重建；收到任何变化只更新全局 store，列表页再按需重新拉 `/api/files`。
- 响应头带 `Cache-Control: no-cache`、`X-Accel-Buffering: no`，避免反向代理缓冲导致"进度不动"。

## 7. 磁盘布局

| 路径 | 内容 | 是否入库 |
| --- | --- | --- |
| `data/flow.db` | 全部结构化数据（WAL 模式，另有 `-wal`/`-shm`） | — |
| `data/library/` | **资料库**：上传与导入的音频副本；容器里是 `/data/library`；代码里 `UPLOAD_DIR` 只是它的别名，旧 `data/uploads` 已废弃 | 是，`rel_path` 指向这里 |
| `data/transcripts/<file_id>.json` | 每个文件一份导出快照（含 `engine` 与 speakers 映射） | 否，冗余备份 |
| `.cache/tmp/job_<id>.wav` | 转码中间文件，默认任务结束即删（`keep_wav=true` 保留） | 否 |
| `.cache/modelscope/` | ModelScope 模型缓存（`MODELSCOPE_CACHE`） | 否 |
| `.cache/huggingface/`、`.cache/funasr/` | 其余缓存（`HF_HOME`、`FUNASR_CACHE`） | 否 |
| `CallRecord/` | 默认的导入来源目录，只读；导入时把音频复制进资料库 | — |

`data/`、`.cache/`、`CallRecord/` 都在 `.gitignore` 里。`FLOW_DATA_DIR` / `FLOW_CACHE_DIR` 可以把前两者搬到别的磁盘。

### 为什么是「导入」而不是「扫描外部目录」

旧实现是「扫描外部目录 + 把 `rel_path` 记成相对 `input_dir` 的路径」。这套模型有个致命问题：`input_dir` 一换（换挂载点、容器重建、用户改了录音目录），数据库里所有相对路径同时失效，978 个文件会被一次性判成 `missing`——转写结果其实还在，列表却全是「文件丢了」。

现在把来源和资产分开：

- 外部目录**只是导入来源**。`library.import_one()` 把音频复制进 `data/library/`（`ingest_mode=link` 时同盘建硬链接），数据库只记资料库里的绝对路径。源目录被删、盘符变了、容器换挂载点，都不影响已有录音和转写。
- `source_path`（绝对路径）+ `size` 用来查重：同一来源、大小没变 → `skipped`；同来源但大小变了 → 覆盖资料库里的副本、更新记录，并删掉旧 `transcripts`（内容已不是同一段音频）。
- 重名不覆盖：`library.unique_target()` 改成 `名字 (2).m4a`。
- `missing` 只在「重扫资料库」时判定，且只对落在本次扫描目录下的记录，别的目录的文件不会互相误伤；文件回来自动恢复 `idle`。

导入跑在后台线程里，进度存在 `library._state`，通过 `GET /api/state` 的 `import` 与 `GET /api/files/library` 暴露：`running`、`mode`、`dir`、`total`、`done`、`added`、`skipped`、`failed`、`current`、`started_at`、`finished_at`、`error`。同一时刻只允许一个导入任务（`start_import()` 见 `running` 直接拒绝，API 返回 409）；取消是协作式的，线程处理下一个文件前检查标志。

导入线程结束时会自动触发一次 `scanner.start_duration_probe(LIBRARY_DIR)`：新入库的文件还没有时长，探测完列表里立刻能看到 `0:xx`（补时长失败只记 warning，不影响导入结果）。

注意：`import_dir()` 的返回值与 `_state` 只有 `added/skipped/failed/total`，**没有独立的 `replaced` 键**——被覆盖的文件计在 `added` 里。

## 8. 已知限制

- 单 worker 串行；多卡并行尚未实现。
- `auto_scan_minutes > 0` 时 `services/scanner.start_auto_scan()` 起一个 `auto-scan` 线程，每 15 秒检查一次是否到点；到点调 `library.import_dir(input_dir)` 自动导入一次（已有导入在跑就跳过），并给新增文件补时长。默认 `0`（关闭）。
- 取消只能停在阶段边界，不能中断正在执行的 FunASR 推理。
- 声纹登记的临时文件用 `config.temp_path()`（创建后立刻 `os.close`）配合 `safe_unlink()`，Windows 上文件被占用时只跳过删除，不再抛 500。
- SSE 每个连接每 0.7 s 查一次库；同时开很多标签页会增加 SQLite 读压力（WAL 下读不阻塞写，但仍建议一两个页面）。
