# HTTP API

服务启动后监听 `http://127.0.0.1:8000`（`--host` / `--port` 可改）。前端开发模式下 Vite 把 `/api` 代理到 8000，所以下面的路径在两种模式下都一致。

- 所有请求与响应都是 JSON（音频流与导出下载除外），UTF-8。
- 没有鉴权、没有 API Key：这是本机工具，默认只监听 `127.0.0.1`。要暴露到局域网，请自行在反向代理上加访问控制。
- CORS 允许 `http://localhost:5173`、`http://127.0.0.1:5173`、`:4173` 三个来源，方法与头不限制。
- 出错统一返回 `{"detail": "<中文说明>"}`，HTTP 状态码见各接口。
- 时间戳字段（`created_at`/`updated_at`/`started_at` 等）是 SQLite 的 UTC 字符串，形如 `2025-11-11 03:20:41`。
- 时长：`files.duration`、`transcripts.duration` 单位为**秒**（float）；`segments.start_ms/end_ms`、`speaker.seconds` 之外的时间戳单位为**毫秒**（int）。`file_speakers.seconds` 是秒。
- 说话人簇标签统一是 `spk0`、`spk1`…，颜色按标签末尾数字在 8 色调色板里轮转。
- 查询参数里带中文（如 `q=医院`）时，客户端必须做 URL 编码；直接向 uvicorn 发送未编码的非 ASCII URI 会被拒绝（`Invalid HTTP request received`）。

## 资源一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/version` | 名称、版本、项目根目录 |
| GET | `/api/health` | 依赖、GPU、引擎、worker 状态 |
| GET | `/api/settings` | 当前设置、默认值、模型候选 |
| PATCH | `/api/settings` | 批量修改设置 |
| GET | `/api/state` | 队列与文件状态快照 |
| GET | `/api/events` | SSE 状态流 |
| POST | `/api/queue/cancel` | 取消排队 / 中断当前任务 |
| POST | `/api/queue/clear` | 清理已结束的任务记录 |
| GET | `/api/stats` | 汇总统计 |
| GET | `/api/files` | 文件列表（筛选、排序、分页） |
| GET | `/api/files/{file_id}` | 单个文件详情 |
| GET | `/api/files/{file_id}/audio` | 音频流（支持 Range） |
| GET | `/api/browse` | 列目录（界面「导入录音」选来源用） |
| POST | `/api/files/upload` | 上传单个音频（原始 body，落进资料库） |
| POST | `/api/files/scan` | 带 `dir` = 后台导入该目录；不带 `dir` = 重扫资料库 |
| GET | `/api/files/library` | 资料库概况（文件数 / 占用）+ 当前导入进度 |
| POST | `/api/files/import/cancel` | 取消正在跑的导入任务 |
| POST | `/api/files/batch` | 批量入队 / 重新转写 / 删除 |
| DELETE | `/api/files/{file_id}` | 删除记录；`purge=true` 时只删资料库里的副本 |
| GET | `/api/transcripts/{file_id}` | 转写全文（句子 + 说话人） |
| PATCH | `/api/transcripts/{file_id}/segments/{idx}` | 改一句的文字或簇标签 |
| PATCH | `/api/transcripts/{file_id}/speakers/{cluster}` | 给某个簇改名 / 绑定声纹 |
| POST | `/api/transcripts/{file_id}/speakers/{cluster}/enroll` | 用该簇质心登记声纹 |
| POST | `/api/transcripts/{file_id}/speakers/{cluster}/merge` | 把该簇合并进另一个簇（人工收拾过聚类） |
| POST | `/api/transcripts/bulk-assign` | 把某簇绑定到已有声纹 |
| GET | `/api/search` | 全文搜索 |
| GET | `/api/export` | 导出 TXT / Markdown / SRT / JSON |
| GET | `/api/speakers` | 声纹库列表 |
| POST | `/api/speakers` | 新建声纹（只有名字） |
| POST | `/api/speakers/from-file` | 从录音的时间区间提取声纹 |
| POST | `/api/speakers/enroll` | 上传音频登记声纹（原始 body） |
| POST | `/api/speakers/{speaker_id}/enroll` | 同上，登记到指定人物 |
| POST | `/api/speakers/{speaker_id}/enroll-from-file` | 从录音时间区间覆盖指定人物的声纹 |
| POST | `/api/speakers/{target_id}/merge` | 合并两个声纹 |
| GET | `/api/speakers/{speaker_id}/files` | 该人物出现过的录音 |
| PATCH | `/api/speakers/{speaker_id}` | 改名 / 改颜色 / 备注 |
| DELETE | `/api/speakers/{speaker_id}` | 删除声纹 |

