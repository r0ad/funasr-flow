"""SQLite 访问层：建表、连接、设置读写。

单文件 SQLite，优先 WAL（主进程与内置 worker 并发读写安全）。若资料库所在
文件系统不支持 WAL（典型：Windows 上把 data 目录 bind mount 进 Docker，
9p/grpc-fuse 上没有共享内存），自动退回 DELETE 日志模式，保证照样能跑。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from .config import DB_PATH, DEFAULT_SETTINGS, ensure_dirs

log = logging.getLogger(__name__)

_local = threading.local()
_journal = {"mode": None}  # 探测一次就记住：WAL 还是 DELETE
_init_lock = threading.Lock()
_initialized = {"path": None}

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  rel_path    TEXT NOT NULL UNIQUE,
  source_path TEXT,
  name        TEXT NOT NULL,
  ext         TEXT,
  size        INTEGER NOT NULL DEFAULT 0,
  mtime       REAL NOT NULL DEFAULT 0,
  duration    REAL,
  status      TEXT NOT NULL DEFAULT 'idle',
  error       TEXT,
  created_at  TEXT DEFAULT (datetime('now','localtime')),
  updated_at  TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_files_status ON files(status);
CREATE INDEX IF NOT EXISTS idx_files_name ON files(name);

CREATE TABLE IF NOT EXISTS jobs (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  file_id         INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  status          TEXT NOT NULL DEFAULT 'queued',
  stage           TEXT,
  progress        REAL NOT NULL DEFAULT 0,
  attempts        INTEGER NOT NULL DEFAULT 0,
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  error           TEXT,
  options         TEXT,
  queued_at       TEXT DEFAULT (datetime('now','localtime')),
  started_at      TEXT,
  finished_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, id);

CREATE TABLE IF NOT EXISTS transcripts (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  file_id     INTEGER NOT NULL UNIQUE REFERENCES files(id) ON DELETE CASCADE,
  duration    REAL,
  model       TEXT,
  language    TEXT DEFAULT 'zh',
  asr_ms      INTEGER,
  created_at  TEXT DEFAULT (datetime('now','localtime')),
  updated_at  TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS segments (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  transcript_id INTEGER NOT NULL REFERENCES transcripts(id) ON DELETE CASCADE,
  idx           INTEGER NOT NULL,
  start_ms      INTEGER NOT NULL,
  end_ms        INTEGER NOT NULL,
  speaker       TEXT NOT NULL DEFAULT 'spk0',
  text          TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_seg_tr ON segments(transcript_id, idx);

CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts
  USING fts5(text, content='segments', content_rowid='id', tokenize='trigram');

CREATE TRIGGER IF NOT EXISTS seg_ai AFTER INSERT ON segments BEGIN
  INSERT INTO segments_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS seg_ad AFTER DELETE ON segments BEGIN
  INSERT INTO segments_fts(segments_fts, rowid, text) VALUES('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS seg_au AFTER UPDATE ON segments BEGIN
  INSERT INTO segments_fts(segments_fts, rowid, text) VALUES('delete', old.id, old.text);
  INSERT INTO segments_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TABLE IF NOT EXISTS speakers (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT NOT NULL UNIQUE,
  color       TEXT,
  embedding   BLOB,
  notes       TEXT,
  created_at  TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS file_speakers (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  file_id      INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  cluster      TEXT NOT NULL,
  speaker_id   INTEGER REFERENCES speakers(id) ON DELETE SET NULL,
  display_name TEXT,
  confidence   REAL,
  seconds      REAL DEFAULT 0,
  utt_count    INTEGER DEFAULT 0,
  centroid     BLOB,
  UNIQUE(file_id, cluster)
);

CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS metrics (
  key   TEXT PRIMARY KEY,
  value REAL
);
"""


def _set_journal_mode(conn: sqlite3.Connection) -> str:
    """设置 SQLite 日志模式，WAL 不可用时退回 DELETE。

    返回实际生效的模式。探测结果缓存到 ``_journal``，后续连接直接复用。
    """
    if _journal["mode"]:
        try:
            conn.execute(f"PRAGMA journal_mode={_journal['mode']}")
        except sqlite3.Error:
            pass
        return _journal["mode"]

    mode = "delete"
    try:
        row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
        mode = str(row[0]).lower() if row else "delete"
    except sqlite3.Error as exc:
        log.warning("WAL 不可用（%s），改用 DELETE 日志模式", exc)
    if mode != "wal":
        try:
            conn.execute("PRAGMA journal_mode=DELETE")
        except sqlite3.Error:
            pass
        log.warning(
            "资料库所在文件系统不支持 WAL（journal_mode=%s），已退回 DELETE："
            "功能不受影响，并发写入稍慢。绑定挂载（Windows 上的 bind mount）属常见原因。",
            mode,
        )
        _journal["mode"] = "DELETE"
    else:
        _journal["mode"] = "WAL"
    return _journal["mode"]


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        ensure_dirs()
        conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        _set_journal_mode(conn)
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def is_initialized() -> bool:
    """当前库是否已在本进程建过表（worker 线程据此跳过重复初始化）。"""
    return _initialized["path"] == str(DB_PATH)


def init_db() -> None:
    """建表并补齐默认设置。幂等，且进程内只真正执行一次。

    服务启动时 Web 线程与 worker 线程可能同时走到这里，重复执行 schema
    会互相抢写锁（``database is locked``），因此加锁 + 只跑一次。
    """
    ensure_dirs()
    key = str(DB_PATH)
    with _init_lock:
        if _initialized["path"] == key:
            return
        conn = get_conn()
        conn.executescript(SCHEMA)
        conn.commit()
        for k, v in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
                (k, json.dumps(v, ensure_ascii=False)),
            )
        conn.commit()
        _initialized["path"] = key


def get_setting(key: str, default: Any = None) -> Any:
    row = get_conn().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row is None:
        return DEFAULT_SETTINGS.get(key, default)
    try:
        return json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return row["value"]


def set_setting(key: str, value: Any) -> None:
    with tx() as c:
        c.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )


def all_settings() -> dict[str, Any]:
    out = dict(DEFAULT_SETTINGS)
    for row in get_conn().execute("SELECT key, value FROM settings"):
        try:
            out[row["key"]] = json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            out[row["key"]] = row["value"]
    return out


def get_metric(key: str, default: float = 0.0) -> float:
    row = get_conn().execute("SELECT value FROM metrics WHERE key=?", (key,)).fetchone()
    return float(row["value"]) if row else default


def set_metric(key: str, value: float) -> None:
    with tx() as c:
        c.execute(
            "INSERT INTO metrics(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, float(value)),
        )
