"""core/pipeline.serialize_result + _save_history 겸용 payload (§9-8 검증 정식화)."""

import json
from types import SimpleNamespace as NS

import pytest

import core.db as db
from core.pipeline import Pipeline, serialize_result


def _fake_result():
    pat_m = NS(name="P1", type="PRESENCE", weight=1.0, evidence=["l1", "l2"])
    pat_u = NS(name="P2", type="ABSENCE", weight=0.5)
    mc = NS(case_id=7, name="케이스A", relevance_score=0.91, keywords=["ata"],
            chip_tags=["RheaM"], references=[{"system": "Kona", "reference_id": "D-1"}])
    minor = NS(matched_case=NS(case_id=8, name="케이스B", relevance_score=0.72, chip_tags=[]),
               match_result=NS(score=0.4, matched=[pat_m], unmatched=[]),
               source_profile_names=["prof2"], knowledge_similarity=0.5)
    return NS(verdict="유사문제", report_md="# 리포트", matched_case=mc,
              match_result=NS(score=0.67, matched=[pat_m], unmatched=[pat_u]),
              reference_cases=[], reflection_notes=None, history_id=None,
              selected_logs={"D-1/dmesg.log": "..."}, warnings=["경고1"],
              minority_reports=[minor], winner_profile_names=["prof1"])


def test_serialize_result_keys_and_structure():
    s = serialize_result(_fake_result())
    assert set(s) == {
        "verdict", "report_md", "matched_case", "match_result", "reference_cases",
        "reflection_notes", "history_id", "selected_logs", "warnings", "minority_reports",
        "winner_profile_names", "traversal_mode",
    }
    assert s["matched_case"]["chip_tags"] == ["RheaM"]
    assert s["match_result"]["unmatched"] == [{"name": "P2", "type": "ABSENCE", "weight": 0.5}]
    assert s["match_result"]["matched"][0]["evidence_count"] == 2
    assert s["minority_reports"][0]["matched_case"]["name"] == "케이스B"
    assert s["selected_logs"] == ["D-1/dmesg.log"]   # 경로 키만 (내용 아님)
    json.dumps(s)   # JSON 직렬화 가능


def test_save_history_slim_keys_and_full(tmp_path):
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    result = _fake_result()
    hid = Pipeline._save_history(NS(db_path=dbp), "문제 설명", {"f.log": "raw"},
                                 result, defect_id="D-1")
    assert hid is not None
    with db.get_conn(dbp) as conn:
        payload = json.loads(conn.execute(
            "SELECT result FROM history WHERE id=?", (hid,)).fetchone()["result"])

    # 슬림 키 — 기존 HistoryPage·라우터 호환
    assert payload["verdict"] == "유사문제" and payload["score"] == 0.67
    assert payload["matched_case"] == "케이스A"           # 문자열
    assert payload["matched_patterns"] == [{"name": "P1", "type": "PRESENCE", "weight": 1.0}]
    assert payload["problem_text"] == "문제 설명"

    # full — 이전에 소실되던 정보 보존
    full = payload["full"]
    assert full["match_result"]["unmatched"]
    assert full["minority_reports"][0]["matched_case"]["name"] == "케이스B"
    assert full["warnings"] == ["경고1"] and full["winner_profile_names"] == ["prof1"]
    assert "history_id" not in full   # 자기 행 id 혼동 방지
