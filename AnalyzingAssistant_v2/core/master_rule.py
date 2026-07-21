"""
core/master_rule.py

Master Rule — 패턴 매칭 전 전역 로그 스트림 정규화.

Stage 1(L_common) 출력에 적용되어 Stage 3 입력(L_normalized)을 만든다.

지원 rule_type:
  DEDUP_CONSECUTIVE : 같은 rule의 pattern 에 연속으로 매칭되는 라인을
                      1개로 축약한다.

예시:
  rule  : pattern="Mute",  rule_type=DEDUP_CONSECUTIVE
  입력  : Mute → Mute → Mute → Unmute
  출력  : Mute → Unmute                 ← 정상 흐름으로 인식

  rule  : pattern="Unmute", rule_type=DEDUP_CONSECUTIVE
  입력  : Mute → Unmute → Unmute
  출력  : Mute → Unmute                 ← 정상 흐름으로 인식

공개 API:
  apply(lines, rules) → list[LogLine]       # 모든 룰 순서대로 적용
  load_rules(db_path) → list[dict]          # DB 에서 룰 목록 로드
  MasterRuleGenerator.generate(text)        # 자연어 → 룰 생성 (LLM)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path

from core.db import DB_PATH, get_conn
from core.llm import chat
from core.log_refiner import LogLine


# ── 공개 API ──────────────────────────────────────────────────────────────────

def apply(lines: list[LogLine], rules: list[dict]) -> list[LogLine]:
    """
    Master Rule 목록을 순서대로 적용해 정규화된 로그 스트림을 반환한다.

    Parameters
    ----------
    lines : Stage 1 출력 (L_common)
    rules : load_rules() 로 로드한 룰 목록

    Returns
    -------
    정규화된 list[LogLine] (L_normalized)
    """
    for rule in rules:
        rtype = rule.get("rule_type", "")
        if rtype == "DEDUP_CONSECUTIVE":
            lines = _dedup_consecutive(lines, rule["pattern"])
        # 추후 rule_type 추가 시 여기에 elif 로 확장
    return lines


def load_rules(db_path: Path = DB_PATH) -> list[dict]:
    """DB 에서 마스터 룰 전체를 로드한다."""
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT id, name, rule_type, pattern, comment FROM master_rules ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


# ── rule_type 구현 ─────────────────────────────────────────────────────────────

def _dedup_consecutive(lines: list[LogLine], pattern: str) -> list[LogLine]:
    """
    DEDUP_CONSECUTIVE 룰 적용.

    pattern 에 매칭되는 라인이 연속으로 이어질 경우 첫 번째만 남기고
    나머지는 count 에 합산한다.

    패턴이 달라지는 순간(매칭 → 비매칭, 또는 비매칭 → 매칭)에는
    새 라인으로 시작한다.

    예시 (pattern="Mute"):
      Mute(×1) → Mute(×1) → Mute(×1) → Unmute  →  Mute(×3) → Unmute
    """
    compiled = re.compile(pattern, re.IGNORECASE)
    result: list[LogLine] = []

    for ll in lines:
        if result and compiled.search(ll.message) and compiled.search(result[-1].message):
            # 직전 라인도, 현재 라인도 같은 패턴에 매칭 → 병합
            result[-1] = replace(result[-1], count=result[-1].count + ll.count)
        else:
            result.append(ll)

    return result


# ── LLM 기반 룰 생성 ──────────────────────────────────────────────────────────

@dataclass
class RuleGenerationResult:
    """자연어 → LLM 생성 마스터 룰 결과."""
    name: str
    rule_type: str      # 현재 항상 "DEDUP_CONSECUTIVE"
    pattern: str        # regex
    comment: str
    explanation: str    # LLM 이 생성 근거를 설명한 텍스트 (UI 표시용)


_SYSTEM_PROMPT = """\
당신은 Linux 커널 로그 분석 시스템의 마스터 룰 설계 전문가입니다.
사용자가 자연어로 설명한 로그 정규화 요구사항을 JSON 형식의 마스터 룰로 변환하세요.

━━━ 마스터 룰이란 ━━━

패턴 매칭 전에 전역적으로 로그 스트림을 정규화하는 규칙입니다.
목적: 반복/중복 이벤트를 의미 있는 단일 이벤트로 축약해 패턴 매칭 품질을 높입니다.

━━━ 현재 지원 rule_type ━━━

DEDUP_CONSECUTIVE
  - pattern(regex) 에 매칭되는 라인이 연속으로 반복될 때 첫 번째 1개만 남기고 나머지 병합
  - 사용 예: "Mute→Mute→Mute" → "Mute(×3)"  /  "Unmute→Unmute" → "Unmute(×2)"
  - 적합한 상황: 같은 커맨드가 연속 중복 발생해도 의미는 동일한 경우
                (예: 음소거, 음소거 해제, 전원 토글, 리셋 신호 등)

