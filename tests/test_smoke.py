"""最小冒烟测试。

原则：
- **不下载模型**、不联网、不调用 ffmpeg、不依赖 GPU；
- 只测纯逻辑：时间格式化、说话人命名/配色、目录扫描、任务队列、导出格式、声纹簇合并；
- 数据库用 tmp_path 下的临时 SQLite，替换 `db.DB_PATH` 与 `db.ensure_dirs`，
  绝不碰仓库里真实的 `data/flow.db`。
"""

from __future__ import annotations

import json

import numpy as np
import pytest
from fastapi import HTTPException

from backend.app import db
from backend.app.api import deps, transcripts
from backend.app.engines.base import Utterance
from backend.app.services import pipeline, queue, scanner


# --------------------------------------------------------------------------- 夹具
@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """把 db 模块指向临时 SQLite，并建好 schema。"""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "flow.db", raising=False)
    monkeypatch.setattr(db, "ensure_dirs", lambda: None, raising=False)
    # 连接是 threading.local 缓存的，先把当前线程里的旧连接清掉
    monkeypatch.setattr(db._local, "conn", None, raising=False)
    db.init_db()
    yield db
    conn = getattr(db._local, "conn", None)
    if conn is not None:
        conn.close()
    monkeypatch.setattr(db._local, "conn", None, raising=False)


def _add_file(rel_path: str, name: str, size: int = 1024, status: str = "idle") -> int:
    with db.tx() as c:
        cur = c.execute(
            "INSERT INTO files(rel_path, name, ext, size, mtime, status) VALUES(?,?,?,?,?,?)",
            (rel_path, name, rel_path.rsplit(".", 1)[-1].lower(), size, 0.0, status),
        )
    return int(cur.lastrowid)


def _add_transcript(file_id: int, duration: float = 75.4) -> int:
    with db.tx() as c:
        cur = c.execute(
            "INSERT INTO transcripts(file_id, duration, model, language) VALUES(?,?,?,?)",
            (file_id, duration, "paraformer-zh", "zh"),
        )
    return int(cur.lastrowid)


def _add_segment(
    transcript_id: int, idx: int, start_ms: int, end_ms: int, speaker: str, text: str
) -> None:
    with db.tx() as c:
        c.execute(
            "INSERT INTO segments(transcript_id, idx, start_ms, end_ms, speaker, text) "
            "VALUES(?,?,?,?,?,?)",
            (transcript_id, idx, start_ms, end_ms, speaker, text),
        )


def _add_file_speaker(
    file_id: int,
    cluster: str,
    display_name: str | None = None,
    seconds: float = 1.0,
    utt_count: int = 1,
) -> None:
    with db.tx() as c:
        c.execute(
            "INSERT INTO file_speakers(file_id, cluster, display_name, seconds, utt_count) "
            "VALUES(?,?,?,?,?)",
            (file_id, cluster, display_name, seconds, utt_count),
        )


# --------------------------------------------------------------------- 格式化函数
def test_fmt_clock_srt_timestamps() -> None:
    assert transcripts._fmt_clock(0) == "00:00:00,000"
    assert transcripts._fmt_clock(1500) == "00:00:01,500"
    # 1h02m03.456s
    assert transcripts._fmt_clock(3_723_456) == "01:02:03,456"


def test_fmt_short_mm_ss() -> None:
    assert transcripts._fmt_short(0) == "00:00.00"
    assert transcripts._fmt_short(75_400) == "01:15.40"
    # 超过一小时按分钟累计，不折成小时
    assert transcripts._fmt_short(3_723_456) == "62:03.46"


def test_default_speaker_name() -> None:
    assert transcripts._default_name("spk0") == "说话人1"
    assert transcripts._default_name("cluster_12") == "说话人13"
    assert transcripts._default_name("") == "说话人1"


def test_speaker_color_cycles_by_trailing_digit() -> None:
    colors = deps.SPEAKER_COLORS
    assert len(colors) == 8
    assert deps.speaker_color("spk0") == colors[0]
    assert deps.speaker_color("x3") == colors[3]
    assert deps.speaker_color("spk8") == colors[0]  # 8 取模回到第 1 个
    assert deps.speaker_color("") == colors[0]


# ------------------------------------------------------------------------- 扫描器
def test_audio_exts_cover_common_formats() -> None:
    for ext in (".m4a", ".mp3", ".wav", ".amr", ".silk"):
        assert ext in scanner.AUDIO_EXTS
    assert ".txt" not in scanner.AUDIO_EXTS
    assert ".db" not in scanner.AUDIO_EXTS


