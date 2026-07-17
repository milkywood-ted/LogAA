"""routers/user_logs.py — src_path 허용 루트 경계 + DELETE 경로 조작 방어 (§9-8 검증 정식화)."""

import pytest
from fastapi import HTTPException

import routers.user_logs as ul
from config import config


@pytest.fixture
def env(tmp_path, monkeypatch):
    """임시 workspace + 허용 루트로 격리."""
    ws = tmp_path / "ws"
    root = tmp_path / "allowed"
    outside = tmp_path / "outside"
    for d in (ws, root, outside):
        d.mkdir()
    defect = ws / "D-1"
    defect.mkdir()
    (defect / "meta.json").write_text("{}")

    monkeypatch.setattr(config, "_workspace", ws)
    monkeypatch.setattr(config, "_user_log_roots", [root.resolve()])
    return {"ws": ws, "root": root, "outside": outside, "defect": defect}


def _add(src):
    return ul.add_user_log("D-1", ul.AddUserLogRequest(src_path=str(src)))


def test_allowed_file_copied(env):
    (env["root"] / "ok.log").write_text("kernel")
    assert _add(env["root"] / "ok.log") == {"copied": ["ok.log"], "skipped": []}


def test_outside_root_forbidden(env):
    (env["outside"] / "secret.txt").write_text("API_KEY=x")
    with pytest.raises(HTTPException) as e:
        _add(env["outside"] / "secret.txt")
    assert e.value.status_code == 403


def test_symlink_escape_forbidden(env):
    (env["outside"] / "secret.txt").write_text("x")
    link = env["root"] / "escape.log"
    link.symlink_to(env["outside"] / "secret.txt")
    with pytest.raises(HTTPException) as e:
        _add(link)
    assert e.value.status_code == 403


def test_folder_copy_skips_escaping_symlink(env):
    sub = env["root"] / "logs"
    sub.mkdir()
    (sub / "a.log").write_text("a")
    (env["outside"] / "secret.txt").write_text("x")
    (sub / "esc.log").symlink_to(env["outside"] / "secret.txt")
    result = _add(sub)
    assert result["copied"] == ["a.log"]
    assert result["skipped"] == ["esc.log"]


def test_boundary_checked_before_existence(env):
    # 밖의 존재하지 않는 경로도 존재 확인(400) 전에 경계(403)로 막힌다
    with pytest.raises(HTTPException) as e:
        _add("/etc/nonexistent-xyz")
    assert e.value.status_code == 403


def test_unconfigured_roots_block_all(env, monkeypatch):
    monkeypatch.setattr(config, "_user_log_roots", [])
    (env["root"] / "ok.log").write_text("x")
    with pytest.raises(HTTPException) as e:
        _add(env["root"] / "ok.log")
    assert e.value.status_code == 403 and "user_log_roots" in e.value.detail


def test_delete_path_traversal_blocked(env):
    with pytest.raises(HTTPException) as e:
        ul.delete_user_log("D-1", "../meta.json")
    assert e.value.status_code == 400
    assert (env["defect"] / "meta.json").exists()   # 원본 무사


def test_delete_normal(env):
    dest = env["defect"] / "user_added_log"
    dest.mkdir()
    (dest / "x.log").write_text("x")
    assert ul.delete_user_log("D-1", "x.log") == {"deleted": "x.log"}
    assert not (dest / "x.log").exists()
