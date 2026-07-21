"""
api/auth.py

API key 인증.

config/api_keys.txt 에 한 줄에 하나씩 key를 등록한다.
환경변수 LOGAA_API_KEY 로 단일 key를 설정할 수도 있다.

키가 하나도 등록되지 않은 경우 기본 동작은 401 차단이다.
환경변수 LOGAA_ALLOW_EMPTY_KEYS=true 가 명시적으로 설정된 경우에 한해
모든 요청을 통과시키며, 이 경우 ERROR 레벨 로그를 남긴다(개발 편의용).

사용:
    from api.auth import verify_api_key
    from fastapi import Security
    router = APIRouter(dependencies=[Security(verify_api_key)])
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

logger = logging.getLogger(__name__)

_HEADER_NAME = "X-API-Key"
_api_key_header = APIKeyHeader(name=_HEADER_NAME, auto_error=False)

_KEY_FILE = Path(__file__).parent.parent / "config" / "api_keys.txt"
_ALLOW_EMPTY_ENV = "LOGAA_ALLOW_EMPTY_KEYS"


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

    # key가 하나도 등록되지 않은 경우:
    #   - 기본: 401 차단 (운영 안전)
    #   - LOGAA_ALLOW_EMPTY_KEYS=true 일 때만 통과 (개발 편의)
    if not valid_keys:
        if os.environ.get(_ALLOW_EMPTY_ENV, "").strip().lower() == "true":
            logger.error(
                "API key가 등록되어 있지 않습니다. "
                "%s=true 로 인증이 우회되어 모든 요청이 허용됩니다. "
                "운영 환경에서는 이 설정을 제거하고 키를 등록하세요.",
                _ALLOW_EMPTY_ENV,
            )
            return api_key
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "서버에 등록된 API key가 없습니다. "
                "config/api_keys.txt 또는 LOGAA_API_KEY 환경변수를 설정하세요."
            ),
        )

    if api_key not in valid_keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="유효하지 않은 API key입니다.",
        )

    return api_key