---

## 系统

### GET `/api/version`

```json
{"name": "funasr-flow", "version": "0.1.0", "root": "H:\\ws-ai\\funasr-flow"}
```

### GET `/api/health`

依赖探测与运行环境。`engines` 里每项来自引擎注册表，`available=false` 时 `reason` 给出原因。

```json
{
  "ok": true,
  "root": "H:\\ws-ai\\funasr-flow",
  "cache_dir": "H:\\ws-ai\\funasr-flow\\.cache",
  "ffmpeg": true,
  "python": true,
  "funasr": true,
  "torch": true,
  "gpu": {"available": true, "name": "NVIDIA GeForce RTX 4060 Ti", "vram_mb": 16379,
          "torch_cuda": true, "hint": null},
  "engines": [
    {"id": "funasr", "label": "FunASR（Paraformer + CAM++，本地离线）", "available": true, "reason": ""}
  ],
  "worker": {"thread": true, "heartbeat_age": 1.2},
  "ml": {
    "models": {
      "asr": [
        {"id": "paraformer-zh", "label": "Paraformer 中文通用（快，推荐）"},
        {"id": "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch", "label": "Paraformer-large 完整版"},
        {"id": "lukeewin01/paraformer-large-sichuan-offline", "label": "Paraformer 四川话微调（205h，方言更准）"}
      ],
      "spk": [{"id": "cam++", "label": "CAM++ 中文声纹（推荐）"}]
    }
  }
}
```

`worker.thread` 表示**本进程**里是否有存活的内置 worker；用独立 `funasr-flow worker` 部署时它是 `false`，但 `heartbeat_age` 仍会更新。

`gpu` 字段固定有 5 个键，不要按「有没有 hint」做分支：

| 键 | 类型 | 说明 |
| --- | --- | --- |
| `available` | bool | `torch.cuda.is_available()` 且能取到 0 号卡时为 `true` |
| `name` / `vram_mb` | str / int | 显卡名与显存（MB），没有 GPU 时为 `null` |
| `torch_cuda` | bool | `torch.cuda.is_available()` 的原始结果；`false` 说明当前 torch 没认出 CUDA |
| `hint` | str \| null | 仅在没有 GPU 时有内容，给用户一句可操作的中文说明 |

`hint` 分两种场景：容器里提示「镜像可能是 CPU 版（用
`--build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cu121` 重新构建），或启动时没加 `--gpus all`」；
宿主机上提示「确认已装 NVIDIA 驱动，且 PyTorch 是 CUDA 版」。没有 GPU 时 `gpu` 长这样：

```json
{"gpu": {"available": false, "name": null, "vram_mb": null, "torch_cuda": false,
         "hint": "容器里没检测到 GPU：镜像可能是 CPU 版（用 --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cu121 重新构建），或启动时没加 --gpus all"}}
```

### GET `/api/settings`

```json
{
  "values": {
    "input_dir": "H:\\ws-ai\\funasr-flow\\CallRecord",
    "ingest_mode": "copy",
    "device": "cuda:0",
    "asr_model": "paraformer-zh",
    "vad_model": "fsmn-vad",
    "punc_model": "ct-punc",
    "spk_model": "cam++",
    "hotwords": "妈妈 爸爸 哥哥 姐姐 外婆 外公 幺爸 幺妈 舅舅 嬢嬢",
    "spk_num": 0,
    "batch_size_s": 300,
    "auto_map_speakers": true,
    "min_speaker_sim": 0.62,
    "merge_threshold": 0.72,
    "absorb_threshold": 0.45,
    "absorb_max_seconds": 6.0,
    "concurrency": 1,
    "keep_wav": false,
    "auto_scan_minutes": 0
  },
  "defaults": {"...": "同上，来自 config.DEFAULT_SETTINGS"},
  "models": {"asr": ["..."], "spk": ["..."]}
}
```

`defaults` 是 `DEFAULT_SETTINGS` 原样；`models` 是 `MODEL_CHOICES`。

### PATCH `/api/settings`

请求体只带要改的键，值会按默认值的类型转换（`bool` / `int` / `float` / `str`）：

```json
{"values": {"device": "cpu", "min_speaker_sim": 0.7}}
```

返回 `{"values": {...}}`（完整的最新值）。未知键返回 400 `{"detail": "未知配置项: foo"}`；类型转不过去返回 400 `{"detail": "配置项 x 类型错误"}`。

