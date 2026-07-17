"""routers/puller.py _extract_archives_recursive — 재귀 압축해제 + provenance
+ zip slip/bomb 방어 (문제1·2 수정 검증)."""

import io
import zipfile

import pytest

import routers.puller as puller


def _write_zip(path, entries):
    """entries: [(name, bytes_or_str)]. 중첩용으로 bytes 도 허용."""
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries:
            zf.writestr(name, data)


def _zip_bytes(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return buf.getvalue()


def test_extracts_into_subfolder_and_records_origin(tmp_path):
    _write_zip(tmp_path / "system_logs.zip", [("logs/dmesg.log", "A"), ("logs/kernel.log", "B")])
    origins = puller._extract_archives_recursive(tmp_path)
    assert (tmp_path / "logs" / "dmesg.log").read_text() == "A"
    assert origins["logs/dmesg.log"] == "system_logs.zip"
    assert origins["logs/kernel.log"] == "system_logs.zip"


def test_nested_zip_extracted_with_top_level_origin(tmp_path):
    # outer.zip → inner.zip → deep.log : deep.log 의 출처는 최상위 outer.zip
    inner = _zip_bytes([("deep.log", "DEEP")])
    _write_zip(tmp_path / "outer.zip", [("inner.zip", inner), ("top.log", "TOP")])
    origins = puller._extract_archives_recursive(tmp_path)
    assert (tmp_path / "deep.log").read_text() == "DEEP"
    assert origins["deep.log"] == "outer.zip"      # inner.zip 아님
    assert origins["top.log"] == "outer.zip"


def test_directly_downloaded_file_has_no_origin(tmp_path):
    (tmp_path / "raw_console.log").write_text("raw")
    _write_zip(tmp_path / "logs.zip", [("a.log", "A")])
    origins = puller._extract_archives_recursive(tmp_path)
    assert "raw_console.log" not in origins       # zip 출신 아님 → 주석 없음
    assert origins["a.log"] == "logs.zip"


def test_zip_slip_relative_escape_skipped(tmp_path):
    dest = tmp_path / "ws"
    dest.mkdir()
    _write_zip(dest / "evil.zip", [("../escape.txt", "X"), ("ok.log", "O")])
    puller._extract_archives_recursive(dest)
    assert not (tmp_path / "escape.txt").exists()   # 루트 밖 탈출 안 함
    assert (dest / "ok.log").read_text() == "O"


def test_file_count_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(puller, "MAX_EXTRACT_FILES", 3)
    _write_zip(tmp_path / "many.zip", [(f"f{i}.log", "x") for i in range(10)])
    puller._extract_archives_recursive(tmp_path)
    assert len(list((tmp_path).rglob("f*.log"))) == 3


def test_total_bytes_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(puller, "MAX_EXTRACT_BYTES", 100)
    _write_zip(tmp_path / "big.zip",
               [("big1.log", "a" * 80), ("big2.log", "b" * 80), ("big3.log", "c")])
    puller._extract_archives_recursive(tmp_path)
    assert (tmp_path / "big1.log").exists()
    assert not (tmp_path / "big2.log").exists()


def test_no_zip_returns_empty(tmp_path):
    (tmp_path / "plain.log").write_text("x")
    assert puller._extract_archives_recursive(tmp_path) == {}


def test_corrupt_zip_skipped(tmp_path):
    (tmp_path / "broken.zip").write_bytes(b"PK\x03\x04 not really a zip")
    # 손상 zip 은 skip, 크래시하지 않는다
    assert puller._extract_archives_recursive(tmp_path) == {}
