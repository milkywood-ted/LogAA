"""routers/puller.py _safe_extract — zip slip / zip bomb 방어 (§9-7 검증 정식화)."""

import io
import zipfile

import pytest

import routers.puller as puller


def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries:
            zf.writestr(name, data)
    buf.seek(0)
    return zipfile.ZipFile(buf)


def test_normal_extract_with_nested_dir(tmp_path):
    puller._safe_extract(_zip([("a.log", "A"), ("sub/b.log", "B")]), tmp_path)
    assert (tmp_path / "a.log").read_text() == "A"
    assert (tmp_path / "sub" / "b.log").read_text() == "B"


def test_zip_slip_relative_escape_skipped(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    puller._safe_extract(_zip([("../escape.txt", "X"), ("ok.log", "O")]), dest)
    assert not (tmp_path / "escape.txt").exists()   # 밖으로 탈출 안 함
    assert (dest / "ok.log").read_text() == "O"


def test_zip_slip_absolute_path_skipped(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    puller._safe_extract(_zip([("/tmp/evil_xyz.txt", "X"), ("ok.log", "O")]), dest)
    assert not (dest / "tmp" / "evil_xyz.txt").exists()
    assert (dest / "ok.log").exists()


def test_file_count_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(puller, "MAX_EXTRACT_FILES", 3)
    puller._safe_extract(_zip([(f"f{i}.log", "x") for i in range(10)]), tmp_path)
    assert len(list(tmp_path.glob("*.log"))) == 3   # 상한까지만


def test_total_bytes_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(puller, "MAX_EXTRACT_BYTES", 100)
    puller._safe_extract(
        _zip([("big1.log", "a" * 80), ("big2.log", "b" * 80), ("big3.log", "c")]),
        tmp_path,
    )
    assert (tmp_path / "big1.log").exists()
    assert not (tmp_path / "big2.log").exists()   # 총량 초과 시점에 중단