`merge_threshold` / `absorb_threshold` / `absorb_max_seconds` 分别控制聚类合并、碎片簇吸收阈值与碎片判定时长（详见 `docs/architecture.md`），设置页「高级」面板里可以直接调。

注意：`engine` 不是设置项，选中引擎只能通过任务 `options`（见 `/api/files/batch`）；`auto_scan_minutes > 0` 时后端每 N 分钟自动导入一次 `input_dir`，`0` 表示关闭。
`ingest_mode` 取 `copy`（默认，复制进资料库）或 `link`（同盘硬链接，省一份空间）；其它值在导入时回退 `copy`。设置页暂未暴露，需要时用 PATCH 改。

### GET `/api/state`

SSE 推送的就是这个结构（未变化时不重复推）。

```json
{
  "queue": {
    "queued": 12, "running": 1, "done": 964, "failed": 2, "canceled": 1,
    "current": {"job_id": 977, "file_id": 13, "progress": 0.42, "stage": "asr",
                "name": "2024-05-01_妈妈.m4a", "duration": 183.4, "started_at": "2025-11-11 03:20:41"},
    "queued_seconds": 4210.5,
    "rtf": null
  },
  "files": {"idle": 3, "queued": 12, "running": 1, "done": 964, "failed": 2, "missing": 1},
  "import": {"running": false, "mode": "copy", "dir": "D:/通话录音", "total": 0, "done": 0,
             "added": 0, "skipped": 0, "failed": 0, "current": "",
             "started_at": null, "finished_at": null, "error": null},
  "active": {"id": 977, "file_id": 13, "stage": "asr", "progress": 0.42,
             "started_at": "2025-11-11 03:20:41", "name": "2024-05-01_妈妈.m4a", "duration": 183.4},
  "worker_age": 0.8,
  "ts": 1762790441
}
```

- `queue.queued_seconds` 是排队任务的音频总时长，前端用 `× rtf_est` 估算剩余时间；`queue.rtf` 目前恒为 `null`（真实值在 `/api/stats`）。
- `active` 与 `queue.current` 内容重叠，前者少 `job_id`。
- `worker_age` 为 `null` 表示从未有过心跳。
- `import` 是当前（或最近一次）导入任务的进度。`running` 为真时轮询这个字段就够了；`added` **包含被覆盖的文件**，`skipped` 是来源与 `source_path` + `size` 都没变的文件，`failed` 是单个导入失败的文件（不会中断整批），`current` 是正在处理的文件名。`error` 非空表示整批失败或已取消（取消时是 `导入已取消`）。从未导入过时 `dir` 为空、计数全 0、`started_at`/`finished_at` 为 `null`。

### GET `/api/events`

`text/event-stream`。每 0.7 s 生成一次快照，与上次不同才发送：

```text
data: {"queue":{"queued":12,"running":1,...},"files":{"done":964,...},"active":{...},"worker_age":0.8,"ts":1762790441}

: keepalive
```

没有 `id:`、不支持 `Last-Event-ID`、没有历史重放。约 10.5 s 无变化发一条 `: keepalive` 注释。响应头包含 `Cache-Control: no-cache` 与 `X-Accel-Buffering: no`。

### POST `/api/queue/cancel`

| 参数 | 位置 | 说明 |
| --- | --- | --- |
| `mode` | query | `queued`（默认）只取消排队；`all` 同时给运行中的任务置取消标志 |

```json
{"canceled": 12, "interrupted": 1}
```

被取消的排队任务：`jobs.status=canceled`、文件回到 `idle`；运行中的任务要等 pipeline 走到下一个检查点才真正停下。

### POST `/api/queue/clear`

删除所有 `done`/`failed`/`canceled` 的 `jobs` 记录（不动 `files`、`transcripts`）：

```json
{"deleted": 967}
```

### GET `/api/stats`

```json
{
  "files": 980,
  "done_files": 962,
  "total_seconds": 512340.2,
  "done_seconds": 498120.6,
  "segments": 128400,
  "speakers": 6,
  "speech_hours": 135.2,
  "rtf_est": 0.0621,
  "eta_seconds": 261.5
}
```

`done_files` 是已转写完成的文件数（前端顶部「已转写 N/980」用它）；`total_seconds`/`done_seconds` 按文件时长累计；`speech_hours` 按句子时长累计（更接近"有效语音"）。
`eta_seconds = max(total_seconds - done_seconds, 0) × rtf_est`——注意它按"全库还没转写的时长"估算，不是只算当前队列；
`rtf_est` 还没建立时取 `0.1` 兜底。

---

## 文件库

