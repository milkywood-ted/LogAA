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
    "verdict": "유사문제", "score": 0.67, "matched_case": "케이스A",
    "matched_patterns": [{"name": "P1", "type": "PRESENCE", "weight": 1.0}],
    "problem_text": "문제 설명", "report_md": "# 리포트",
    "full": {"verdict": "유사문제", "match_result": {"unmatched": [{"name": "P2"}]},
             "minority_reports": [{"matched_case": {"name": "케이스B"}}],
             "warnings": ["경고1"]},
}


def test_list_returns_slim_summary_only(hist_db):
    _insert(hist_db, "D-1", _PAYLOAD)
    rows = hist.list_history(limit=20, defect_id=None)
    assert len(rows) == 1
    r = rows[0]
    assert r["verdict"] == "유사문제" and r["score"] == 0.67
    assert r["matched_case"] == "케이스A"
    assert r["matched_patterns"] == _PAYLOAD["matched_patterns"]
    # 목록은 full·report_md 를 싣지 않는다 (요약만) — report_md 유무는 파생 불리언으로만
    assert "full" not in r and "report_md" not in r
    assert r["has_report_md"] is True


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


# ── 사용자 분석 입력 (분석 리포트 개선 PR 5 — report_md 직접 갱신) ────────────
#
# "불확실"/"유사문제 없음"은 파이프라인이 Stage 5를 건너뛰므로 report_md가
# 처음부터 비어있다(§2-1) — 별도 컬럼 없이 이 필드 유무만으로 이력 페이지의
# "사용자 분석 입력" 버튼 노출을 판단한다.

_PENDING_PAYLOAD = {
    "verdict": "불확실", "score": 0.4, "matched_case": None,
    "matched_patterns": [], "problem_text": "문제 설명", "report_md": "",
    "full": {"verdict": "불확실", "report_md": ""},
}


def test_list_and_get_expose_missing_report_md_when_pending(hist_db):
    hid = _insert(hist_db, "D-1", _PENDING_PAYLOAD)
    rows = hist.list_history(limit=20, defect_id=None)
    assert rows[0]["has_report_md"] is False
    out = hist.get_history(hid)
    assert out["result"]["report_md"] == ""


def test_update_history_report_saves_slim_key_only(hist_db):
    hid = _insert(hist_db, "D-1", _PENDING_PAYLOAD)

    resp = hist.update_history_report(
        hid, hist.HistoryReportRequest(report_md="직접 분석한 결과입니다."),
    )
    assert resp["report_md"] == "직접 분석한 결과입니다."

    out = hist.get_history(hid)
    assert out["result"]["report_md"] == "직접 분석한 결과입니다."
    # full.report_md 는 아카이브 사본이라 그대로 둔다(§6 근거) — 원본 LLM
    # 초안이 있었다면 여기서 보존된다.
    assert out["result"]["full"]["report_md"] == ""

    rows = hist.list_history(limit=20, defect_id=None)
    assert rows[0]["has_report_md"] is True


def test_update_history_report_missing_404(hist_db):
    with pytest.raises(HTTPException) as e:
        hist.update_history_report(99999, hist.HistoryReportRequest(report_md="x"))
    assert e.value.status_code == 404
