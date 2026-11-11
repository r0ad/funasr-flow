"""资料库导入（`services/library.py`）的离线单元测试。

原则与 `tests/test_smoke.py` 一致：
- 不联网、不下载模型、不调用 ffmpeg；
- 数据库换成 `tmp_path` 下的临时 SQLite（替换 `db.DB_PATH`）；
- 资料库目录用 monkeypatch 指到 `tmp_path/library`，绝不碰仓库里真实的 `data/library`。

注意：`library.py` 在 import 时用 `from ..config import LIBRARY_DIR` 绑定了这个名字，
所以测试必须 patch `library.LIBRARY_DIR`（而不是 `config.LIBRARY_DIR`）。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.app import db
from backend.app.services import library
from backend.app.services.scanner import abs_key


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


@pytest.fixture()
def library_dir(tmp_path, monkeypatch):
    """资料库指向临时目录；模块级 `LIBRARY_DIR` 一起换掉。"""
    lib = tmp_path / "library"
    lib.mkdir()
    monkeypatch.setattr(library, "LIBRARY_DIR", lib, raising=False)
    return lib


def _audio(path: Path, data: bytes = b"fake-audio") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _files(temp_db) -> list:
    return temp_db.get_conn().execute("SELECT * FROM files ORDER BY id").fetchall()


# ----------------------------------------------------------------------- import_one
def test_import_one_added_copies_into_library(temp_db, library_dir, tmp_path) -> None:
    """新文件：复制进资料库、DB 落一行、source_path 记来源。"""
    src = _audio(tmp_path / "手机" / "通话 01.m4a", b"abc123")
    res = library.import_one(src)

    assert res["status"] == "added"
    target = library.file_path(res["path"])
    assert target != src
    assert target.parent == library_dir  # 落在资料库里，不是原地引用
    assert target.name == "通话 01.m4a"
    assert target.read_bytes() == b"abc123"
    assert src.exists()  # 原目录只当来源，绝不动它

    rows = _files(temp_db)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == res["id"]
    assert row["rel_path"] == res["path"]
    assert Path(row["rel_path"]).is_absolute()  # rel_path 是资料库里的绝对路径
    assert row["source_path"] == abs_key(src)  # 来源留档，用于查重与展示
    assert row["size"] == 6
    assert row["status"] == "idle"


def test_import_one_same_source_same_size_is_skipped(temp_db, library_dir, tmp_path) -> None:
    """同一来源、同样大小：跳过，且不会重新复制（目标 mtime 不变）。"""
    src = _audio(tmp_path / "src" / "a.m4a", b"1234")
    first = library.import_one(src)
    target = library.file_path(first["path"])
    copied = target.stat().st_mtime_ns

    # 把源文件 mtime 改掉再导：只要 size 没变就算同一个文件
    os.utime(src, (1_000_000, 1_000_000))
    second = library.import_one(src)

    assert second["status"] == "skipped"
    assert second["id"] == first["id"]
    assert len(_files(temp_db)) == 1
    assert [p.name for p in library_dir.iterdir()] == ["a.m4a"]
    assert target.stat().st_mtime_ns == copied  # 没有重新复制


def test_import_one_changed_content_is_replaced_and_drops_transcript(
    temp_db, library_dir, tmp_path
) -> None:
    """同名同来源但 size 变了：覆盖资料库副本，清掉旧转写，不新增行。"""
    src = _audio(tmp_path / "src" / "a.m4a", b"old")
    first = library.import_one(src)
    fid = first["id"]
    target = library.file_path(first["path"])

    # 手工造一条转写，验证 replaced 会把它清掉
    with temp_db.tx() as c:
        c.execute(
            "INSERT INTO transcripts(file_id, duration, model, language) VALUES(?,?,?,?)",
            (fid, 3.5, "paraformer-zh", "zh"),
        )
    assert temp_db.get_conn().execute("SELECT COUNT(*) n FROM transcripts").fetchone()["n"] == 1

    src.write_bytes(b"new-content")  # size 3 -> 11
    second = library.import_one(src)

    assert second["status"] == "replaced"
    assert second["id"] == fid
    assert library.file_path(second["path"]) == target  # 覆盖原副本，不新建文件
    assert target.read_bytes() == b"new-content"
    assert [p.name for p in library_dir.iterdir()] == ["a.m4a"]

    assert temp_db.get_conn().execute("SELECT COUNT(*) n FROM transcripts").fetchone()["n"] == 0
    row = temp_db.get_conn().execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()
    assert row["size"] == 11
    assert row["status"] == "idle"


def test_import_one_copy_and_link_modes_both_yield_usable_copy(
    temp_db, library_dir, tmp_path
) -> None:
    """copy 与 link 两种模式都能得到可播放的副本，源文件都还在。"""
    for i, mode in enumerate(("copy", "link")):
        src = _audio(tmp_path / f"src{i}" / f"rec{i}.m4a", f"data-{i}".encode())
        res = library.import_one(src, mode=mode)
        assert res["status"] == "added"
        target = library.file_path(res["path"])
        assert target.read_bytes() == f"data-{i}".encode()
        assert src.exists()
        assert len(_files(temp_db)) == i + 1


def test_import_one_link_is_hardlink_on_same_fs(temp_db, library_dir, tmp_path) -> None:
    """同盘时 link 建的是硬链接（同一 inode）；建不了就回退复制，副本仍可用。"""
    src = _audio(tmp_path / "src" / "b.m4a", b"shared-bytes")

    # 先探测当前文件系统是否支持硬链接，决定断言强度
    probe = library_dir / "probe.tmp"
    try:
        os.link(src, probe)
        can_link = True
    except OSError:
        can_link = False
    finally:
        probe.unlink(missing_ok=True)

    res = library.import_one(src, mode="link")
    target = library.file_path(res["path"])
    assert target.exists()
    assert target.read_bytes() == b"shared-bytes"

    if can_link and os.stat(target).st_ino:
        assert os.stat(target).st_ino == os.stat(src).st_ino  # 硬链接 = 同一份数据
        src.unlink()  # 删掉来源，资料库副本不受影响
        assert target.exists() and target.read_bytes() == b"shared-bytes"


def test_import_one_rejects_missing_and_non_audio(temp_db, library_dir, tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        library.import_one(tmp_path / "nope.m4a")
    note = _audio(tmp_path / "note.txt", b"hello")
    with pytest.raises(ValueError):
        library.import_one(note)


# -------------------------------------------------------------------- unique_target
def test_unique_target_appends_suffix_on_name_conflict(library_dir) -> None:
    assert library.unique_target("a.m4a") == library_dir / "a.m4a"
    (library_dir / "a.m4a").write_bytes(b"1")
    assert library.unique_target("a.m4a") == library_dir / "a (2).m4a"
    (library_dir / "a (2).m4a").write_bytes(b"1")
    assert library.unique_target("a.m4a") == library_dir / "a (3).m4a"
    assert library.unique_target("b.wav") == library_dir / "b.wav"


def test_unique_target_keeps_basename_only(library_dir) -> None:
    """带路径的名字只保留最后一段，挡目录穿越。"""
    assert library.unique_target("../../etc/passwd.wav") == library_dir / "passwd.wav"
    assert library.unique_target("C:\\Users\\someone\\rec.m4a") == library_dir / "rec.m4a"


# ---------------------------------------------------------------------- import_dir
def test_import_dir_imports_only_audio_recursively(temp_db, library_dir, tmp_path) -> None:
    root = tmp_path / "来源"
    (root / "sub").mkdir(parents=True)
    _audio(root / "a.m4a", b"a" * 10)
    _audio(root / "sub" / "b.MP3", b"b" * 20)  # 扩展名大小写不敏感
    _audio(root / "sub" / "deep" / "c.wav", b"c" * 30)
    (root / "note.txt").write_text("不是音频", encoding="utf-8")
    (root / "sub" / "cover.jpg").write_bytes(b"jpg")

    res = library.import_dir(root)

    assert res["total"] == 3
    assert res["added"] == 3
    assert res["skipped"] == 0
    assert res["failed"] == 0
    assert len(_files(temp_db)) == 3
    assert {p.name for p in library_dir.iterdir()} == {"a.m4a", "b.MP3", "c.wav"}
    # 进度 state 收尾：running=False，计数可查
    st = library.state()
    assert st["running"] is False
    assert st["total"] == 3
    assert st["done"] == 3
    assert st["error"] is None


def test_import_dir_second_run_skips_and_reimport_counts_as_added(
    temp_db, library_dir, tmp_path
) -> None:
    """重复导入全部 skipped；改了内容的文件走 replaced，在计数里并入 added。"""
    root = tmp_path / "来源"
    root.mkdir()
    _audio(root / "a.m4a", b"a" * 10)
    _audio(root / "b.wav", b"b" * 10)

    assert library.import_dir(root)["added"] == 2

    again = library.import_dir(root)
    assert again["added"] == 0
    assert again["skipped"] == 2
    assert again["failed"] == 0

    (root / "a.m4a").write_bytes(b"a" * 99)  # 内容变了 -> replaced
    third = library.import_dir(root)
    # import_dir 的返回值没有单独的 replaced 键：替换计入 added
    assert third["added"] == 1
    assert third["skipped"] == 1
    assert len(_files(temp_db)) == 2  # 覆盖，不是新增
    row = (
        temp_db.get_conn()
        .execute("SELECT rel_path FROM files WHERE source_path=?", (abs_key(root / "a.m4a"),))
        .fetchone()
    )
    assert library.file_path(row["rel_path"]).read_bytes() == b"a" * 99


def test_import_dir_single_failure_does_not_abort_batch(
    temp_db, library_dir, tmp_path, monkeypatch
) -> None:
    """单个坏文件只记 failed，不影响其它文件；0 字节音频按正常文件 added。"""
    root = tmp_path / "来源"
    root.mkdir()
    _audio(root / "good.m4a", b"good")
    _audio(root / "bad.m4a", b"bad")
    _audio(root / "empty.wav", b"")  # 0 字节：copy2 正常，进 added（size=0）

    real_import_one = library.import_one

    def flaky(src, mode="copy"):
        if Path(src).name == "bad.m4a":
            raise OSError("模拟坏文件")
        return real_import_one(src, mode)

    monkeypatch.setattr(library, "import_one", flaky)
    res = library.import_dir(root)

    assert res["total"] == 3
    assert res["failed"] == 1
    assert res["added"] == 2
    assert res["skipped"] == 0
    assert {p.name for p in library_dir.iterdir()} == {"good.m4a", "empty.wav"}
    empty = temp_db.get_conn().execute("SELECT size FROM files WHERE name='empty.wav'").fetchone()
    assert empty["size"] == 0
    assert library.state()["failed"] == 1
    assert library.state()["running"] is False


def test_import_dir_missing_root_and_unknown_mode(temp_db, library_dir, tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        library.import_dir(tmp_path / "不存在")

    root = tmp_path / "来源"
    root.mkdir()
    _audio(root / "a.m4a", b"a")
    res = library.import_dir(root, mode="weird")  # 未知 mode 回退 copy
    assert res["added"] == 1
    assert library.state()["mode"] == "copy"


def test_import_dir_honours_cancel(temp_db, library_dir, tmp_path) -> None:
    """取消在文件之间生效：已导入的保留，剩余不再处理。"""
    root = tmp_path / "来源"
    root.mkdir()
    for name in ("a.m4a", "b.m4a", "c.m4a"):
        _audio(root / name, name.encode())

    calls = {"n": 0}

    def should_cancel() -> bool:
        calls["n"] += 1
        return calls["n"] > 1  # 第一个文件处理完就取消

    res = library.import_dir(root, should_cancel=should_cancel)

    assert res["added"] == 1
    assert res["total"] == 3
    st = library.state()
    assert st["running"] is False
    assert st["error"] == "导入已取消"
    assert st["added"] == 1


def test_start_import_refuses_when_one_is_running(
    temp_db, library_dir, tmp_path, monkeypatch
) -> None:
    """只允许一个导入任务：已有任务时返回 started=False（API 层映射成 409）。"""
    monkeypatch.setitem(library._state, "running", True)
    res = library.start_import(str(tmp_path))
    assert res["started"] is False
    assert res["reason"] == "已有导入任务在跑"


# -------------------------------------------------------------------- file_path / stats
def test_file_path_handles_absolute_and_legacy_relative(library_dir) -> None:
    absolute = library_dir / "a.m4a"
    assert library.file_path(str(absolute)) == absolute
    # 老库里的相对路径兜底拼到资料库目录
    assert library.file_path("sub/b.m4a") == library_dir / "sub" / "b.m4a"


def test_is_in_library_only_accepts_library_paths(library_dir, tmp_path) -> None:
    inside = library_dir / "a.m4a"
    inside.write_bytes(b"x")
    outside = tmp_path / "other" / "b.m4a"
    _audio(outside, b"y")
    assert library.is_in_library(inside) is True
    assert library.is_in_library(outside) is False
    assert library.is_in_library(tmp_path / "不存在.m4a") is False


def test_stats_sums_only_existing_library_files(temp_db, library_dir, tmp_path) -> None:
    src = _audio(tmp_path / "src" / "a.m4a", b"12345")
    library.import_one(src)
    missing = library_dir / "手工删掉.wav"
    with temp_db.tx() as c:
        c.execute(
            "INSERT INTO files(rel_path, name, ext, size, mtime) VALUES(?,?,?,?,?)",
            (abs_key(missing), missing.name, ".wav", 100, 0.0),
        )

    st = library.stats()
    assert st["dir"] == str(library_dir)
    assert st["files"] == 2
    assert st["bytes"] == 5  # 不存在的文件不计入
