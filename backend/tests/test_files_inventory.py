"""routers/files.py — 파일 인벤토리: 재귀 walk + zip 제외 + archive 주석 (문제1 검증)."""

import json

import pytest

import routers.files as files
from config import config


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_workspace", tmp_path)
    d = tmp_path / "D-1"
    d.mkdir()
    return d


def _meta(defect_dir, archive_origins=None):
    (defect_dir / "meta.json").write_text(
        json.dumps({"id": "D-1", "archive_origins": archive_origins or {}}),
        encoding="utf-8",
    )


def test_extracted_files_in_subfolder_are_listed(ws):
    # zip 은 서브폴더로 풀렸고 원본 zip 도 남아 있는 상태
    (ws / "system_logs.zip").write_bytes(b"PKfake")
    sub = ws / "logs"
    sub.mkdir()
    (sub / "dmesg.log").write_text("A")
    (sub / "kernel.log").write_text("B")
    _meta(ws, {"logs/dmesg.log": "system_logs.zip", "logs/kernel.log": "system_logs.zip"})

    out = files.list_defect_files("D-1")
    names = {f["relative_path"] for f in out["default"]}
    # 압축해제된 서브폴더 파일이 보이고, zip 자신은 목록에서 빠진다
    assert names == {"logs/dmesg.log", "logs/kernel.log"}
    assert not any(f["relative_path"].endswith(".zip") for f in out["default"])


def test_archive_annotation_attached(ws):
    sub = ws / "logs"
    sub.mkdir()
    (sub / "dmesg.log").write_text("A")
    (ws / "raw.log").write_text("R")   # zip 출신 아님
    _meta(ws, {"logs/dmesg.log": "system_logs.zip"})

    by_rel = {f["relative_path"]: f for f in files.list_defect_files("D-1")["default"]}
    assert by_rel["logs/dmesg.log"]["archive"] == "system_logs.zip"
    assert by_rel["raw.log"]["archive"] is None   # 직접 다운로드 → 주석 없음


def test_subtrees_excluded_from_default(ws):
    (ws / "top.log").write_text("T")
    for sub, fname in (("CommentAttachment", "c.log"), ("user_added_log", "u.log")):
        d = ws / sub
        d.mkdir()
        (d / fname).write_text("x")
    _meta(ws)

    out = files.list_defect_files("D-1")
    default_names = {f["relative_path"] for f in out["default"]}
    assert default_names == {"top.log"}   # 서브트리는 default 에서 제외
    assert any(f["filename"] == "c.log" for f in out["comment_attachment"])
    assert any(f["filename"] == "u.log" for f in out["user_added"])


def test_meta_json_excluded(ws):
    (ws / "a.log").write_text("A")
    _meta(ws)
    out = files.list_defect_files("D-1")
    assert all(f["filename"] != "meta.json" for f in out["default"])