### GET `/api/files`

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `status` | 全部 | 逗号分隔，如 `idle,missing`；取值 `idle`/`queued`/`running`/`done`/`failed`/`missing` |
| `q` | — | 文件名模糊匹配（`LIKE %q%`） |
| `sort` | `name` | `name`/`duration`/`size`/`status`/`updated`，非法值回退 `name` |
| `order` | `asc` | `asc`/`desc`，其它值按 `asc` |
| `limit` | `50` | 每页条数 |
| `offset` | `0` | 偏移 |

```json
{
  "items": [
    {
      "id": 13,
      "name": "2024-05-01_妈妈.m4a",
      "rel_path": "H:/ws-ai/funasr-flow/data/library/2024-05-01_妈妈.m4a",
      "ext": ".m4a",
      "size": 1827344,
      "duration": 183.4,
      "status": "done",
      "error": null,
      "updated_at": "2025-11-11 03:22:10",
      "has_transcript": true,
      "model": "paraformer-zh",
      "segment_count": 96,
      "speakers": [
        {"cluster": "spk0", "name": "妈妈", "seconds": 121.3, "speaker_id": 4, "color": "#4C8DFF"},
        {"cluster": "spk1", "name": "说话人2", "seconds": 58.9, "speaker_id": null, "color": "#FF7A59"}
      ]
    }
  ],
  "total": 980,
  "limit": 50,
  "offset": 0
}
```

`name` 在 `speakers[]` 里的取值顺序：`file_speakers.display_name` → 绑定的 `speakers.name` → `cluster` 本身。

### GET `/api/files/{file_id}`

返回单个文件的全部字段，外加带 `confidence`/`utt_count` 的说话人列表：

```json
{
  "id": 13, "name": "2024-05-01_妈妈.m4a", "rel_path": "H:/ws-ai/funasr-flow/data/library/2024-05-01_妈妈.m4a",
  "ext": ".m4a", "size": 1827344, "duration": 183.4, "status": "done",
  "error": null, "updated_at": "2025-11-11 03:22:10",
  "has_transcript": true, "model": "paraformer-zh",
  "speakers": [
    {"cluster": "spk0", "name": "妈妈", "confidence": 0.8731, "seconds": 121.3,
     "utt_count": 62, "speaker_id": 4, "color": "#4C8DFF"}
  ]
}
```

404 `{"detail": "文件不存在"}`。

### GET `/api/files/{file_id}/audio`

直接返回音频字节，供 `<audio>` 播放：

- 支持 `Range: bytes=start-end`，正常返回 206 + `Content-Range`；非法区间返回 416。
- `Content-Type`：`.m4a`/`.mp4`/`.m4b` → `audio/mp4`；`.wav` → `audio/wav`；其余 → `audio/mpeg`。
- 资料库里的副本被删掉时返回 410 `{"detail": "资料库里的文件已丢失"}`；记录不存在返回 404。

### GET `/api/browse`

给界面里的「导入录音」选来源用：列出目录、盘符和每个子目录里的音频数量。**不限制路径范围**
（本机工具，和能读你磁盘的其它本地程序权限相同）；`path` 省略或无效时会回退到当前
`input_dir`，再回退到第一个可访问的根。

```
GET /api/browse?path=H:/ws-ai/funasr-flow
```

```json
{
  "path": "H:\\ws-ai\\funasr-flow",
  "parent": "H:\\ws-ai",
  "sep": "\\",
  "roots": [{"name": "C:", "path": "C:\\"}, {"name": "H:", "path": "H:\\"}],
  "entries": [{"name": "CallRecord", "path": "H:\\ws-ai\\funasr-flow\\CallRecord", "audio": 980}],
  "audio": 0,
  "container": false,
  "mounts": [],
  "image": "sunzroad/funasr-flow:latest",
  "hint": ""
}
```

- `entries` 只列子目录（跳过 `.`/`$` 开头的隐藏与系统目录，以及 `System Volume Information` 等），**里面有录音的排在前面**；`audio` 是该目录（不递归）的音频数。
- `container`：是否跑在 Docker 里（检测 `/.dockerenv` 或 `FLOW_IN_DOCKER=1`）。为 `true` 时：
  `mounts` 列出可见挂载（`/media`、`/mnt`、`/data`，以及整盘挂载约定 `/host/<盘符>`），它们会被放进 `roots` 最前面，界面上直接可点；`hint` 是给界面显示的一句话说明（容器里看不到未挂载的宿主机目录）。
- 无权限返回 403 `{"detail": "没有权限读取: ..."}`，其它读取失败返回 400。

