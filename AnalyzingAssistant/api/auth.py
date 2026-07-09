"""
api/auth.py

API key 인증.

config/api_keys.txt 에 한 줄에 하나씩 key를 등록한다.
환경변수 LOGAA_API_KEY 로 단일 key를 설정할 수도 있다.

사용:
    from api.auth import verify_api_key
    from fastapi import Security
    router = APIRouter(dependencies=[Security(verify_api_key)])
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

_HEADER_NAME = "X-API-Key"
_api_key_header = APIKeyHeader(name=_HEADER_NAME, auto_error=False)

_KEY_FILE = Path(__file__).parent.parent / "config" / "api_keys.txt"


def _load_keys() -> set[str]:
    keys: set[str] = set()

    # 환경변수 우선
    env_key = os.environ.get("LOGAA_API_KEY", "").strip()
    if env_key:
        keys.add(env_key)

    # 파일에서 추가 로드
    if _KEY_FILE.exists():
        for line in _KEY_FILE.read_text(encoding="utf-8").splitlines():
            key = line.strip()
            if key and not key.startswith("#"):
                keys.add(key)

    return keys


def verify_api_key(api_key: str | None = Security(_api_key_header)) -> str:
    """FastAPI dependency — 유효한 API key 가 없으면 401 반환."""
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-API-Key 헤더가 없습니다.",
        )

    valid_keys = _load_keys()

    # key가 하나도 등록 안 된 경우 개발 편의상 통과 (경고 로그)
    if not valid_keys:
        import logging
        logging.getLogger(__name__).warning(
            "API key가 등록되어 있지 않습니다. "
            "config/api_keys.txt 또는 LOGAA_API_KEY 환경변수를 설정하세요. "
            "현재는 모든 요청을 허용합니다."
        )
        return api_key

    if api_key not in valid_keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="유효하지 않은 API key입니다.",
        )

    return api_key