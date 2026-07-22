"""core/llm.py — rerank() / _rerank_vllm() (선구현 — 실제 vLLM 호출 불가, httpx 목킹).

reranker 엔드포인트 구현 설계 문서(Document/Reranker 전용 엔드포인트 도입/)
§4.1 요청·응답 스키마, §C4 URL 합성 규칙(base_url + rerank_path)을 검증한다.
"""

import httpx
import pytest

import core.llm as llm
from core.llm import RerankError, rerank


class _FakeResponse:
    """httpx.Response 최소 흉내 — status_code/json()/text/raise_for_status()."""

    def __init__(self, status_code: int = 200, json_body: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._json_body  = json_body if json_body is not None else {}
        self.text         = text or str(json_body)
        self.request       = httpx.Request("POST", "http://test")

    def json(self):
        return self._json_body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=self.request,
                response=httpx.Response(self.status_code, request=self.request),
            )


def _profile(**overrides) -> dict:
    base = {
        "provider": "vllm-rerank",
        "base_url": "http://vllm-host:8000/v1",
        "model":    "BAAI/bge-reranker-v2-m3",
        "api_key":  "",
    }
    base.update(overrides)
    return base


# ── provider 분기 ─────────────────────────────────────────────────────────────

def test_rerank_unsupported_provider_raises():
    with pytest.raises(RerankError, match="지원하지 않는 rerank provider"):
        rerank(_profile(provider="openai"), "q", ["d1"])


def test_rerank_missing_provider_raises():
    with pytest.raises(RerankError):
        rerank({"base_url": "http://x"}, "q", ["d1"])


# ── URL 합성 (§C4) ────────────────────────────────────────────────────────────

def test_url_composition_v1_base_plus_default_rerank_path(monkeypatch):
    """base_url 이 /v1 로 끝나고 rerank_path 기본값 /rerank → 실효 경로 /v1/rerank."""
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        return _FakeResponse(200, {"results": [{"index": 0, "relevance_score": 0.9}]})

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    rerank(_profile(base_url="http://vllm-host:8000/v1"), "q", ["d1"])
    assert captured["url"] == "http://vllm-host:8000/v1/rerank"


def test_url_composition_respects_custom_rerank_path(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        return _FakeResponse(200, {"results": [{"index": 0, "relevance_score": 0.5}]})

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    rerank(_profile(base_url="http://vllm-host:8000", rerank_path="/v2/rerank"), "q", ["d1"])
    assert captured["url"] == "http://vllm-host:8000/v2/rerank"


def test_url_composition_strips_trailing_slash_on_base_url(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        return _FakeResponse(200, {"results": [{"index": 0, "relevance_score": 0.5}]})

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    rerank(_profile(base_url="http://vllm-host:8000/v1/"), "q", ["d1"])
    assert captured["url"] == "http://vllm-host:8000/v1/rerank"


# ── 요청 페이로드 (§4.1) ──────────────────────────────────────────────────────

def test_request_payload_contains_model_query_documents(monkeypatch):
    captured = {}

    def fake_post(url, json, **kwargs):
        captured["json"] = json
        return _FakeResponse(200, {"results": [{"index": 0, "relevance_score": 0.5}]})

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    rerank(_profile(model="my-model"), "부팅 중 커널 패닉", ["doc0", "doc1"])
    assert captured["json"] == {
        "model": "my-model", "query": "부팅 중 커널 패닉", "documents": ["doc0", "doc1"],
    }


def test_top_n_included_when_given(monkeypatch):
    captured = {}

    def fake_post(url, json, **kwargs):
        captured["json"] = json
        return _FakeResponse(200, {"results": [{"index": 0, "relevance_score": 0.5}]})

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    rerank(_profile(), "q", ["d1", "d2", "d3"], top_n=2)
    assert captured["json"]["top_n"] == 2


def test_top_n_omitted_when_none(monkeypatch):
    captured = {}

    def fake_post(url, json, **kwargs):
        captured["json"] = json
        return _FakeResponse(200, {"results": [{"index": 0, "relevance_score": 0.5}]})

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    rerank(_profile(), "q", ["d1"])
    assert "top_n" not in captured["json"]


def test_api_key_sets_bearer_header(monkeypatch):
    captured = {}

    def fake_post(url, headers, **kwargs):
        captured["headers"] = headers
        return _FakeResponse(200, {"results": [{"index": 0, "relevance_score": 0.5}]})

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    rerank(_profile(api_key="secret-key"), "q", ["d1"])
    assert captured["headers"]["Authorization"] == "Bearer secret-key"


def test_empty_api_key_omits_auth_header(monkeypatch):
    captured = {}

    def fake_post(url, headers, **kwargs):
        captured["headers"] = headers
        return _FakeResponse(200, {"results": [{"index": 0, "relevance_score": 0.5}]})

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    rerank(_profile(api_key=""), "q", ["d1"])
    assert "Authorization" not in captured["headers"]


# ── 응답 파싱 (§4.1) ──────────────────────────────────────────────────────────

def test_response_parsed_to_index_score_tuples(monkeypatch):
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: _FakeResponse(200, {
        "results": [
            {"index": 0, "relevance_score": 0.98},
            {"index": 2, "relevance_score": 0.71},
        ],
    }))
    result = rerank(_profile(), "q", ["d0", "d1", "d2"])
    assert result == [(0, 0.98), (2, 0.71)]


# ── 오류 처리 ──────────────────────────────────────────────────────────────────

def test_http_error_status_raises_rerank_error(monkeypatch):
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: _FakeResponse(500, text="internal error"))
    with pytest.raises(RerankError, match="HTTP 500"):
        rerank(_profile(), "q", ["d1"])


def test_connection_error_raises_rerank_error(monkeypatch):
    def fake_post(*a, **k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    with pytest.raises(RerankError, match="연결 실패"):
        rerank(_profile(), "q", ["d1"])


def test_missing_results_key_raises_rerank_error(monkeypatch):
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: _FakeResponse(200, {"unexpected": []}))
    with pytest.raises(RerankError, match="응답 형식"):
        rerank(_profile(), "q", ["d1"])


def test_malformed_result_item_raises_rerank_error(monkeypatch):
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: _FakeResponse(
        200, {"results": [{"index": "not-an-int"}]},
    ))
    with pytest.raises(RerankError, match="응답 형식"):
        rerank(_profile(), "q", ["d1"])


def test_empty_results_raises_rerank_error(monkeypatch):
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: _FakeResponse(200, {"results": []}))
    with pytest.raises(RerankError, match="빈 결과"):
        rerank(_profile(), "q", ["d1"])