### POST `/api/files/upload`

上传一个音频文件：**请求体是文件的原始字节**（不是 multipart），文件名通过查询参数传。
这样后端不需要 `python-multipart` 依赖，前端用 XHR 直传 `File` 对象即可拿到上传进度。

```bash
curl -X POST "http://127.0.0.1:8000/api/files/upload?name=$(python -c "import urllib.parse;print(urllib.parse.quote('10月13日 妈.m4a'))")" \
     -H 'Content-Type: application/octet-stream' \
     --data-binary @"/path/to/10月13日 妈.m4a"
```

```json
{"ok": true, "file": {"id": 981, "name": "10月13日 妈.m4a", "size": 831800, "status": "idle", "duration": null}}
```

- 文件落在资料库 `data/library/`（容器里是 `/data/library`；代码里 `UPLOAD_DIR` 只是 `LIBRARY_DIR` 的别名，旧 `data/uploads` 已废弃），数据库里存**绝对路径**；和「导入目录」进来的文件在同一个资料库，换挂载点、重启容器都不影响。
- 文件名只取 basename，Windows 非法字符、路径分隔符和控制字符会换成 `_`；重名自动加 ` (2)`。
- **同名同大小视为同一条录音**：写完副本后发现库里已有 `name` + `size` 相同且状态不是 `missing` 的记录，
  就删掉刚写的副本，返回 `{"ok": true, "skipped": true, "file": {…}}`。界面整文件夹重传时就靠这个跳过重复项。
- 界面上传是浏览器读本机文件（`FolderImport.vue`）：选文件夹只取**这一层**的音频，不进子文件夹；服务器端不需要挂载任何目录。
- 扩展名不在音频白名单里返回 400 `不支持的音频格式: .exe`；空 body 返回 400 `空文件`。
- 上传不写 `source_path`（它只记导入来源），所以同一份来源文件之后再用「导入目录」导入时，会作为新文件再加一份。
- 上传完不会自动入队，需要再调 `/api/files/batch`（或界面上点「开始转写」）。

### POST `/api/files/scan`

同一个端点两种语义。

**带 `dir`：把该目录当来源导入资料库**（后台线程跑，请求立刻返回）：

```bash
curl -X POST http://127.0.0.1:8000/api/files/scan \
     -H 'Content-Type: application/json' \
     -d '{"dir": "D:/通话录音"}'
```

```json
{
  "ok": true,
  "importing": true,
  "root": "D:/通话录音",
  "import": {"running": true, "mode": "copy", "dir": "D:/通话录音", "total": 0, "done": 0,
             "added": 0, "skipped": 0, "failed": 0, "current": "",
             "started_at": 1762790441.2, "finished_at": null, "error": null}
}
```

- 目录（含子目录）里的音频**复制**进 `data/library/`，数据库记资料库里的绝对路径，另用 `source_path` 记来源查重；来源目录被删、换盘符都不影响已导入的录音。
- 返回里的 `importing` 表示导入是否仍在跑：刚触发时通常为 `true`，线程结束后是 `false`；进度一律以 `import` 为准。
- 导入结束会自动触发一次后台 `ffprobe` 补时长，新入库文件的 `duration` 先为 `null`，探测完列表里就有 `0:xx`。
- `dir` 会写进 `input_dir`，作为设置页「自动导入目录」和 `auto_scan_minutes` 的默认来源。可选 `mode`：`copy`（默认）/ `link`（同盘硬链接）；省略时用 `ingest_mode`。
- 进度看 `GET /api/state` 的 `import`，或 `GET /api/files/library`。同时只允许一个导入任务，已在跑时返回 **409** `{"detail": "已有导入任务在跑"}`。
- 目录不存在返回 **400** `{"detail": "目录不存在：D:/没有这个目录"}`，且不会写 `input_dir`。

**不带 `dir`：重扫资料库**（对齐磁盘变化，不导入外部目录）：

```bash
curl -X POST http://127.0.0.1:8000/api/files/scan
```

```json
{"ok": true, "root": "H:/ws-ai/funasr-flow/data/library", "added": 0, "updated": 3, "missing": 1, "total": 980}
```

- `added`/`updated`：资料库里新增或 `size`/`mtime` 变化的文件；`missing`：记录在、资料库里的副本没了（只在本次目录范围内判定，文件回来会自动恢复 `idle`）。
- 重扫和导入结束都会在后台用 `ffprobe` 补时长；两种语义都不自动入队。

### GET `/api/files/library`

资料库概况 + 当前导入进度（设置页「资料库」卡片用）：

