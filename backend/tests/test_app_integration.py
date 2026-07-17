"""backend main.app — 라우터 통합 (TestClient).

backend는 프록시라 앱 기동에 DB·네트워크가 필요 없다. AA 호출은 aa_client
스텁으로, 칩 매핑은 임시 YAML로 격리한다 (실제 AA·config 미접촉).
"""

import httpx
import pytest
from starlette.testclient import TestClient

import main
import chip_resolver
from AnalyzingAssistant_client import aa_client


@pytest.fixture
def client():
    return TestClient(main.app, raise_server_exceptions=False)


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_cors_header_present(client):
    # dev 워크플로용 CORS — 교차 출처 응답에 ACAO 부여
    r = client.get("/health", headers={"Origin": "http://example.com:5173"})
    assert r.headers.get("access-control-allow-origin") == "*"


def test_chips_get_and_reload(client, tmp_path, monkeypatch):
    yaml_path = tmp_path / "map.yaml"
    yaml_path.write_text("mappings:\n  - pattern: rhea\n    chip: [RheaM]\n", encoding="utf-8")
    monkeypatch.setattr(chip_resolver, "_MAP_PATH", yaml_path)
    chip_resolver.reload()

    # YAML 갱신 후 reload → 새 매핑 반영 (§9-9)
    yaml_path.write_text(
        "mappings:\n  - pattern: rhea\n    chip: [RheaM]\n"
        "  - pattern: rose\n    chip: [RoseM, RoseP]\n",
        encoding="utf-8",
    )
    r = client.post("/api/settings/chips/reload")
    assert r.status_code == 200
    assert r.json() == {"chips": ["RheaM", "RoseM", "RoseP"]}
    assert client.get("/api/settings/chips").json()["chips"] == ["RheaM", "RoseM", "RoseP"]
    chip_resolver.reload()


def test_cases_passthrough_preserves_novel_field(client, monkeypatch):
    """§9-4: 프록시가 필드를 모르므로 새 필드도 그대로 AA에 전달 (조용한 유실 없음)."""
    captured = {}

    async def fake_create(payload):
        captured.update(payload)
        return {"id": 1}

    monkeypatch.setattr(aa_client, "create_case", fake_create)
    body = {"name": "케이스", "verdict": "defect", "review_status": "approved"}
    r = client.post("/api/cases", json=body)
    assert r.status_code == 200
    assert captured == body   # review_status 포함 전량 전달


def test_error_propagation_from_aa(client, monkeypatch):
    """§9-5: AA 4xx 상태코드·detail 원문 전파."""
    req = httpx.Request("POST", "http://127.0.0.1:8020/cases")
    resp = httpx.Response(422, json={"detail": "결함 판정에는 모듈명이 필수입니다."}, request=req)

    async def fake_create(payload):
        raise httpx.HTTPStatusError("e", request=req, response=resp)

    monkeypatch.setattr(aa_client, "create_case", fake_create)
    r = client.post("/api/cases", json={"name": "x", "verdict": "defect"})
    assert r.status_code == 422 and "모듈명" in r.json()["detail"]


@pytest.mark.skipif(not main._FRONTEND_DIST.exists(),
                    reason="frontend/dist 미빌드 — SPA 서빙 테스트 생략")
class TestSPAServing:
    def test_root_serves_index(self, client):
        r = client.get("/")
        assert r.status_code == 200 and '<div id="root">' in r.text

    def test_client_route_fallback(self, client):
        for route in ("/settings", "/cases", "/history"):
            assert '<div id="root">' in client.get(route).text

    def test_unknown_api_is_404_not_fallback(self, client):
        r = client.get("/api/nonexistent")
        assert r.status_code == 404 and "root" not in r.text