def test_walk_recurses_and_filters_by_suffix(tmp_path) -> None:
    (tmp_path / "a.m4a").write_bytes(b"a")
    (tmp_path / "note.txt").write_text("x", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.MP3").write_bytes(b"b")  # 大小写不敏感
    audio_dir = tmp_path / "dir.wav"  # 目录名像音频，应该被递归而不是当成文件
    audio_dir.mkdir()
    (audio_dir / "c.wav").write_bytes(b"c")

    names = {e.name for e in scanner._walk(tmp_path)}
    assert names == {"a.m4a", "b.MP3", "c.wav"}


def test_scan_add_update_missing_roundtrip(temp_db, tmp_path) -> None:
    root = tmp_path / "rec"
    (root / "sub").mkdir(parents=True)
    a = root / "a.m4a"
    a.write_bytes(b"1234")
    b = root / "sub" / "b.wav"
    b.write_bytes(b"12")

    first = scanner.scan(str(root))
    assert first == {"added": 2, "updated": 0, "missing": 0, "total": 2}

    rows = {r["rel_path"] for r in temp_db.get_conn().execute("SELECT rel_path FROM files")}
    assert rows == {"a.m4a", "sub/b.wav"}  # 统一用 / 分隔

    # 再扫一次：没有任何变化
    assert scanner.scan(str(root)) == {"added": 0, "updated": 0, "missing": 0, "total": 2}

    # 文件被替换 -> updated；文件消失 -> missing
    a.write_bytes(b"123456")
    b.unlink()
    third = scanner.scan(str(root))
    assert third["added"] == 0
    assert third["updated"] == 1
    assert third["missing"] == 1
    assert third["total"] == 1


def test_scan_missing_directory_raises(temp_db, tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        scanner.scan(str(tmp_path / "nope"))


# --------------------------------------------------------------------------- 队列
def test_queue_enqueue_claim_and_fail(temp_db) -> None:
    fid = _add_file("CallRecord/one.m4a", "one.m4a")

    res = queue.enqueue([fid])
    assert res["queued"] == 1 and res["skipped"] == 0 and len(res["job_ids"]) == 1
    job_id = res["job_ids"][0]

    # 同一个文件重复入队会被跳过，并返回已有 job id
    again = queue.enqueue([fid])
    assert again == {"queued": 0, "skipped": 1, "job_ids": [job_id]}

    claimed = queue.claim_next()
    assert claimed == {"job_id": job_id, "file_id": fid}
    assert queue.claim_next() is None  # 队列已空

    assert queue.is_cancel_requested(job_id) is False
    assert queue.request_cancel(job_id) is True
    assert queue.is_cancel_requested(job_id) is True

    queue.mark_failed(job_id, "boom: 模型加载失败")
    job = (
        temp_db.get_conn()
        .execute("SELECT status, error FROM jobs WHERE id=?", (job_id,))
        .fetchone()
    )
    assert job["status"] == "failed"
    assert "boom" in job["error"]
    f = temp_db.get_conn().execute("SELECT status FROM files WHERE id=?", (fid,)).fetchone()
    assert f["status"] == "failed"


def test_queue_enqueue_unknown_id_is_skipped(temp_db) -> None:
    assert queue.enqueue([9999]) == {"queued": 0, "skipped": 1, "job_ids": []}
    assert queue.enqueue([]) == {"queued": 0, "skipped": 0, "job_ids": []}


def test_queue_cancel_queued_returns_file_to_idle(temp_db) -> None:
    fid = _add_file("CallRecord/two.m4a", "two.m4a")
    job_id = queue.enqueue([fid])["job_ids"][0]
    assert queue.request_cancel(job_id) is True
    job = temp_db.get_conn().execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert job["status"] == "canceled"
    f = temp_db.get_conn().execute("SELECT status FROM files WHERE id=?", (fid,)).fetchone()
    assert f["status"] == "idle"


# --------------------------------------------------------------------------- 导出
def test_export_txt_srt_md_json(temp_db) -> None:
    fid = _add_file("CallRecord/通话 01.m4a", "通话 01.m4a")
    tid = _add_transcript(fid, duration=75.4)
    _add_segment(tid, 0, 0, 1500, "spk0", "你好")
    _add_segment(tid, 1, 75_400, 80_000, "spk1", "在的")
    _add_file_speaker(fid, "spk0", display_name="张三", seconds=2.0, utt_count=1)
    _add_file_speaker(fid, "spk1", display_name=None, seconds=1.0, utt_count=1)

    name, blob = transcripts._export_text(fid, "txt")
    txt = blob.decode("utf-8")
    assert name == "通话 01.txt"
    assert "# 通话 01.m4a" in txt
    assert "[00:00.00] 张三：你好" in txt
    assert "[01:15.40] 说话人2：在的" in txt  # spk1 没有显示名 -> 按簇号兜底

    name, blob = transcripts._export_text(fid, "srt")
    srt = blob.decode("utf-8")
    assert name == "通话 01.srt"
    assert srt.startswith("1\n00:00:00,000 --> 00:00:01,500\n张三：你好\n")
    assert "2\n00:01:15,400 --> 00:01:20,000\n说话人2：在的" in srt

    name, blob = transcripts._export_text(fid, "json")
    payload = json.loads(blob.decode("utf-8"))
    assert name == "通话 01.json"
    assert payload["name"] == "通话 01.m4a"
    assert payload["model"] == "paraformer-zh"
    assert payload["duration"] == 75.4
    assert [s["text"] for s in payload["segments"]] == ["你好", "在的"]
    assert payload["speakers"][0]["name"] == "张三"
    assert "你好".encode() in blob  # ensure_ascii=False，中文不转义

    name, blob = transcripts._export_text(fid, "md")
    md = blob.decode("utf-8")
    assert name == "通话 01.md"
    assert md.startswith("# 通话 01.m4a")
    assert "**张三** `00:00.00`" in md

    # 未知格式回退成 txt
    assert transcripts._export_text(fid, "weird")[0].endswith(".txt")


def test_export_without_transcript_raises_400(temp_db) -> None:
    fid = _add_file("CallRecord/none.m4a", "none.m4a")
    with pytest.raises(HTTPException) as exc:
        transcripts._export_text(fid, "txt")
    assert exc.value.status_code == 400


def test_export_missing_file_raises_404(temp_db) -> None:
    with pytest.raises(HTTPException) as exc:
        transcripts._export_text(424242, "txt")
    assert exc.value.status_code == 404


# --------------------------------------------------------------- 声纹簇合并逻辑
def _unit(*vals: float) -> np.ndarray:
    v = np.asarray(vals, dtype=np.float32)
    return v / np.linalg.norm(v)


def test_merge_similar_clusters_merges_close_and_rewrites_utterances() -> None:
    a = _unit(1.0, 0.0, 0.0)
    b_close = _unit(1.0, 0.05, 0.0)  # 余弦 ~0.999 -> 合并
    c_far = _unit(0.0, 1.0, 0.0)  # 正交 -> 不合并

    utts = [
        Utterance(0, 1000, "第一句", "spk0"),
        Utterance(1000, 2000, "第二句", "spk1"),
        Utterance(2000, 3000, "第三句", "spk2"),
    ]
    out, alias = pipeline.merge_similar_clusters(
        {"spk0": a, "spk1": b_close, "spk2": c_far}, utts, threshold=0.72
    )

    assert set(out) == {"spk0", "spk2"}
    assert alias["spk1"] == "spk0"
    assert [u.cluster for u in utts] == ["spk0", "spk0", "spk2"]
    for vec in out.values():
        assert abs(float(np.linalg.norm(vec)) - 1.0) < 1e-5  # 合并后重新归一化


def test_merge_similar_clusters_identity_when_all_distinct() -> None:
    utts = [Utterance(0, 1000, "hi", "spk1"), Utterance(1000, 2000, "yo", "spk0")]
    centroids = {"spk0": _unit(1.0, 0.0), "spk1": _unit(0.0, 1.0)}
    out, alias = pipeline.merge_similar_clusters(centroids, utts, threshold=0.72)
    assert out is centroids
    assert alias == {"spk0": "spk0", "spk1": "spk1"}
    assert utts[0].cluster == "spk1"


def test_merge_drops_clusters_without_utterances() -> None:
    """没有句子的簇要丢掉：它给不了文字贴标签，也不该当吸收目标。"""
    utts = [
        Utterance(0, 3000, "第一句", "spk0"),
        Utterance(3000, 5000, "第二句", "spk1"),
    ]
    centroids = {
        "spk0": _unit(1.0, 0.0),
        "spk1": _unit(0.0, 1.0),
        "spk2": _unit(1.0, 0.0),  # 没有任何句子，且和 spk0 完全同向
    }
    out, alias = pipeline.merge_similar_clusters(centroids, utts, threshold=0.72)
    assert set(out) == {"spk0", "spk1"}
    assert alias == {"spk0": "spk0", "spk1": "spk1"}


# --------------------------------------------------------------------- 设置 / 应用
def test_settings_roundtrip_includes_defaults(temp_db) -> None:
    assert temp_db.set_setting("unit_test_key", {"a": 1}) is None
    assert temp_db.get_setting("unit_test_key") == {"a": 1}
    assert temp_db.get_setting("missing_key", "fallback") == "fallback"
    assert "input_dir" in temp_db.all_settings()  # DEFAULT_SETTINGS 已写入


def test_fastapi_app_imports_without_asr_extras() -> None:
    """不装 funasr/torch 时也必须能 import app —— CI 的 lint/test job 就靠这个前提。"""
    from backend.app.main import app

    assert app.title == "FunASR Flow"
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/health" in paths


# --------------------------------------------------------------- 临时文件 / 迁移
def test_temp_path_is_closed_and_removable(tmp_path, monkeypatch) -> None:
    """Windows 上 mkstemp 的句柄不关会导致 unlink 报 WinError 32。"""
    from backend.app import config

    monkeypatch.setattr(config, "TMP_DIR", tmp_path, raising=False)
    p = config.temp_path(suffix=".wav")
    p.write_bytes(b"RIFF")
    config.safe_unlink(p)  # 句柄已关，删除必须成功
    assert not p.exists()
    config.safe_unlink(p)  # 重复删除不抛异常


def test_init_db_upgrades_old_schema(tmp_path, monkeypatch) -> None:
    """老库缺列时 init_db 只做加法，不重建表（数据保留）。"""
    import sqlite3

    from backend.app import db as dbmod

    old = tmp_path / "old.db"
    conn = sqlite3.connect(old)
    conn.executescript(
        "CREATE TABLE files (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " rel_path TEXT NOT NULL UNIQUE, name TEXT NOT NULL);"
        "INSERT INTO files(rel_path, name) VALUES('a.m4a', 'a');"
        "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(dbmod, "DB_PATH", old, raising=False)
    monkeypatch.setattr(dbmod, "ensure_dirs", lambda: None, raising=False)
    monkeypatch.setattr(dbmod._local, "conn", None, raising=False)
    dbmod.init_db()
    cols = {r[1] for r in dbmod.get_conn().execute("PRAGMA table_info(files)")}
    assert {"status", "duration", "updated_at"} <= cols
    rows = dbmod.get_conn().execute("SELECT COUNT(*) n FROM files").fetchone()
    assert rows["n"] == 1  # 老数据没被删


def test_merge_keeps_minority_speaker_with_several_sentences() -> None:
    """只说了几句的第二说话人不能被当成碎片簇吸收掉。"""
    main = _unit(1.0, 0.0)
    frag = _unit(0.6, 0.8)  # 1 句 4 秒，与主簇余弦 0.6 -> 碎片，吸收
    minor = _unit(0.55, -0.835)  # 6 句 12 秒 -> 是真人，保留（与碎片簇不像）
    utts = [Utterance(0, 30000, "主说话人", "spk0")]
    utts.append(Utterance(0, 4000, "嗯", "spk1"))
    for i in range(6):
        utts.append(Utterance(0, 2000, f"第二个人第{i}句", "spk2"))

    out, alias = pipeline.merge_similar_clusters(
        {"spk0": main, "spk1": frag, "spk2": minor}, utts, threshold=0.72
    )

    assert set(out) == {"spk0", "spk2"}
    assert alias["spk1"] == "spk0"
    assert alias["spk2"] == "spk2"
    assert [u.cluster for u in utts if u.text != "嗯"] == ["spk0"] + ["spk2"] * 6


def test_safe_name_strips_path_and_requires_audio_ext() -> None:
    """上传的文件名只保留 basename，且必须是音频扩展名。"""
    from backend.app.api.files import _safe_name

    assert _safe_name("C:\\Users\\r0ad\\通话 记录 (1).m4a") == "通话 记录 (1).m4a"
    assert _safe_name("../../etc/passwd.wav") == "passwd.wav"
    assert _safe_name("a<b>c|d.m4a") == "a_b_c_d.m4a"
    with pytest.raises(HTTPException):
        _safe_name("木马.exe")


def test_resolve_audio_accepts_absolute_rel_path(temp_db, tmp_path) -> None:
    """上传的录音以绝对路径入库，resolve_audio 要能直接用。"""
    audio = tmp_path / "上传的录音.m4a"
    audio.write_bytes(b"fake")
    fid = _add_file(str(audio.resolve()), audio.name, size=4)
    assert deps.resolve_audio(fid) == audio.resolve()