```bash
curl -s http://127.0.0.1:8000/api/files/library
```

```json
{
  "ok": true,
  "dir": "H:/ws-ai/funasr-flow/data/library",
  "files": 980,
  "bytes": 3741119210,
  "import": {"running": false, "mode": "copy", "dir": "D:/通话录音", "total": 12, "done": 12,
             "added": 10, "skipped": 2, "failed": 0, "current": "",
             "started_at": 1762790441.2, "finished_at": 1762790450.7, "error": null}
}
```

- `files`/`bytes` 只统计**真正落在 `LIBRARY_DIR` 里**的副本（仍指向外部目录的老记录不算）。`files` 也计入「记录还在、副本被手工删掉」的条目，`bytes` 不计（读不到大小就跳过）。
- `import` 结构和 `/api/state` 里的一致；没跑过导入时计数全 0。

### POST `/api/files/import/cancel`

请求取消当前导入任务（不删已导入的文件）：

```bash
curl -X POST http://127.0.0.1:8000/api/files/import/cancel
```

```json
{"ok": true, "import": {"running": true, "mode": "copy", "dir": "D:/通话录音", "total": 12,
                        "done": 4, "added": 4, "skipped": 0, "failed": 0, "current": "a.m4a",
                        "started_at": 1762790441.2, "finished_at": null, "error": null}}
```

- 取消是协作式的：后台线程处理下一个文件前停下，`import.error` 变成 `导入已取消`，`running` 随后转 `false`。没有导入在跑时也返回 200，只是什么都没发生。

### POST `/api/files/batch`

```json
{"ids": [13, 14, 15], "action": "enqueue", "options": {"engine": "funasr", "spk_num": 2}}
```

| action | 行为 | 返回 |
| --- | --- | --- |
| `enqueue` | 逐个入队；已在 `queued`/`running` 的跳过 | `{"ok": true, "queued": 3, "skipped": 0, "job_ids": [981, 982, 983]}` |
| `reset` | 删除该文件的 `transcripts` 与 `file_speakers`，置 `idle` 后重新入队 | `{"ok": true, "reset": 3}` |
| `delete` | 删除 `files` 记录（级联删 `jobs`/`transcripts`/`segments`/`file_speakers`） | `{"ok": true, "deleted": 3}` |

- `options` 是**任务级覆盖**：入队时存进 `jobs.options`，运行时覆盖全局设置（例如只给这一批指定 `spk_num`）。
- 未知 `action` 返回 400 `{"detail": "未知操作: xxx"}`。
- `reset` 是"重新转写"：已有校对文本会被清掉。

### DELETE `/api/files/{file_id}`

```json
{"ok": true, "purged": true}
```

- 默认 `purge=true`：如果记录里的音频在资料库（`data/library/`，上传和导入的副本都在这里），一并删掉磁盘文件；**外部来源目录里的原文件永远不动**。
- 传 `?purge=false` 只删数据库记录；资料库外的路径不会被 unlink。删除不存在的 id 返回 404 `文件不存在`。

---

## 转写结果

### GET `/api/transcripts/{file_id}`

没有转写结果时也返回 200，`transcript` 为 `null`：

```json
{
  "file": {"id": 13, "name": "2024-05-01_妈妈.m4a", "rel_path": "H:/ws-ai/funasr-flow/data/library/2024-05-01_妈妈.m4a",
           "ext": ".m4a", "size": 1827344, "duration": 183.4, "status": "done",
           "error": null, "updated_at": "2025-11-11 03:22:10"},
  "transcript": {"id": 96, "duration": 183.4, "model": "paraformer-zh",
                 "asr_ms": 11420, "created_at": "2025-11-11 03:21:58",
                 "updated_at": "2025-11-11 03:21:58"},
  "segments": [
    {"idx": 0, "start_ms": 340, "end_ms": 2260, "speaker": "spk0", "text": "喂，妈，你吃饭没有？"}
  ],
  "speakers": [
    {"cluster": "spk0", "name": "妈妈", "display_name": "妈妈", "speaker_id": 4,
     "confidence": 0.8731, "seconds": 121.3, "utt_count": 62, "color": "#4C8DFF"}
  ]
}
```

- `segments` 按 `idx` 升序；`speaker` 是簇标签，不是人名（人名在 `speakers` 里映射）。
- `speakers` 按说话时长降序；`name` = `display_name` → `speakers.name` → `说话人N`。
- 文件不存在返回 404 `{"detail": "文件不存在"}`。

### PATCH `/api/transcripts/{file_id}/segments/{idx}`

