"""api/router/settings.py — reranker(provider='vllm-rerank') 연결 확인.

기존 _check_connection 은 provider 미인식 시 chat completion 을 호출하는
_check_connection_openai 로 기본 낙하해, cross-encoder rerank 엔드포인트에
대해 항상 실패했다(reranker 엔드포인트 구현 설계 §0 과 동일한 실패 패턴).
vllm-rerank 전용 분기가 실제 rerank 호출로 연결을 확인하는지 검증한다.
"""

import asyncio

import api.router.settings as settings_module
from api.router.settings import (
    ConnectionCheckRequest,
    _check_connection,
    _check_connection_vllm_rerank,
    check_llm_connection,
)
from core.config import llm_config
from core.llm import RerankError


def _run(coro):
    return asyncio.run(coro)


# ── 분기 라우팅 ───────────────────────────────────────────────────────────────

def test_dispatch_routes_vllm_rerank_to_dedicated_check(monkeypatch):
    """provider='vllm-rerank' 가 chat 계열(_check_connection_openai)로 새지 않아야 한다."""
    called = {}

    async def fake_check(base_url, api_key, model, rerank_path):
        called["args"] = (base_url, api_key, model, rerank_path)
        return True, "연결 성공"

    monkeypatch.setattr(settings_module, "_check_connection_vllm_rerank", fake_check)

    ok, detail = _run(_check_connection(
        "http://vllm:8000/v1", "key", "bge-reranker",
        provider="vllm-rerank", rerank_path="/rerank",
    ))
    assert ok is True
    assert called["args"] == ("http://vllm:8000/v1", "key", "bge-reranker", "/rerank")


def test_dispatch_does_not_fall_through_to_openai_check(monkeypatch):
    """vllm-rerank 가 chat 검사로 새면 이 테스트가 실패해야 한다(회귀 방지)."""
    def fail_if_called(*a, **k):
        raise AssertionError("_check_connection_openai 가 호출되면 안 된다 (vllm-rerank 는 전용 분기)")

    async def fake_openai_check(*a, **k):
        fail_if_called()

    monkeypatch.setattr(settings_module, "_check_connection_openai", fake_openai_check)
    monkeypatch.setattr(
        "core.llm.rerank",
        lambda profile, query, docs: [(0, 0.9)],
    )

    ok, detail = _run(_check_connection(
        "http://vllm:8000/v1", "", "bge-reranker", provider="vllm-rerank",
    ))
    assert ok is True


# ── _check_connection_vllm_rerank 자체 ────────────────────────────────────────

def test_vllm_rerank_check_success(monkeypatch):
    captured = {}

    def fake_rerank(profile, query, documents):
        captured["profile"] = profile
        captured["query"] = query
        captured["documents"] = documents
        return [(0, 0.42)]

    monkeypatch.setattr("core.llm.rerank", fake_rerank)

    ok, detail = _run(_check_connection_vllm_rerank(
        "http://vllm:8000/v1", "secret", "bge-reranker-v2-m3", "/rerank",
    ))
    assert ok is True
    assert detail == "연결 성공"
    # 최소 페이로드로 호출됐는지 — ping 쿼리/문서 1건
    assert captured["query"] == "ping"
    assert captured["documents"] == ["ping"]
    assert captured["profile"]["provider"] == "vllm-rerank"
    assert captured["profile"]["base_url"] == "http://vllm:8000/v1"
    assert captured["profile"]["rerank_path"] == "/rerank"


def test_vllm_rerank_check_failure_returns_message(monkeypatch):
    def failing_rerank(profile, query, documents):
        raise RerankError("rerank 엔드포인트 연결 실패: http://vllm:8000/v1/rerank")

    monkeypatch.setattr("core.llm.rerank", failing_rerank)

    ok, detail = _run(_check_connection_vllm_rerank(
        "http://vllm:8000/v1", "", "bge-reranker", "/rerank",
    ))
    assert ok is False
    assert "연결 실패" in detail


def test_vllm_rerank_check_placeholder_host_fails_cleanly():
    """config.yaml 의 placeholder(base_url=<vllm-host>) 로도 예외 없이 (False, 메시지) 를 반환해야 한다."""
    ok, detail = _run(_check_connection_vllm_rerank(
        "http://<vllm-host>:<port>/v1", "", "bge-reranker", "/rerank",
    ))
    assert ok is False
    assert isinstance(detail, str) and detail


# ── 엔드포인트 배선 (rerank_path 전달) ────────────────────────────────────────

def test_check_llm_connection_endpoint_passes_rerank_path(monkeypatch):
    # _get_llm_profile 은 core_config.llm() 이 아니라 llm_config.find_llm_profile()
    # (디스크의 실제 config.yaml)을 직접 읽으므로 여기를 패치해야 한다.
    fake_profile = {
        "name": "bge-reranker-vllm", "provider": "vllm-rerank",
        "base_url": "http://vllm:8000/v1", "api_key": "",
        "model": "bge-reranker-v2-m3", "rerank_path": "/v2/rerank",
    }
    monkeypatch.setattr(
        llm_config, "find_llm_profile",
        lambda name: fake_profile if name == "bge-reranker-vllm" else None,
    )

    captured = {}

    async def fake_check(base_url, api_key, model, rerank_path):
        captured["rerank_path"] = rerank_path
        return True, "연결 성공"

    monkeypatch.setattr(settings_module, "_check_connection_vllm_rerank", fake_check)

    result = _run(check_llm_connection(ConnectionCheckRequest(profile="bge-reranker-vllm")))
    assert result == {"ok": True, "detail": "연결 성공"}
    assert captured["rerank_path"] == "/v2/rerank"


def test_check_llm_connection_endpoint_defaults_rerank_path_when_absent(monkeypatch):
    """프로필에 rerank_path 가 없으면 기본값 '/rerank' 를 써야 한다."""
    fake_profile = {
        "name": "bge-reranker-vllm", "provider": "vllm-rerank",
        "base_url": "http://vllm:8000/v1", "api_key": "", "model": "bge-reranker",
    }
    monkeypatch.setattr(
        llm_config, "find_llm_profile",
        lambda name: fake_profile if name == "bge-reranker-vllm" else None,
    )

    captured = {}

    async def fake_check(base_url, api_key, model, rerank_path):
        captured["rerank_path"] = rerank_path
        return True, "연결 성공"

    monkeypatch.setattr(settings_module, "_check_connection_vllm_rerank", fake_check)

    _run(check_llm_connection(ConnectionCheckRequest(profile="bge-reranker-vllm")))
    assert captured["rerank_path"] == "/rerank"