━━━ 출력 JSON 스키마 ━━━

{
  "name": "룰을 한눈에 설명하는 짧은 이름 (예: '연속 Mute 정규화')",
  "rule_type": "DEDUP_CONSECUTIVE",
  "pattern": "매칭 대상 regex 문자열",
  "comment": "이 룰이 왜 필요한지 한 문장 설명",
  "explanation": "패턴 선택 근거와 예상 동작을 2~4줄로 설명"
}

━━━ regex 작성 지침 ━━━
- 대소문자 무관(case-insensitive)으로 적용됨 — 별도 플래그 불필요
- 너무 좁은 패턴(완전 일치)보다 핵심 키워드 위주로 작성
- 특수문자는 이스케이프: \\[ \\] \\( \\) 등
- 반드시 유효한 Python re 모듈 regex 여야 함

반드시 JSON 만 출력하고 다른 텍스트는 포함하지 마세요.
"""

_MAX_RETRIES = 3


class MasterRuleGenerator:
    """자연어 설명을 받아 마스터 룰을 생성한다."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path

    def generate(self, text: str) -> RuleGenerationResult:
        """
        Parameters
        ----------
        text : 사용자가 자연어로 작성한 정규화 요구사항

        Returns
        -------
        RuleGenerationResult

        Raises
        ------
        RuntimeError : _MAX_RETRIES 회 모두 실패한 경우
        """
        existing_rules = load_rules(self.db_path)
        return self._call_with_retry(text, existing_rules)

    def _call_with_retry(
        self,
        text: str,
        existing_rules: list[dict],
        error_feedback: str = "",
    ) -> RuleGenerationResult:
        last_error = error_feedback

        for attempt in range(_MAX_RETRIES):
            user_prompt = _build_user_prompt(text, existing_rules, last_error)

            # chat() 의 네트워크/LLM 오류는 retry feedback 으로 활용할 수 없으므로
            # try 밖에서 호출하여 즉시 상위로 전파한다. retry 는 LLM 응답이 정상
            # 수신되었으나 형식/검증에 실패한 경우에만 의미가 있다.
            raw = chat(
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user",   "content": user_prompt},
                ],
                json_mode   = True,
                temperature = 0.1,
            )

            try:
                result = _parse_rule_response(raw)
                return result
            except (json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
                last_error = str(e)

        raise RuntimeError(
            f"마스터 룰 생성 실패 — {_MAX_RETRIES}회 재시도 후 마지막 오류: {last_error}"
        )


def _build_user_prompt(
    text: str,
    existing_rules: list[dict],
    error_feedback: str = "",
) -> str:
    parts: list[str] = ["/no_think"]

    if existing_rules:
        parts.append("━━━ 현재 등록된 마스터 룰 (참고용, 중복 생성 지양) ━━━")
        for r in existing_rules:
            parts.append(
                f"- [{r['rule_type']}] {r['name']}  |  pattern: {r['pattern']}"
                + (f"  |  {r['comment']}" if r.get("comment") else "")
            )
    else:
        parts.append("(현재 등록된 마스터 룰 없음)")

    parts.append("\n━━━ 정규화 요구사항 (자연어) ━━━")
    parts.append(text)

    if error_feedback:
        parts.append("\n━━━ 이전 시도 오류 (수정 후 재출력) ━━━")
        parts.append(error_feedback)

    return "\n".join(parts)


def _parse_rule_response(raw: str) -> RuleGenerationResult:
    """LLM JSON 응답을 파싱하고 기본 검증을 수행한다."""
    data = json.loads(raw)

    required = ("name", "rule_type", "pattern", "comment", "explanation")
    missing = [k for k in required if k not in data]
    if missing:
        raise ValueError(f"응답에 필수 필드가 없습니다: {missing}")

    rule_type = data["rule_type"]
    if rule_type not in ("DEDUP_CONSECUTIVE",):
        raise ValueError(
            f"지원하지 않는 rule_type: {rule_type!r}. "
            "현재 지원: DEDUP_CONSECUTIVE"
        )

    pattern = data["pattern"]
    try:
        re.compile(pattern)
    except re.error as e:
        raise ValueError(f"유효하지 않은 regex 패턴: {pattern!r} — {e}") from e

    name = str(data["name"]).strip()
    if not name:
        raise ValueError("name 이 비어 있습니다")

    return RuleGenerationResult(
        name        = name,
        rule_type   = rule_type,
        pattern     = pattern,
        comment     = str(data["comment"]).strip(),
        explanation = str(data["explanation"]).strip(),
    )