```json
{"text": "喂，妈，你吃过饭没有？"}
```

也可以只改簇标签：`{"speaker": "spk1"}`。返回 `{"ok": true}`。

- 无转写结果：404 `该文件还没有转写结果`；两个字段都没给：400 `没有要修改的字段`；`idx` 不存在：404 `句子不存在`。
- `text` 会 `strip()` 后写入，同时同步 `segments_fts`（触发器），搜索立即可见。

### PATCH `/api/transcripts/{file_id}/speakers/{cluster}`

```json
{"display_name": "妈妈"}
```

或绑定到声纹库：`{"speaker_id": 4}`（会同时把 `display_name` 设为该声纹的名字）。返回 `{"ok": true}`。

- 簇不存在：404 `说话人不存在`；`speaker_id` 不存在：400 `声纹库中不存在该人物`。
- 只改 `file_speakers`，不重写 `segments.speaker`；导出时用映射表把 `spk0` 换成名字。

### POST `/api/transcripts/{file_id}/speakers/{cluster}/enroll`

用这次转写算出的簇质心直接登记声纹，不需要再上传音频：

```json
{"name": "妈妈", "overwrite": false}
```

```json
{"ok": true, "speaker_id": 4}
```

- 该簇没有质心（未开启 `auto_map_speakers`、句子太短、或引擎不支持声纹）时返回 400 `该说话人还没有声纹质心，请先完成转写`。
- 同名已存在且 `overwrite=false` 时复用原声纹；`overwrite=true` 覆盖其 embedding。

### POST `/api/transcripts/{file_id}/speakers/{cluster}/merge`

自动聚类刻意保守（宁可多留一个簇，也不把只说了几句的人并掉），多出来的簇用这个接口手动合并：

```json
{"target": "spk0"}
```

```json
{"ok": true, "moved": 7}
```

- 会把 `segments.speaker = cluster` 的句子全部改成 `target`，然后删掉该簇的 `file_speakers` 行。
- 目标簇还没名字/没绑声纹时，继承源簇的 `display_name` 与 `speaker_id`。
- `target` 与自己相同：400 `不能把说话人合并到自己`；任一簇不存在：404 `说话人不存在`。

### POST `/api/transcripts/bulk-assign`

把某文件的某个簇绑定到已有声纹，并顺带修正在其它文件里"同名但未绑定"的历史结果：

```json
{"file_id": 13, "cluster": "spk1", "speaker_id": 4}
```

```json
{"ok": true}
```

`speaker_id` 不存在返回 400 `声纹库中不存在该人物`。

### GET `/api/search`

| 参数 | 说明 |
| --- | --- |
| `q` | 必填，至少 1 字符。`strip()` 后长度 ≥ 3 时走 FTS5（trigram）；否则直接 `LIKE` |
| `limit` | 默认 60 |

```json
{
  "items": [
    {"file_id": 13, "name": "2024-05-01_妈妈.m4a", "cluster": "spk0",
     "start_ms": 48200, "end_ms": 50120, "text": "你上次说的那个医院在哪个位置？"}
  ]
}
```

`cluster` 是句子上的簇标签。FTS 查询语法错误会被吞掉并回退到 `LIKE`，不会 500。

### GET `/api/export`

| 参数 | 说明 |
| --- | --- |
| `ids` | 必填，逗号分隔的文件 id，如 `13,14` |
| `format` | `txt`（默认）/ `md` / `srt` / `json`；其它值按 `txt` |

- 单个 id：直接返回文件流，`Content-Disposition` 同时给出 ASCII 兜底名与 RFC 5987 的 `filename*=UTF-8''…`，中文文件名不会乱码。
- 多个 id：返回 `application/zip`，文件名 `transcripts.zip`；其中没有转写结果的文件会被跳过。
- 单个 id 但没有转写：400 `{"detail": "<文件名> 还没有转写结果"}`；`ids` 不是整数：400 `{"detail": "ids 参数格式错误"}`。

各格式内容：

```text
# TXT
# 2024-05-01_妈妈.m4a
# 时长 03:03.40

[00:00.34] 妈妈：喂，妈，你吃饭没有？
```

```markdown
# 2024-05-01_妈妈.m4a

> 时长 03:03.40 · 模型 paraformer-zh · 说话人 2 人

**妈妈** `00:00.34`  
喂，妈，你吃饭没有？
```

```text
1
00:00:00,340 --> 00:00:02,260
妈妈：喂，妈，你吃饭没有？
```

