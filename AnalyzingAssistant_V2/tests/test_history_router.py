"""api/router/history.py — 이력 목록/단건 조회 (§9-8 겸용 payload 소비 검증).

라우터 함수를 임시 DB로 직접 호출한다 (전체 앱 lifespan 미기동 — 실제 DB 미접촉).
list_history 가 슬림 요약만 추출하고, get_history 가 full 을 포함해 반환하는지 확인.
"""

import json

import pytest
from fastapi import HTTPException

import core.db as db
import api.router.history as hist


@pytest.fixture
def hist_db(tmp_path, monkeypatch):
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    monkeypatch.setattr(hist, "DB_PATH", dbp)
    return dbp


def _insert(dbp, defect_id, payload):
    with db.get_conn(dbp) as conn:
        cur = conn.execute(
            "INSERT INTO history (input_hash, result, defect_id) VALUES (?, ?, ?)",
            ("h" + defect_id, json.dumps(payload, ensure_ascii=False), defect_id),
        )
        return cur.lastrowid


# §9-8 겸용 payload: 슬림 요약 + full
_PAYLOAD = {
    "verdict": "문제", "score": 0.67, "matched_case": "케이스A",
    "matched_patterns": [{"name": "P1", "type": "PRESENCE", "weight": 1.0}],
    "problem_text": "문제 설명", "report_md": "# 리포트",
    "full": {"verdict": "문제", "match_result": {"unmatched": [{"name": "P2"}]},
             "minority_reports": [{"matched_case": {"name": "케이스B"}}],
             "warnings": ["경고1"]},
}


def test_list_returns_slim_summary_only(hist_db):
    _insert(hist_db, "D-1", _PAYLOAD)
    rows = hist.list_history(limit=20, defect_id=None)
    assert len(rows) == 1
    r = rows[0]
    assert r["verdict"] == "문제" and r["score"] == 0.67
    assert r["matched_case"] == "케이스A"
    assert r["matched_patterns"] == _PAYLOAD["matched_patterns"]
    # 목록은 full·report_md 를 싣지 않는다 (요약만)
    assert "full" not in r and "report_md" not in r


def test_list_defect_filter_and_order(hist_db):
    _insert(hist_db, "D-1", {**_PAYLOAD, "verdict": "불확실"})
    _insert(hist_db, "D-2", _PAYLOAD)
    _insert(hist_db, "D-1", _PAYLOAD)
    only_d1 = hist.list_history(limit=20, defect_id="D-1")
    assert len(only_d1) == 2 and all(True for _ in only_d1)
    # 최신순(id DESC)
    all_rows = hist.list_history(limit=20, defect_id=None)
    assert [r["id"] for r in all_rows] == sorted((r["id"] for r in all_rows), reverse=True)


def test_get_single_includes_full_and_logs(hist_db):
    hid = _insert(hist_db, "D-1", _PAYLOAD)
    with db.get_conn(hist_db) as conn:
        conn.execute(
            "INSERT INTO analysis_logs (history_id, stage, payload) VALUES (?, 'stage2', ?)",
            (hid, json.dumps({"hit": True})),
        )
    out = hist.get_history(hid)
    assert out["defect_id"] == "D-1"
    # full 이 result 안에 보존됨 (§9-8)
    assert out["result"]["full"]["warnings"] == ["경고1"]
    assert out["result"]["full"]["minority_reports"][0]["matched_case"]["name"] == "케이스B"
    # analysis_logs 파싱 동봉
    assert out["analysis_logs"][0]["stage"] == "stage2"
    assert out["analysis_logs"][0]["payload"] == {"hit": True}


def test_get_missing_404(hist_db):
    with pytest.raises(HTTPException) as e:
        hist.get_history(99999)
    assert e.value.status_code == 404
