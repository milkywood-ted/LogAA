"""core/kb_search.py — MatchedCase 케이스 리포트 스키마 v2 필드 배선 (R2).

Document/케이스 판정 연계 재도입/PR36 요구사항 및 결함 리포트.md PR 1:
load_case_by_name / _vector_search / _rerank 세 지점이 cases.verdict 등을
MatchedCase 까지 빠짐없이 실어 나르는지 검증한다. 이 시점에는 아무 downstream
소비자도 없으므로(§1.2-A 판정 로직은 PR 2) 여기서는 순수 데이터 배선만 확인한다.
"""

import json

import core.db as db
import core.kb_search as kb_search_module
from core.kb_search import KBSearch


def _make_kb(tmp_path) -> tuple[KBSearch, str]:
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    kb = KBSearch(db_path=dbp, chroma_path=tmp_path / "chroma")
    kb._llm_profile          = {"provider": "openai", "model": "qwen", "base_url": "http://ollama/v1"}
    kb._llm_fallback_profile = None
    return kb, dbp


def _insert_full_verdict_case(dbp, case_id: int, name: str) -> None:
    with db.get_conn(dbp) as conn:
        conn.execute(
            "INSERT INTO cases ("
            "id, name, description, keywords, chip_tags, "
            "verdict, verdict_rationale, undetermined_reason, undetermined_reason_note, "
            "actions, symptom_module, defect_area_type, defect_area_module, "
            "defect_area_items, notes, analyst, owner_module, analysis_date, log_source"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                case_id, name, f"{name} 설명", json.dumps([]), json.dumps([]),
                "defect", "커널 패닉 재현 확인됨", None, "",
                json.dumps({"fix": {"done": True}}), "전원 관리", "module", "PMIC",
                json.dumps(["항목1"]), "비고", "홍길동", "전원팀", "2026-07-20", "고객 현장 로그",
            ),
        )


def _insert_legacy_case(dbp, case_id: int, name: str) -> None:
    """verdict 등 스키마 v2 컬럼을 전혀 지정하지 않은 케이스 — DB 기본값 확인용."""
    with db.get_conn(dbp) as conn:
        conn.execute(
            "INSERT INTO cases (id, name, description, keywords, chip_tags) VALUES (?, ?, ?, ?, ?)",
            (case_id, name, f"{name} 설명", json.dumps([]), json.dumps([])),
        )


# ── load_case_by_name ───────────────────────────────────────────────────────

def test_load_case_by_name_includes_verdict_fields(tmp_path):
    kb, dbp = _make_kb(tmp_path)
    _insert_full_verdict_case(dbp, 1, "케이스A")

    mc = kb.load_case_by_name("케이스A")

    assert mc is not None
    assert mc.case_verdict == "defect"
    assert mc.verdict_rationale == "커널 패닉 재현 확인됨"
    assert mc.undetermined_reason is None
    assert mc.actions == {"fix": {"done": True}}
    assert mc.symptom_module == "전원 관리"
    assert mc.defect_area_type == "module"
    assert mc.defect_area_module == "PMIC"
    assert mc.defect_area_items == ["항목1"]
    assert mc.notes == "비고"
    assert mc.analyst == "홍길동"
    assert mc.owner_module == "전원팀"
    assert mc.analysis_date == "2026-07-20"
    assert mc.log_source == "고객 현장 로그"


def test_load_case_by_name_legacy_case_defaults(tmp_path):
    kb, dbp = _make_kb(tmp_path)
    _insert_legacy_case(dbp, 1, "레거시케이스")

    mc = kb.load_case_by_name("레거시케이스")

    assert mc is not None
    assert mc.case_verdict is None       # NULL = 레거시(C2)
    assert mc.verdict_rationale == ""
    assert mc.actions == {}
    assert mc.defect_area_items == []
    assert mc.analyst == ""
    assert mc.analysis_date is None


# ── _rerank (LLM 경로) — 후보 dict → MatchedCase 필드 통과 ────────────────────

def test_rerank_passes_through_verdict_fields(tmp_path, monkeypatch):
    kb, dbp = _make_kb(tmp_path)
    _insert_full_verdict_case(dbp, 1, "케이스A")

    candidate = {
        "case_id": 1, "name": "케이스A", "description": "케이스A 설명",
        "analysis": "", "keywords": [], "chip_tags": [],
        "distance": 0.1, "distance_desc": 0.1, "distance_analysis": None,
        "case_verdict": "defect", "verdict_rationale": "커널 패닉 재현 확인됨",
        "undetermined_reason": None, "undetermined_reason_note": "",
        "actions": {"fix": {"done": True}}, "symptom_module": "전원 관리",
        "defect_area_type": "module", "defect_area_module": "PMIC",
        "defect_area_items": ["항목1"], "notes": "비고",
        "analyst": "홍길동", "owner_module": "전원팀",
        "analysis_date": "2026-07-20", "log_source": "고객 현장 로그",
    }

    monkeypatch.setattr(
        kb_search_module, "chat_with_profile",
        lambda **kwargs: json.dumps({"scores": [{"index": 1, "relevance_score": 0.9}]}),
    )

    results = kb._rerank("문제 설명", [candidate])

    assert len(results) == 1
    mc = results[0]
    assert mc.case_verdict == "defect"
    assert mc.verdict_rationale == "커널 패닉 재현 확인됨"
    assert mc.actions == {"fix": {"done": True}}
    assert mc.defect_area_items == ["항목1"]
    assert mc.analyst == "홍길동"


# ── _vector_search — SELECT 배선 ─────────────────────────────────────────────

def test_vector_search_includes_verdict_columns(tmp_path, monkeypatch):
    kb, dbp = _make_kb(tmp_path)
    _insert_full_verdict_case(dbp, 1, "케이스A")

    class _FakeHitCollection:
        def count(self):
            return 1

        def query(self, **kwargs):
            return {
                "ids":       [["1"]],
                "documents": [["케이스A 설명"]],
                "metadatas": [[{"case_id": 1}]],
                "distances": [[0.05]],
            }

    monkeypatch.setattr(kb, "_col", _FakeHitCollection())
    monkeypatch.setattr(kb, "_col_analysis", _FakeHitCollection())
    monkeypatch.setattr(kb_search_module, "embed", lambda texts: [[0.0]])

    candidates = kb._vector_search("문제 설명")

    assert len(candidates) == 1
    c = candidates[0]
    assert c["case_verdict"] == "defect"
    assert c["actions"] == {"fix": {"done": True}}
    assert c["defect_area_items"] == ["항목1"]
    assert c["log_source"] == "고객 현장 로그"
