"""core/kb_search.py KBSearch._rerank — rerank 엔드포인트 분기 (선구현 — llm.rerank 목킹).

reranker 엔드포인트 구현 설계 문서 §3.2 "_rerank 채점부만 provider로 분기,
조립부(칩 가중치·max_candidates·MatchedCase 조립)는 무변경"을 검증한다.
실제 vLLM 호출은 test_llm_rerank.py 가 별도로 검증하므로, 여기서는
core.kb_search.llm_rerank 를 스텁으로 교체해 kb_search 쪽 분기·threshold·
downstream 조립 로직만 격리 검증한다.
"""

import json

import pytest

import core.db as db
import core.kb_search as kb_search_module
from core.kb_search import KBSearch
from core.llm import RerankError


def _insert_case(dbp, case_id: int, name: str, chip_tags=None) -> None:
    with db.get_conn(dbp) as conn:
        conn.execute(
            "INSERT INTO cases (id, name, description, keywords, chip_tags) "
            "VALUES (?, ?, ?, ?, ?)",
            (case_id, name, f"{name} 설명", json.dumps([]),
             json.dumps(chip_tags or [])),
        )


def _make_kb(tmp_path, threshold=0.7, rerank_threshold=0.5) -> KBSearch:
    """KBSearch 를 만들고 LLM 프로필 해석을 실제 config.yaml 에서 분리한다.

    실제 config/LLM/config.yaml 내용에 테스트가 의존하지 않도록, 생성 후
    프로필·threshold 를 인스턴스 속성으로 직접 덮어쓴다.
    """
    dbp = tmp_path / "t.db"
    db.init_db(dbp)
    kb = KBSearch(db_path=dbp, chroma_path=tmp_path / "chroma")
    kb.threshold         = threshold
    kb.rerank_threshold  = rerank_threshold
    kb._llm_profile          = {"provider": "vllm-rerank", "model": "bge-reranker",
                                 "base_url": "http://vllm:8000/v1"}
    kb._llm_fallback_profile = None
    return kb, dbp


def _candidate(case_id: int, name: str) -> dict:
    return {
        "case_id": case_id, "name": name, "description": f"{name} 설명",
        "analysis": "", "keywords": [], "chip_tags": [],
        "distance": 0.1, "distance_desc": 0.1, "distance_analysis": None,
    }


# ── 엔드포인트 경로 채점 ──────────────────────────────────────────────────────

def test_rerank_endpoint_path_returns_matched_cases(tmp_path, monkeypatch):
    kb, dbp = _make_kb(tmp_path, rerank_threshold=0.5)
    _insert_case(dbp, 1, "케이스A")
    _insert_case(dbp, 2, "케이스B")

    monkeypatch.setattr(
        kb_search_module, "llm_rerank",
        lambda profile, query, docs: [(0, 0.9), (1, 0.3)],
    )

    results = kb._rerank("문제 설명", [_candidate(1, "케이스A"), _candidate(2, "케이스B")])

    # rerank_threshold(0.5) 미만인 케이스B(0.3)는 컷 — 조립부(threshold 필터)가
    # 그대로 동작함을 확인
    assert [r.name for r in results] == ["케이스A"]
    assert results[0].relevance_score == 0.9


def test_rerank_endpoint_uses_rerank_threshold_not_kb_threshold(tmp_path, monkeypatch):
    """kb_threshold(0.7) 보다 낮은 점수라도 rerank_threshold(0.2) 를 넘으면 통과해야 한다."""
    kb, dbp = _make_kb(tmp_path, threshold=0.7, rerank_threshold=0.2)
    _insert_case(dbp, 1, "케이스A")

    monkeypatch.setattr(
        kb_search_module, "llm_rerank",
        lambda profile, query, docs: [(0, 0.4)],   # kb_threshold 미만, rerank_threshold 이상
    )

    results = kb._rerank("문제 설명", [_candidate(1, "케이스A")])
    assert [r.name for r in results] == ["케이스A"]


