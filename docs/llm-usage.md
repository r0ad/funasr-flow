# 让 LLM / agent 驱动 FunASR Flow

这个文档写给"要替人操作本项目"的模型或脚本：把一段自然语言需求（"帮我把这些录音整理成文字，
找出提到医院的部分"）翻译成确定的命令序列。所有命令都可直接复制执行。

约定：`BASE=http://127.0.0.1:8000`（用 `--port` 启动时改成对应端口）。

## 0. 先确认服务活着

```bash
curl -s $BASE/api/health | python -c "import sys,json; d=json.load(sys.stdin); print(d['gpu'], d['ffmpeg'], d['worker'])"
```

- `health.worker.thread == true` 说明队列会自己跑；`false` 时任务只会停在 queued（此时需要另起 `funasr-flow worker`）。
- `health.ml.models.asr` 是可选识别模型清单；`health.gpu.available == false` 时推理会落到 CPU（很慢）。

## 1. 扫描录音目录

```bash
curl -s -X POST $BASE/api/files/scan -H 'Content-Type: application/json' -d '{"dir":"CallRecord"}'
# {"added":980,"updated":0,"missing":0,"total":980}
```

不传 `dir` 就扫当前设置里的 `input_dir`。新增/改动的文件会入库为 `idle`，并顺带补齐时长。

**不知道有哪些目录可选**：`GET /api/browse?path=` 会返回盘符/根、父目录和每个子目录里的音频数量
（`entries[].audio`），可以用它逐层往下找录音目录——界面上的「选录音目录」就是调这个接口。

```bash
curl -s "$BASE/api/browse?path=D:/" | python -c "import json,sys;d=json.load(sys.stdin);print(d['path']);[print(' ',e['name'],e['audio']) for e in d['entries'][:10]]"
```

**文件不在本机目录里（或只有几个）**：直接上传，比让 agent 去猜路径省事。

```bash
# 文件名放查询参数，body 是文件原始字节；上传后是 idle，需要再入队
curl -s -X POST "$BASE/api/files/upload?name=10月13日%20妈.m4a" \
     -H 'Content-Type: application/octet-stream' --data-binary @"D:/临时/10月13日 妈.m4a"
# {"ok":true,"file":{"id":981,"name":"10月13日 妈.m4a","status":"idle"}}

# 然后照常入队
curl -s -X POST $BASE/api/files/batch -H 'Content-Type: application/json' -d '{"ids":[981],"action":"enqueue"}'
```

上传的副本放在 `data/uploads/`（删记录时会连文件一起删），扫描进来的原始录音不会被删。

## 2. 看清单、挑要处理的文件

```bash
# 未处理的（idle,missing），限制条数
curl -s "$BASE/api/files?status=idle&limit=20&sort=duration&order=asc"
# 按文件名找
curl -s "$BASE/api/files?q=妈&limit=10"
```

返回 `items[].id / name / duration / status / has_transcript / segment_count / speakers`。
**转写请求用 `id`**。

## 3. 入队与等待

```bash
curl -s -X POST $BASE/api/files/batch -H 'Content-Type: application/json' \
     -d '{"ids":[11,23,27],"action":"enqueue"}'
# {"ok":true,"queued":3,"skipped":0,"job_ids":[6,7,8]}
```

- `action`：`enqueue`（转写）、`reset`（清掉旧结果重跑）、`delete`（从库中移除，不删源文件）。
- 已排队/运行中的文件会被跳过（`skipped`），可以安全重发。
- 查看进度：

```bash
curl -s $BASE/api/state
# queue.queued/running/done/failed/canceled，active={stage,progress,name,duration}
```

- `active.stage`：`convert`（转码）→ `asr`（识别+聚类）→ `diarize`（声纹匹配）→ `save`。
- 需要实时推送时订阅 SSE：`curl -N $BASE/api/events`（每 0.7 秒比对一次，有变化才推 JSON）。
- 单文件处理时长约为录音时长的 0.05–0.3 倍（GPU）；**不要并发入队**，队列本身串行。

## 4. 取转写结果

```bash
curl -s $BASE/api/transcripts/23
```

结构：

```json
{
  "file": {"id": 23, "name": "10月13日 妈.m4a", "duration": 66.96, "status": "done"},
  "transcript": {"model": "...", "asr_ms": 4520, "language": "zh"},
  "segments": [{"idx": 0, "start_ms": 3030, "end_ms": 5130, "speaker": "spk1", "text": "…"}],
  "speakers": [{"cluster": "spk1", "display_name": null, "speaker_id": null,
                "confidence": null, "seconds": 39.6, "utt_count": 22, "color": "#FF7A59"}]
}
```

转成可读文本（无第三方依赖）：

```bash
curl -s $BASE/api/transcripts/23 | python -c "
import sys, json
d = json.load(sys.stdin)
names = {s['cluster']: (s['display_name'] or s['cluster']) for s in d['speakers']}
for s in d['segments']:
    m, sec = divmod(s['start_ms'] // 1000, 60)
    print(f'[{m:02d}:{sec:02d}] {names.get(s[\"speaker\"], s[\"speaker\"])}：{s[\"text\"]}')
"
```

`file.status == failed` 时错误在 `GET /api/files/{id}` 的 `error` 字段（多半是缺 ffmpeg 或显存不足）。

## 5. 说话人命名（把 spk1 变成"妈妈"）

