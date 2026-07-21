"""
chip_filter.py

defect 의 칩 정보에 따라 KB 패턴을 필터링한다.

규칙 (cases 의 profile_refs 필터와 동일 철학):
  - 패턴의 chip_tags 가 비어 있음        → 공통 패턴, 항상 적용
  - 패턴의 chip_tags 가 있음             → defect chip 과 교집합이 있을 때만 적용
  - defect chip 이 None/빈값            → 필터링하지 않음 (전체 적용)

chip 매칭은 대소문자를 무시한다.
"""

from __future__ import annotations

import json


def _as_str_list(raw) -> list[str]:
    """chip_tags / chip 값을 문자열 리스트로 정규화한다.

    DB row 에서 온 JSON 문자열, 이미 파싱된 리스트, 단일 문자열, None 을
    모두 허용한다. 파싱 실패·빈 값이면 [] 를 반환한다.
    """
    if not raw:
        return []
    if isinstance(raw, str):
        # JSON 배열 문자열이면 파싱, 아니면 단일 문자열로 취급
        try:
            parsed = json.loads(raw)
        except Exception:
            return [raw]
        raw = parsed
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, (list, tuple)):
        return [str(x) for x in raw if x]
    return []


def chip_matches(chip_tags, chip) -> bool:
    """chip_tags 와 defect chip 이 교집합을 가지면 True.

    둘 중 하나라도 비어 있으면 False (가중치/우대 대상 아님).
    매칭은 대소문자를 무시한다. 유사 케이스 검색의 칩 가중치에 사용한다.
    """
    chip_set = {c.lower() for c in _as_str_list(chip)}
    tag_set = {t.lower() for t in _as_str_list(chip_tags)}
    return bool(chip_set and tag_set and (chip_set & tag_set))


def filter_patterns_by_chip(patterns: list[dict], chip) -> list[dict]:
    """패턴 목록을 defect 의 칩 정보 기준으로 필터링한다.

    Parameters
    ----------
    patterns : 각 패턴 dict. chip_tags 키(JSON 문자열 또는 리스트)를 가질 수 있다.
    chip     : defect 의 칩 정보 (리스트 / 단일 문자열 / None).

    Returns
    -------
    필터를 통과한 패턴 목록. chip 이 비어 있으면 입력을 그대로 반환한다.
    """
    chip_set = {c.lower() for c in _as_str_list(chip)}
    if not chip_set:
        # 칩 정보 없음 → 전체 적용
        return patterns

    result: list[dict] = []
    for p in patterns:
        tags = _as_str_list(p.get("chip_tags"))
        if not tags:
            # 공통 패턴 → 항상 적용
            result.append(p)
            continue
        if chip_set & {t.lower() for t in tags}:
            # 칩 일치 → 적용
            result.append(p)
    return result
