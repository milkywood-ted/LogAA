"""middleware/ip_allowlist.py — IP 허용목록 미들웨어 (§9-2 검증 정식화)."""

from fastapi import FastAPI
from starlette.testclient import TestClient

from middleware.ip_allowlist import IPAllowlistMiddleware, _parse_networks


def _app(allowed):
    app = FastAPI()
    app.add_middleware(IPAllowlistMiddleware, allowed_ips=allowed)

    @app.get("/health")
    def health():
        return {"ok": True}

    return app


def test_empty_list_allows_all():
    c = TestClient(_app([]))
    assert c.get("/health").status_code == 200


def test_localhost_always_allowed():
    for host in ("127.0.0.1", "::1"):
        c = TestClient(_app(["12.26.0.0/16"]), client=(host, 5000))
        assert c.get("/health").status_code == 200


def test_cidr_inside_and_outside():
    app = _app(["12.26.0.0/16"])
    assert TestClient(app, client=("12.26.5.9", 1)).get("/health").status_code == 200
    r = TestClient(app, client=("12.27.0.1", 1)).get("/health")
    assert r.status_code == 403 and r.json()["detail"] == "허용되지 않은 접근"


def test_mixed_single_and_multiple_ranges():
    app = _app(["203.0.113.7", "10.0.0.0/8", "192.168.1.0/24"])
    expect = {
        "203.0.113.7": 200, "203.0.113.8": 403,
        "10.255.1.2": 200, "192.168.1.50": 200, "192.168.2.1": 403,
    }
    for ip, code in expect.items():
        assert TestClient(app, client=(ip, 1)).get("/health").status_code == code


def test_ipv6_range():
    app = _app(["2001:db8::/32"])
    assert TestClient(app, client=("2001:db8::1", 1)).get("/health").status_code == 200
    assert TestClient(app, client=("2001:dbff::1", 1)).get("/health").status_code == 403


def test_parse_networks_skips_invalid():
    nets = _parse_networks(["12.26.1.5/16", "bad-entry", "127.0.0.1"])
    assert len(nets) == 2   # host-bit 표기 허용, 잘못된 항목 skip