```bash
# a) 只在这一次转写里改名（不影响其他文件）
curl -s -X PATCH $BASE/api/transcripts/23/speakers/spk1 \
     -H 'Content-Type: application/json' -d '{"display_name":"妈妈"}'

# b) 建一个全局声纹并绑定：之后别的录音会自动匹配到她
curl -s -X POST $BASE/api/transcripts/23/speakers/spk1/enroll \
     -H 'Content-Type: application/json' -d '{"name":"妈妈"}'

# c) 把这一簇绑到已注册的声纹上（id 来自 GET /api/speakers）
curl -s -X PATCH $BASE/api/transcripts/23/speakers/spk1 \
     -H 'Content-Type: application/json' -d '{"speaker_id":2}'

# d) 一句话纠正归属（人工听出来的错分）
curl -s -X PATCH $BASE/api/transcripts/23/segments/7 \
     -H 'Content-Type: application/json' -d '{"speaker":"spk0"}'
```

# e) 用某条录音的一段音频，给已存在的声纹换一段更干净的（覆盖，不新建）
curl -s -X POST "$BASE/api/speakers/4/enroll-from-file?file_id=23&start_ms=24500&end_ms=33100"

# f) 反过来：从录音的某一段直接新建一个声纹（名字重名则复用并覆盖声纹）
curl -s -X POST "$BASE/api/speakers/from-file?file_id=23&start_ms=24500&end_ms=33100&name=%E5%A6%88%E5%A6%88"
```

批量场景：`POST /api/transcripts/bulk-assign` body `{"file_id":23,"cluster":"spk1","speaker_id":2}`。

声纹登记要点：挑 **5 秒以上、只有这一个人说话、没有杂音**的一段；登记一次后，其它录音里同一人会被自动匹配（阈值 `min_speaker_sim`，默认 0.62）并套用名字。

簇分多了（同一个人被拆成 spk1/spk2）：`POST /api/transcripts/23/speakers/spk2/merge` body `{"target":"spk0"}`，返回 `{"ok":true,"moved":7}` 表示 7 句改归目标人。

## 6. 全文搜索

```bash
curl -s "$BASE/api/search?q=医院&limit=20"
```

- 查询词 ≥ 3 个字符走 FTS5 trigram；1–2 个字符走 LIKE。
- 结果按文件分组，每段带 `start_ms`，可直接拼 `GET /api/transcripts/{id}` 取上下文。
- 前端可深链：`/files/23?t=16710&q=排骨`（跳转到该时间点并高亮）。

## 7. 导出

```bash
curl -s "$BASE/api/export?ids=23&format=srt" -o 23.srt
curl -s "$BASE/api/export?ids=11,23,27&format=md" -o 选中录音.zip   # 多文件自动打 zip
```

`format`：`txt`（纯文本）/ `md`（带时间轴与说话人）/ `srt`（字幕）/ `json`（结构化）。
中文文件名走 RFC 5987，curl 直接 `-o` 成中文名也没问题。

## 8. 用 CLI 代替 curl（更省 token）

```bash
funasr-flow api list --status done --json          # 文件清单
funasr-flow api transcript 23                      # [mm:ss] 说话人：文本
funasr-flow api transcript 23 --json                # 原始结构
funasr-flow api enqueue 11 23 27                    # 入队
funasr-flow api state                               # 队列快照
funasr-flow api search 医院 --limit 20
funasr-flow api export 23 -f srt -o .cache/tmp/23.srt
funasr-flow api speakers                            # 声纹库
funasr-flow api health
```

`--server` 可覆盖地址（默认读 `FLOW_SERVER`，再默认 `http://127.0.0.1:8000`）；`--json` 时输出即 API 原样 JSON。

## 9. 用 MCP 接入（Claude Desktop / DSH 等）

```json
{
  "mcpServers": {
    "funasr-flow": {
      "command": "funasr-flow",
      "args": ["mcp", "--server", "http://127.0.0.1:8000"]
    }
  }
}
```

暴露的工具：`list_files` `get_file` `get_transcript` `search` `queue_state`
`enqueue_transcribe` `cancel_jobs` `scan_library` `list_speakers` `health`。
工具直接返回文本，模型不需要再拼 JSON。

## 10. 常见任务配方

| 用户说 | 你要做 |
| --- | --- |
| "把这些录音都转成文字" | `POST /api/files/scan` → `GET /api/files?status=idle` 收集 id → `POST /api/files/batch` enqueue → 轮询 `/api/state` 直到 `queued+running == 0` |
| "妈那段聊了啥" | `GET /api/files?q=妈` → 取 `status=done` 的最新一条 → `GET /api/transcripts/{id}` → 按说话人分段复述 |
| "有没有提到寄东西/医院" | `GET /api/search?q=寄` / `?q=医院` → 命中文件+时间点 → 取该文件转写，摘上下文 |
| "整理成会议纪要" | 取 `segments`，按 `speaker` 合并连续发言，抽要点；需要引用就带 `[mm:ss]` |
| "把这个人的话单独提出来" | `speakers` 里选 `cluster`（或先 `PATCH display_name` 命名）→ 过滤 `segments` |
| "导出字幕" | `GET /api/export?ids=…&format=srt` |

## 11. 别踩的坑

- 别并发 enqueue 同一批文件（会被 `skipped`，不是错误）；队列本来就串行。
- 别在 `active != null` 时重启服务，否则 running 的任务会被 `reset_stuck_jobs` 标失败，需要 `action=reset` 重跑。
- 转写结果里的说话人是本文件内的 `spk0/spk1`，**跨文件不保证同一人**；要跨文件一致就必须登记声纹（第 5 节）。
- 内置 worker 与 Web 同进程；`FLOW_WORKER=0` 启动时任务不会自己跑。
- 把所有录音转写会被真实姓名/号码污染上下文：做摘要时用"说话人1/2"，不要把整段原文贴回给用户。
