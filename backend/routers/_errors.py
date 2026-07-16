"""
routers/_errors.py

AA 프록시 라우터 공용 오류 전파 헬퍼.

오류 전파 규약(backend 설계서 §4.2): AA 가 반환하는 4xx/5xx 상태코드와
detail 메시지는 변형 없이 프론트까지 그대로 전파한다 — AA 의 한국어 검증
메시지(케이스 v2 422 등)가 사용자 화면에 도달하는 것이 계약이다.
"""

from contextlib import contextmanager

import httpx
from fastapi import HTTPException


@contextmanager
def propagate_aa_errors():
    """AA 의 HTTP 4xx/5xx 상태코드와 detail 을 그대로 전파한다."""
    try:
        yield
    except httpx.HTTPStatusError as e:
        detail = e.response.text
        try:
            detail = e.response.json().get("detail", detail)
        except Exception:
            pass
        raise HTTPException(status_code=e.response.status_code, detail=detail)