`json` 为 `{name, duration, model, speakers, segments}`，`indent=2`、`ensure_ascii=False`；`segments` 与 `GET /api/transcripts/{id}` 的句子结构一致。

---

## 声纹库

### GET `/api/speakers`

```json
{
  "items": [
    {"id": 4, "name": "妈妈", "color": "#EAB308", "notes": null,
     "created_at": "2025-11-10 21:03:11", "has_embedding": true, "file_count": 213}
  ]
}
```

`file_count` 是 `COUNT(DISTINCT file_id)`，统计 `file_speakers` 里绑定到该人物的录音数。

### POST `/api/speakers`

只建名字，不登记声音（之后再"更新声纹"）：

```json
{"name": "舅舅", "notes": "老家号码"}
```

```json
{"ok": true, "speaker_id": 7}
```

空名返回 400 `姓名不能为空`；同名会复用已有记录并返回其 id。

### POST `/api/speakers/from-file`

从某个录音的毫秒区间截一段音频算 embedding 并登记：

| 参数 | 位置 | 说明 |
| --- | --- | --- |
| `file_id` | query | `files.id` |
| `start_ms` / `end_ms` | query | 区间，建议 ≥ 3 s |
| `name` | query | 人物名 |
| `overwrite` | query | 默认 `false` |

```json
{"ok": true, "speaker_id": 4}
```

- 服务层的 `ValueError` 映射为 400（`文件不存在`、`资料库里的文件已丢失`、`姓名不能为空`）；ffmpeg 截取/解码失败等 `RuntimeError` 映射为 500，`detail` 为异常原文。
- 该接口内部用 ffmpeg 按 `-ss/-to` 截取到临时 wav，再调 CAM++ 算 embedding。
- 想给**已存在的**人物换一段更干净的声纹（不新建、不改名），用下面的 `/enroll-from-file`。

### POST `/api/speakers/{speaker_id}/enroll-from-file`

和 `from-file` 一样从录音里截段算 embedding，但**覆盖已有说话人**的声纹：

| 参数 | 位置 | 说明 |
| --- | --- | --- |
| `speaker_id` | path | 目标人物 |
| `file_id` | query | `files.id` |
| `start_ms` / `end_ms` | query | 区间；`end_ms=0` 表示到文件结尾 |

```json
{"ok": true, "speaker_id": 4}
```

前端「声纹库 → 更新声纹」就是走这个接口：可「从录音库选一段」（推荐，注意挑只有这个人说话的部分），也可「上传本机音频」。

### POST `/api/speakers/enroll`

上传一段音频登记（**原始 body**，不是 multipart，避免额外的表单解析依赖）：

```bash
curl -X POST "http://127.0.0.1:8000/api/speakers/enroll?name=%E8%88%85%E8%88%85" \
     --data-binary @uncle.wav
```

```json
{"ok": true, "speaker_id": 7}
```

- body 为空：400 `音频为空`；解码失败：400 `音频解码失败: …`。
- 内部会转成 16 kHz 单声道再送 CAM++；任意 ffmpeg 能解的格式都行。
- 临时文件在 `.cache/tmp/` 下，用完即删；Windows 上若文件仍被占用则留给下次清理，不影响接口返回。

### POST `/api/speakers/{speaker_id}/enroll`

同样的 body 与行为，登记到指定人物；`speaker_id` 不存在返回 404 `声纹不存在`。

### POST `/api/speakers/{target_id}/merge`

```json
{"source_id": 8}
```

```json
{"ok": true, "target_id": 4}
```

把 `source_id` 名下所有 `file_speakers` 改到 `target_id`；若 target 没有 embedding 则继承 source 的；最后删除 source。自己合并自己：400 `不能与自己合并`；任一不存在：404 `声纹不存在`。

### GET `/api/speakers/{speaker_id}/files`

```json
{
  "items": [
    {"id": 13, "name": "2024-05-01_妈妈.m4a", "duration": 183.4,
     "cluster": "spk0", "confidence": 0.8731}
  ]
}
```

按文件 `mtime` 降序。

### PATCH `/api/speakers/{speaker_id}`

```json
{"name": "妈妈（老家）", "color": "#22C55E", "notes": "常用号码"}
```

返回 `{"ok": true}`。空名 400 `姓名不能为空`；与已有声纹重名 409 `同名声纹已存在`；改名会同步所有 `file_speakers.display_name`。

### DELETE `/api/speakers/{speaker_id}`

```json
{"ok": true}
```

删除人物；`file_speakers.speaker_id` 因外键 `ON DELETE SET NULL` 自动解绑，录音与转写结果保留。