def test_rerank_endpoint_documents_built_from_candidates(tmp_path, monkeypatch):
    kb, dbp = _make_kb(tmp_path)
    _insert_case(dbp, 1, "케이스A")
    captured = {}

    def fake_rerank(profile, query, docs):
        captured["query"] = query
        captured["docs"] = docs
        return [(0, 0.9)]

    monkeypatch.setattr(kb_search_module, "llm_rerank", fake_rerank)
    kb._rerank("부팅 중 커널 패닉", [_candidate(1, "케이스A")])

    assert captured["query"] == "부팅 중 커널 패닉"
    assert captured["docs"] == ["케이스A\n\n케이스A 설명"]


# ── fallback 재시도 (primary/fallback 혼합) ──────────────────────────────────

def test_endpoint_primary_fails_falls_back_to_llm_profile(tmp_path, monkeypatch):
    """primary(엔드포인트) 실패 시 fallback(LLM) 프로필로 재시도한다."""
    kb, dbp = _make_kb(tmp_path)
    _insert_case(dbp, 1, "케이스A")
    kb._llm_fallback_profile = {"provider": "openai", "model": "qwen",
                                 "base_url": "http://ollama/v1"}

    def failing_rerank(profile, query, docs):
        raise RerankError("연결 실패")

    monkeypatch.setattr(kb_search_module, "llm_rerank", failing_rerank)
    monkeypatch.setattr(
        kb_search_module, "chat_with_profile",
        lambda **kwargs: json.dumps({"scores": [{"index": 1, "relevance_score": 0.8}]}),
    )

    results = kb._rerank("문제 설명", [_candidate(1, "케이스A")])
    assert [r.name for r in results] == ["케이스A"]


def test_llm_primary_fails_falls_back_to_endpoint_profile(tmp_path, monkeypatch):
    """primary(LLM) 실패 시 fallback(엔드포인트) 프로필로 재시도한다."""
    kb, dbp = _make_kb(tmp_path)
    _insert_case(dbp, 1, "케이스A")
    kb._llm_profile = {"provider": "openai", "model": "qwen", "base_url": "http://ollama/v1"}
    kb._llm_fallback_profile = {"provider": "vllm-rerank", "model": "bge-reranker",
                                 "base_url": "http://vllm:8000/v1"}

    def failing_chat(**kwargs):
        raise RuntimeError("chat 호출 실패")

    monkeypatch.setattr(kb_search_module, "chat_with_profile", failing_chat)
    monkeypatch.setattr(
        kb_search_module, "llm_rerank",
        lambda profile, query, docs: [(0, 0.85)],
    )

    results = kb._rerank("문제 설명", [_candidate(1, "케이스A")])
    assert [r.name for r in results] == ["케이스A"]


def test_all_profiles_fail_raises_kb_reranker_error(tmp_path, monkeypatch):
    from core.kb_search import KBRerankerError

    kb, dbp = _make_kb(tmp_path)
    _insert_case(dbp, 1, "케이스A")

    monkeypatch.setattr(
        kb_search_module, "llm_rerank",
        lambda *a, **k: (_ for _ in ()).throw(RerankError("항상 실패")),
    )

    with pytest.raises(KBRerankerError):
        kb._rerank("문제 설명", [_candidate(1, "케이스A")])


# ── LLM 경로 회귀 (기존 동작 불변) ────────────────────────────────────────────

def test_llm_prompt_path_unaffected_when_provider_not_endpoint(tmp_path, monkeypatch):
    kb, dbp = _make_kb(tmp_path, threshold=0.6)
    _insert_case(dbp, 1, "케이스A")
    kb._llm_profile = {"provider": "openai", "model": "qwen", "base_url": "http://ollama/v1"}
    kb._llm_fallback_profile = None

    monkeypatch.setattr(
        kb_search_module, "chat_with_profile",
        lambda **kwargs: json.dumps({"scores": [{"index": 1, "relevance_score": 0.9}]}),
    )

    results = kb._rerank("문제 설명", [_candidate(1, "케이스A")])
    assert [r.name for r in results] == ["케이스A"]
    assert results[0].relevance_score == 0.9
