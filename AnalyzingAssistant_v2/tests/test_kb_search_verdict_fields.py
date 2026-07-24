"""core/kb_search.py — MatchedCase 에 케이스 판정 관련 필드(verdict/verdict_rationale/
actions/notes)가 채워지는지 검증한다 (분석 리포트 개선 PR 1).

DB 컬럼은 케이스 스키마 v2 마이그레이션(core/db.py _migrate)에 이미 있고, 이 PR은
그 컬럼을 MatchedCase 까지 읽어오는 조회 경로 3곳(load_case_by_name / _vector_search
→ _rerank)을 연결한다 — 순수 데이터 연결이며 소비자는 아직 없다.
"""

import json

import core.db as db
from core.kb_search import KBSearch


def _insert_case_with_verdict(dbp, case_id: int, name: str) -> None:
    with db.get_conn(dbp) as conn:
        conn.execute(
            "INSERT INTO cases (id, name, description, keywords, verdict, "
            "verdict_rationale, actions, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                case_id, name, f"{name} 설명", json.dumps([]),
                "defect", "로그 패턴이 알려진 결함 시그니처와 일치",
                json.dumps({"fix": ["패치 적용"]}), "특이사항 없음",
            ),
        )


def _make_kb(tmp_path) -> tuple[KBSearch, "Path"]:
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    kb = KBSearch(db_path=dbp, chroma_path=tmp_path / "chroma")
    return kb, dbp


# ── load_case_by_name (pinned 경로) ──────────────────────────────────────────

def test_load_case_by_name_fills_verdict_fields(tmp_path):
    kb, dbp = _make_kb(tmp_path)
    _insert_case_with_verdict(dbp, 1, "케이스A")

    case = kb.load_case_by_name("케이스A")

    assert case is not None
    assert case.verdict == "defect"
    assert case.verdict_rationale == "로그 패턴이 알려진 결함 시그니처와 일치"
    assert case.actions == {"fix": ["패치 적용"]}
    assert case.notes == "특이사항 없음"


def test_load_case_by_name_legacy_case_has_none_verdict(tmp_path):
    """verdict 미기재(레거시) 케이스는 None — 기본값 강제 대입 금지."""
    kb, dbp = _make_kb(tmp_path)
    with db.get_conn(dbp) as conn:
        conn.execute(
            "INSERT INTO cases (id, name, description, keywords) VALUES (?, ?, ?, ?)",
            (2, "케이스B", "케이스B 설명", json.dumps([])),
        )

    case = kb.load_case_by_name("케이스B")

    assert case is not None
    assert case.verdict is None
    assert case.verdict_rationale == ""
    assert case.actions == {}
    assert case.notes == ""


# ── _vector_search → _rerank (자동 검색 경로) ────────────────────────────────

class _FakeQueryCollection:
    """count()>0, query() 가 case_id=1 하나를 반환하는 페이크 컬렉션."""

    def __init__(self, case_id: int, document: str):
        self._case_id = case_id
        self._document = document

    def count(self):
        return 1

    def query(self, **kwargs):
        return {
            "ids":       [[str(self._case_id)]],
            "documents": [[self._document]],
            "metadatas": [[{"case_id": self._case_id, "name": "케이스A"}]],
            "distances": [[0.1]],
        }


class _EmptyCollection:
    def count(self):
        return 0

    def query(self, **kwargs):
        return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}


def test_vector_search_candidate_carries_verdict_fields(tmp_path, monkeypatch):
    kb, dbp = _make_kb(tmp_path)
    _insert_case_with_verdict(dbp, 1, "케이스A")

    kb._col          = _FakeQueryCollection(1, "케이스A 설명")
    kb._col_analysis = _EmptyCollection()
    monkeypatch.setattr("core.kb_search.embed", lambda texts: [[0.0]])

    candidates = kb._vector_search("커널 패닉")

    assert len(candidates) == 1
    assert candidates[0]["verdict"] == "defect"
    assert candidates[0]["verdict_rationale"] == "로그 패턴이 알려진 결함 시그니처와 일치"
    assert candidates[0]["actions"] == {"fix": ["패치 적용"]}
    assert candidates[0]["notes"] == "특이사항 없음"


def test_rerank_result_carries_verdict_fields(tmp_path, monkeypatch):
    """_rerank 가 candidate dict 의 verdict 류를 MatchedCase 로 그대로 옮기는지."""
    kb, dbp = _make_kb(tmp_path)
    _insert_case_with_verdict(dbp, 1, "케이스A")
    kb._llm_profile          = {"provider": "vllm-rerank", "model": "bge-reranker",
                                 "base_url": "http://vllm:8000/v1"}
    kb._llm_fallback_profile = None

    import core.kb_search as kb_search_module
    monkeypatch.setattr(
        kb_search_module, "llm_rerank",
        lambda profile, query, docs: [(0, 0.9)],
    )

    candidate = {
        "case_id": 1, "name": "케이스A", "description": "케이스A 설명",
        "analysis": "", "keywords": [], "chip_tags": [],
        "verdict": "no_defect", "verdict_rationale": "정상 동작 확인됨",
        "actions": {"keep": {"detail": "regression_only"}}, "notes": "",
        "distance": 0.1, "distance_desc": 0.1, "distance_analysis": None,
    }
    results = kb._rerank("문제 설명", [candidate])

    assert len(results) == 1
    assert results[0].verdict == "no_defect"
    assert results[0].verdict_rationale == "정상 동작 확인됨"
    assert results[0].actions == {"keep": {"detail": "regression_only"}}
