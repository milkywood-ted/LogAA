"""IP 허용목록 미들웨어 (backend 설계서 §9-2 대응).

config.yaml `allowed_client_ips`(개별 IP 또는 CIDR 대역, 다중 등록)로
접근 가능한 클라이언트를 제한한다.

정책:
  - 목록이 비어 있으면 전체 허용 (opt-in — 미설정 시 현행 동작 유지)
  - localhost(127.0.0.1/::1)는 목록과 무관하게 항상 허용 (헬스체크·동일 호스트 호출)
  - 클라이언트 IP는 request.client.host 기준 (직접 접근 전제 — X-Forwarded-For
    미신뢰. 앞단 리버스 프록시 도입 시 이 부분 재검토 필요)
"""

from __future__ import annotations

import ipaddress
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

_LOOPBACK = (ipaddress.ip_address("127.0.0.1"), ipaddress.ip_address("::1"))


def _parse_networks(entries: list[str]) -> list[ipaddress._BaseNetwork]:
    """IP/CIDR 문자열 목록을 네트워크 객체로 파싱한다. 잘못된 항목은 경고 후 건너뛴다."""
    networks: list[ipaddress._BaseNetwork] = []
    for entry in entries:
        try:
            # strict=False 로 "12.26.1.5/16" 같은 호스트 비트 포함 표기도 허용
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            logger.warning("allowed_client_ips 항목을 파싱할 수 없어 건너뜁니다: %r", entry)
    return networks


class IPAllowlistMiddleware(BaseHTTPMiddleware):
    """allowed_client_ips 에 속하지 않는 클라이언트의 요청을 403 으로 차단한다."""

    def __init__(self, app, allowed_ips: list[str]):
        super().__init__(app)
        self._networks = _parse_networks(allowed_ips)

    async def dispatch(self, request: Request, call_next):
        # 목록 미설정 → 전체 허용
        if not self._networks:
            return await call_next(request)

        client = request.client
        if client is None:
            # 클라이언트 정보를 알 수 없으면(예: 테스트 전송) 차단
            return JSONResponse(status_code=403, content={"detail": "허용되지 않은 접근"})

        try:
            client_ip = ipaddress.ip_address(client.host)
        except ValueError:
            return JSONResponse(status_code=403, content={"detail": "허용되지 않은 접근"})

        # localhost 항상 허용
        if client_ip in _LOOPBACK:
            return await call_next(request)

        if any(client_ip in net for net in self._networks):
            return await call_next(request)

        logger.warning("허용되지 않은 IP 접근 차단: %s", client.host)
        return JSONResponse(status_code=403, content={"detail": "허용되지 않은 접근"})
