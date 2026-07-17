"""routers/_errors.py propagate_aa_errors — AA 오류 원문 전파 (§9-5 검증 정식화)."""

import httpx
import pytest
from fastapi import HTTPException

from routers._errors import propagate_aa_errors


def _raise_aa(status, *, detail=None, text=None):
    req = httpx.Request("GET", "http://127.0.0.1:8020/x")
    if detail is not None:
        resp = httpx.Response(status, json={"detail": detail}, request=req)
    else:
        resp = httpx.Response(status, text=text or "", request=req)
    raise httpx.HTTPStatusError("err", request=req, response=resp)


def test_propagates_status_and_korean_detail():
    with pytest.raises(HTTPException) as e:
        with propagate_aa_errors():
            _raise_aa(422, detail="결함 판정에는 문제현상 발현 영역(모듈명)이 필수입니다.")
    assert e.value.status_code == 422
    assert "모듈명" in e.value.detail


def test_propagates_various_4xx():
    for code in (400, 404, 409):
        with pytest.raises(HTTPException) as e:
            with propagate_aa_errors():
                _raise_aa(code, detail=f"오류 {code}")
        assert e.value.status_code == code


def test_non_json_body_falls_back_to_text():
    with pytest.raises(HTTPException) as e:
        with propagate_aa_errors():
            _raise_aa(502, text="Bad Gateway raw")
    assert e.value.status_code == 502 and e.value.detail == "Bad Gateway raw"


def test_no_error_passes_through():
    with propagate_aa_errors():
        value = 1 + 1
    assert value == 2   # 예외 없으면 아무 일도 없음
