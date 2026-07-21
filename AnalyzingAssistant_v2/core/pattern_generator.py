"""
core/pattern_generator.py

자연어 설명 → 패턴 구조 생성 + 기존 패턴과의 관계 분석.

흐름:
  1단계 — DB에서 관련 기존 패턴 조회 (키워드 기반 필터)
  2단계 — LLM(Qwen3-14B) 호출: 패턴 생성 + 관계 분석
  3단계 — pydantic 검증 + 참조 무결성 확인
         실패 시 오류 메시지를 포함해 최대 MAX_RETRIES 회 재시도

공개 API:
  PatternGenerator.generate(text) → GenerationResult
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from core.db import DB_PATH, get_conn
from core.llm import chat
from core.pattern_seeder import AnyPattern, CompositePattern, _parse_pattern

MAX_RETRIES          = 3
MAX_CONTEXT_PATTERNS = 20

# ── 결과 타입 ─────────────────────────────────────────────────────────────────

RelationType = Literal[
    "references",   # 새 패턴이 기존 패턴을 component 로 직접 참조
    "similar",      # 내용이 유사 (중복 주의)
    "extends",      # 기존 패턴보다 더 넓은 조건
    "subset",       # 기존 패턴의 일부 조건만 사용
    "conflicts",    # 기존 패턴과 사실상 동일 → 저장 불필요
    "complement",   # 기존 패턴의 보완/반대 조건
]


@dataclass
class Relation:
    type: str           # RelationType
    target_name: str    # 기존 패턴 name
    reason: str         # LLM 이 분석한 근거


@dataclass
class GenerationResult:
    pattern: AnyPattern          # 생성된 패턴 (pydantic 검증 완료)
    relations: list[Relation]    # 기존 패턴과의 관계 목록
    context_patterns: list[dict] # LLM 에 전달된 기존 패턴 (UI 표시용)


# ── PatternGenerator ──────────────────────────────────────────────────────────

class PatternGenerator:
    """자연어 설명을 받아 패턴을 생성하고 기존 패턴과의 관계를 분석한다."""

    def __init__(
        self,
        db_path: Path = DB_PATH,
    ) -> None:
        self.db_path = db_path

    def generate(self, text: str) -> GenerationResult:
        """
        Parameters
        ----------
        text : 사용자가 자연어로 작성한 패턴 설명

        Returns
        -------
        GenerationResult — 패턴 + 관계 + 참조된 기존 패턴

        Raises
        ------
        RuntimeError : MAX_RETRIES 회 모두 실패한 경우
        """
        context_patterns, all_names = self._fetch_context(text)
        return self._call_with_retry(text, context_patterns, all_names)

    # ── 1단계: 관련 패턴 조회 ─────────────────────────────────────────────────

    def _fetch_context(
        self, text: str
    ) -> tuple[list[dict], list[str]]:
        """
        DB 에서 패턴을 읽어 다음 두 가지를 반환한다.
          context_patterns : 키워드가 겹치는 패턴의 상세 정보 (관계 분석용)
          all_names        : 모든 패턴의 name 목록 (COMPOSITE 참조 후보)
        """
        with get_conn(self.db_path) as conn:
            all_rows = conn.execute(
                "SELECT id, name, type, description, keywords, "
                "pattern, event_dedup_window_sec, "
                "step_dedup, non_overlapping, "
                "window_sec, count_threshold, count_unique_only, "
                "trigger_pattern, absent_pattern, "
                "operator, weight "
                "FROM patterns"
            ).fetchall()

            steps_by_pid: dict[int, list[str]] = {}
            for s in conn.execute(
                "SELECT pattern_id, pattern FROM pattern_steps "
                "ORDER BY pattern_id, step_order"
            ).fetchall():
                steps_by_pid.setdefault(s["pattern_id"], []).append(s["pattern"])

            comps_by_pid: dict[int, list[str]] = {}
            for c in conn.execute(
                "SELECT pc.pattern_id, p.name "
                "FROM pattern_components pc "
                "JOIN patterns p ON pc.ref_pattern_id = p.id "
                "ORDER BY pc.pattern_id, pc.component_order"
            ).fetchall():
                comps_by_pid.setdefault(c["pattern_id"], []).append(c["name"])

        all_names = [r["name"] for r in all_rows]

        # 패턴 dict 구성
        def to_dict(row) -> dict:
            d = dict(row)
            d["keywords"] = json.loads(d["keywords"])
            pid = d["id"]
            if d["type"] == "SEQUENCE" and pid in steps_by_pid:
                d["steps"] = steps_by_pid[pid]
            if d["type"] == "COMPOSITE" and pid in comps_by_pid:
                d["components"] = comps_by_pid[pid]
            return d

        all_patterns = [to_dict(r) for r in all_rows]

        # 키워드 기반 필터: 패턴의 keyword 가 입력 텍스트에 등장하는 것만 선택
        text_lower = text.lower()
        related = [
            p for p in all_patterns
            if any(kw.lower() in text_lower for kw in p["keywords"])
        ]

        # 관련 패턴이 없으면 전체에서 MAX_CONTEXT_PATTERNS 개 사용
        if not related:
            related = all_patterns[:MAX_CONTEXT_PATTERNS]

        return related, all_names

    # ── 2단계: LLM 호출 + 재시도 ─────────────────────────────────────────────

    def _call_with_retry(
        self,
        text: str,
        context_patterns: list[dict],
        all_names: list[str],
        error_feedback: str = "",
    ) -> GenerationResult:
        last_error = error_feedback

        for attempt in range(MAX_RETRIES):
            user_prompt = _build_user_prompt(text, context_patterns, all_names, last_error)

            # chat() 의 네트워크/LLM 오류는 retry feedback 으로 활용할 수 없으므로
            # try 밖에서 호출하여 즉시 상위로 전파한다. retry 는 LLM 응답이 정상
            # 수신되었으나 형식/검증에 실패한 경우(JSON 오류·필수 키 누락·참조
            # 무결성 위반)에만 의미가 있다.
            raw = chat(
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user",   "content": user_prompt},
                ],
                json_mode   = True,
                temperature = 0.1,
            )

            try:
                result = _parse_response(raw, all_names)
                result.context_patterns = context_patterns
                return result
            except (json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
                last_error = str(e)

        raise RuntimeError(
            f"패턴 생성 실패 — {MAX_RETRIES}회 재시도 후 마지막 오류: {last_error}"
        )


# ── 프롬프트 ──────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
당신은 Linux 커널 로그 분석 전문가입니다.
사용자가 자연어로 설명한 로그 패턴을 아래 JSON 스키마로 변환하고,
제공된 기존 패턴들과의 관계를 분석하세요.
반드시 JSON 만 출력하고, 설명 텍스트는 포함하지 마세요.

━━━ 패턴 타입 ━━━

PRESENCE  — 특정 regex 가 한 번이라도 등장하는지 확인
  필수: name, type, keywords[], pattern
  선택: event_dedup_window_sec (float|null)

SEQUENCE  — 지정한 regex 들이 이 순서대로 등장하는지 확인
  필수: name, type, keywords[], steps[] (2개 이상)
  선택: step_dedup (bool), non_overlapping (bool)

WINDOW    — window_sec 안에 pattern 이 count_threshold 회 이상 등장
  필수: name, type, keywords[], pattern, window_sec, count_threshold
  선택: count_unique_only (bool)

ABSENCE   — trigger_pattern 발생 후 window_sec 내 absent_pattern 미등장
  필수: name, type, keywords[], trigger_pattern, absent_pattern, window_sec

COMPOSITE — 다른 패턴들의 AND / OR / NOT 조합
  필수: name, type, keywords[], operator (AND|OR|NOT), components[] (패턴 name)
  규칙: NOT 은 components 1개, AND/OR 은 2개 이상

공통 선택: description (str), weight (float, 기본 1.0)

━━━ 관계 유형 ━━━

references  — 새 패턴이 기존 패턴을 component 로 직접 참조
similar     — 내용이 유사 (중복 주의)
extends     — 기존 패턴보다 더 넓은/복잡한 조건
subset      — 기존 패턴의 일부 조건만 사용
conflicts   — 기존 패턴과 사실상 동일 → 새 패턴 저장 불필요
complement  — 기존 패턴의 보완/반대 조건

━━━ 출력 JSON 스키마 ━━━

{
  "pattern": {
    "name": "string",
    "type": "PRESENCE|SEQUENCE|WINDOW|ABSENCE|COMPOSITE",
    "description": "string",
    "keywords": ["string", ...],
    ... 타입별 필드 ...
  },
  "relations": [
    {
      "type": "references|similar|extends|subset|conflicts|complement",
      "target_name": "기존 패턴의 name",
      "reason": "관계 근거 설명"
    }
  ]
}
"""


