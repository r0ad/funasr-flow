"""SQLite 访问层：建表、连接、设置读写。

单文件 SQLite + WAL，主进程和 worker 进程并发读写安全。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from .config import DB_PATH, DEFAULT_SETTINGS, ensure_dirs

_local = threading.local()

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS files (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  rel_path    TEXT NOT NULL UNIQUE,
  name        TEXT NOT NULL,
  ext         TEXT,
  size        INTEGER NOT NULL DEFAULT 0,
  mtime       REAL NOT NULL DEFAULT 0,
  duration    REAL,
  status      TEXT NOT NULL DEFAULT 'idle',
  error       TEXT,
  created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at  TEXT DEFAULT CURRENT_TIMESTAMP
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
  queued_at       TEXT DEFAULT CURRENT_TIMESTAMP,
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
  created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at  TEXT DEFAULT CURRENT_TIMESTAMP
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
  created_at  TEXT DEFAULT CURRENT_TIMESTAMP
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


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        ensure_dirs()
        conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
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


def _upgrade_schema(conn: sqlite3.Connection) -> None:
    """老库平滑升级：把 SCHEMA 里新增的列补到已有表上。

    只做加法（ALTER TABLE ADD COLUMN），不改类型、不删列。做法是把 SCHEMA
    跑在内存库里，逐表比对 PRAGMA table_info，缺哪列补哪列；新库不会命中。
    """
    mem = sqlite3.connect(":memory:")
    try:
        mem.executescript(SCHEMA)
        tables = [
            r[0]
            for r in mem.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for t in tables:
            have = {r[1] for r in conn.execute(f"PRAGMA table_info({t})")}
            if not have:
                continue  # 新表已由 SCHEMA 建好
            for _cid, col, ctype, notnull, dflt, _pk in mem.execute(f"PRAGMA table_info({t})"):
                if col in have:
                    continue
                # SQLite 的 ADD COLUMN 只接受常量默认值（CURRENT_TIMESTAMP 不行）
                const_default = (
                    dflt is not None and "CURRENT_TIMESTAMP" not in dflt.upper() and "(" not in dflt
                )
                if not const_default and notnull:
                    continue  # 无默认值的必填列无法安全补，交给发布说明
                ddl = f'ALTER TABLE "{t}" ADD COLUMN "{col}" {ctype or "TEXT"}'
                if const_default:
                    ddl += f" DEFAULT {dflt}"
                conn.execute(ddl)
    finally:
        mem.close()


def init_db() -> None:
    ensure_dirs()
    conn = get_conn()
    # 先补齐老库缺的列（SCHEMA 里的索引可能依赖新列），再建表/建索引
    _upgrade_schema(conn)
    conn.executescript(SCHEMA)
    conn.commit()
    for k, v in DEFAULT_SETTINGS.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
            (k, json.dumps(v, ensure_ascii=False)),
        )
    # 老库的 rel_path 是相对录音目录的，统一升成绝对路径（换目录扫描才不会互相误判 missing）
    base = get_setting("input_dir")
    if base:
        conn.execute(
            "UPDATE files SET rel_path = ? || '/' || rel_path "
            "WHERE rel_path NOT LIKE '/%' AND rel_path NOT LIKE '%:%'",
            (str(base).replace("\\", "/").rstrip("/"),),
        )
    conn.commit()


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