def _build_user_prompt(
    text: str,
    context_patterns: list[dict],
    all_names: list[str],
    error_feedback: str = "",
) -> str:
    parts: list[str] = ["/no_think"]

    # 기존 패턴 목록 (COMPOSITE component 선택용)
    parts.append("━━━ 등록된 패턴 이름 목록 (COMPOSITE components 참조 가능) ━━━")
    parts.append(", ".join(f'"{n}"' for n in all_names) if all_names else "(없음)")

    # 관련 패턴 상세 (관계 분석용)
    if context_patterns:
        parts.append("\n━━━ 관련 기존 패턴 상세 ━━━")
        for p in context_patterns:
            # id, created_at 등 불필요한 필드 제거 후 전달
            display = {
                k: v for k, v in p.items()
                if k not in ("id", "created_at", "updated_at")
                and v is not None
                and v is not False
                and v != 0
            }
            parts.append(json.dumps(display, ensure_ascii=False, indent=2))

    # 사용자 입력
    parts.append("\n━━━ 새 패턴 설명 ━━━")
    parts.append(text)

    # 오류 피드백 (재시도 시)
    if error_feedback:
        parts.append("\n━━━ 이전 시도 오류 (수정 후 재출력) ━━━")
        parts.append(error_feedback)

    return "\n".join(parts)


# ── 응답 파싱 + 검증 ──────────────────────────────────────────────────────────

def _parse_response(raw: str, all_names: list[str]) -> GenerationResult:
    """LLM JSON 응답을 파싱하고 pydantic + 참조 무결성을 검증한다."""
    data = json.loads(raw)

    if "pattern" not in data:
        raise ValueError("응답에 'pattern' 키가 없습니다")

    pattern = _parse_pattern(data["pattern"])

    # COMPOSITE 참조 무결성: components 가 all_names 에 있어야 함
    if isinstance(pattern, CompositePattern):
        unknown = [c for c in pattern.components if c not in all_names]
        if unknown:
            raise ValueError(
                f"COMPOSITE components 에 존재하지 않는 패턴이 있습니다: {unknown}\n"
                f"사용 가능한 패턴: {all_names}"
            )

    relations: list[Relation] = []
    for r in data.get("relations", []):
        if not all(k in r for k in ("type", "target_name", "reason")):
            continue  # 필드 누락된 관계는 조용히 무시
        relations.append(
            Relation(
                type=r["type"],
                target_name=r["target_name"],
                reason=r["reason"],
            )
        )

    return GenerationResult(
        pattern=pattern,
        relations=relations,
        context_patterns=[],  # 호출자에서 채움
    )
