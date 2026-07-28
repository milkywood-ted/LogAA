#!/usr/bin/env python3
"""`ExpertReport` — Stage 3 산출물 스키마의 파싱·검증·렌더링.

설계 문서: `Document/신규 문제 분석 파이프라인/신규 문제 분석 파이프라인 설계.md` §5

**LLM 을 호출하지 않는다.** 응답 문자열을 받아 구조화하고 품질 신호를 뽑을 뿐이라
LLM 없이 전부 검증할 수 있다.

무엇을 검증하나
---------------
두 층으로 나눈다:

- **구조 오류(errors)** — 필수 필드 누락, 타입 불일치. 리포트로 쓸 수 없다.
- **품질 신호(quality_flags)** — 형식은 맞지만 설계 의도가 지켜지지 않은 것.
  자동으로 고칠 수 없고 사람이 판단할 몫이라 **경고로 드러내고 버리지 않는다.**

품질 신호를 두는 이유: 이 파이프라인은 "확정 진단이 아니라 참고자료" 를 만들며,
그 정직성이 `counter_points`·`confidence`·(사실)/(추론)/(가정) 구분에 실려 있다.
LLM 이 이것들을 형식적으로 채우면 겉보기엔 멀쩡한데 실질이 빈다 — 그래서 빈
`counter_points` 나 태그 없는 서술을 **과신 신호로 보고** 표면에 올린다.

의존성: Python 3 표준 라이브러리만.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

CONFIDENCE_VALUES = ("높음", "중간", "낮음")

# 서술의 사실/추론/가정 태그. 자료 README §4 규율을 승계한 것이라 하나라도
# 없으면 규율이 지켜지지 않은 것으로 본다.
_RE_EPISTEMIC_TAG = re.compile(r"\((사실|추론|가정)\)")

# LLM 이 지시를 어기고 코드펜스로 감싸는 경우가 흔하다 — 관용적으로 벗겨낸다.
_RE_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


@dataclass
class Evidence:
    type: str                                  # "log_line" | "doc_citation"
    value: str
    candidates: list[str] = field(default_factory=list)
    """코드 위치가 하나로 특정되지 않을 때의 후보 전체(설계 §5).
    실측에서 매칭 라인의 31.2% 가 복수 위치를 가리켰다 — 단일 값만 두면 LLM 이
    셋 중 한 번은 임의로 하나를 골라 확정처럼 제시하게 된다."""


@dataclass
class Hypothesis:
    summary: str
    confidence: str
    narrative: str
    evidence: list[Evidence] = field(default_factory=list)
    counter_points: list[str] = field(default_factory=list)


@dataclass
class NextObservation:
    what: str
    confirms: str = ""
    refutes: str = ""


@dataclass
class ExpertReport:
    """전문가 1명의 산출물. 여러 개를 승자 선정 없이 나열해 제시한다(설계 §3)."""

    # 메타 — 전문가 식별 + 전제 조건
    profile_name: str = ""
    chip: str = ""
    module_root: str = ""
    build_assumption: str = ""

    # 본문
    hypotheses: list[Hypothesis] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    next_observations: list[NextObservation] = field(default_factory=list)

    # 파이프라인이 채우는 것 (LLM 응답이 아님)
    errors: list[str] = field(default_factory=list)
    quality_flags: list[str] = field(default_factory=list)
    raw_response: str = ""

    @property
    def usable(self) -> bool:
        """구조 오류가 없으면 리포트로 쓸 수 있다. 품질 신호는 사용을 막지 않는다."""
        return not self.errors


def _as_list(v: Any) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _as_str(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ("" if v is None else str(v))


def parse_response(text: str) -> tuple[dict | None, str | None]:
    """LLM 응답에서 JSON 객체를 뽑는다. 반환: (파싱 결과, 오류 메시지).

    코드펜스로 감싸거나 앞뒤에 설명을 붙이는 경우가 흔해 관용적으로 처리한다 —
    지시를 어긴 응답을 버리기보다 건질 수 있으면 건지는 쪽이 실용적이다.
    """
    if not text or not text.strip():
        return None, "빈 응답"

    body = text.strip()
    m = _RE_FENCE.match(body)
    if m:
        body = m.group(1).strip()

    try:
        return json.loads(body), None
    except json.JSONDecodeError:
        pass

    # 앞뒤 설명이 붙은 경우 — 최외곽 중괄호 구간만 잘라 재시도.
    start, end = body.find("{"), body.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(body[start:end + 1]), None
        except json.JSONDecodeError as e:
            return None, f"JSON 파싱 실패: {e}"
    return None, "응답에서 JSON 객체를 찾지 못했다"


def build_report(
    data: dict,
    *,
    profile_name: str = "",
    chip: str = "",
    module_root: str = "",
    build_assumption: str = "",
    raw_response: str = "",
) -> ExpertReport:
    """파싱된 dict 를 `ExpertReport` 로 만들고 구조·품질을 검사한다."""
    rep = ExpertReport(
        profile_name=profile_name, chip=chip, module_root=module_root,
        build_assumption=build_assumption, raw_response=raw_response,
    )

    if not build_assumption:
        # 설계 §5 에서 필수 필드로 둔 이유 — 전제를 명시하지 않으면 결론이 다른
        # 칩·빌드에 잘못 도용될 위험이 있다.
        rep.quality_flags.append(
            "build_assumption 이 비었다 — 참조 문서에서 가정 빌드 설정을 찾지 못했다. "
            "이 리포트의 적용 범위를 단정하지 말 것"
        )

    raw_hyps = _as_list(data.get("hypotheses"))
    if not isinstance(data.get("hypotheses"), list):
        rep.errors.append("`hypotheses` 가 배열이 아니다")

    for i, h in enumerate(raw_hyps):
        if not isinstance(h, dict):
            rep.errors.append(f"hypotheses[{i}] 가 객체가 아니다")
            continue

        summary = _as_str(h.get("summary"))
        narrative = _as_str(h.get("narrative"))
        confidence = _as_str(h.get("confidence"))

        if not summary:
            rep.errors.append(f"hypotheses[{i}].summary 가 비었다")
        if not narrative:
            rep.errors.append(f"hypotheses[{i}].narrative 가 비었다")
        if confidence not in CONFIDENCE_VALUES:
            rep.quality_flags.append(
                f"hypotheses[{i}].confidence 가 '{confidence}' — "
                f"{'/'.join(CONFIDENCE_VALUES)} 중 하나여야 한다"
            )

        evs: list[Evidence] = []
        for e in _as_list(h.get("evidence")):
            if isinstance(e, dict):
                evs.append(Evidence(
                    type=_as_str(e.get("type")) or "unknown",
                    value=_as_str(e.get("value")),
                    candidates=[_as_str(c) for c in _as_list(e.get("candidates")) if _as_str(c)],
                ))
            elif _as_str(e):
                # 문자열만 온 경우 — 형식은 어겼지만 내용은 살린다.
                evs.append(Evidence(type="unknown", value=_as_str(e)))

        cps = [_as_str(c) for c in _as_list(h.get("counter_points")) if _as_str(c)]

        # ── 품질 신호 ────────────────────────────────────────────────────────
        if not evs:
            rep.quality_flags.append(
                f"hypotheses[{i}] 에 근거(evidence)가 없다 — 근거 없는 주장은 "
                f"이 파이프라인의 산출물로 쓸 수 없다"
            )
        if not cps:
            rep.quality_flags.append(
                f"hypotheses[{i}] 의 counter_points 가 비었다 — 스스로 반박을 찾지 "
                f"못했다면 그 자체가 과신 신호다(설계 §5)"
            )
        if narrative and not _RE_EPISTEMIC_TAG.search(narrative):
            rep.quality_flags.append(
                f"hypotheses[{i}].narrative 에 (사실)/(추론)/(가정) 구분이 없다"
            )

        rep.hypotheses.append(Hypothesis(
            summary=summary, confidence=confidence, narrative=narrative,
            evidence=evs, counter_points=cps,
        ))

    rep.unresolved = [_as_str(x) for x in _as_list(data.get("unresolved")) if _as_str(x)]
    rep.warnings = [_as_str(x) for x in _as_list(data.get("warnings")) if _as_str(x)]

    for n in _as_list(data.get("next_observations")):
        if isinstance(n, dict):
            what = _as_str(n.get("what"))
            if what:
                rep.next_observations.append(NextObservation(
                    what=what,
                    confirms=_as_str(n.get("confirms")),
                    refutes=_as_str(n.get("refutes")),
                ))
        elif _as_str(n):
            rep.next_observations.append(NextObservation(what=_as_str(n)))

    if rep.hypotheses and not rep.next_observations:
        # Q7 이 이 파이프라인의 목표("분석에 도움이 되는 자료")에 가장 직접적으로
        # 답하는 항목이라 비면 눈에 띄게 한다.
        rep.quality_flags.append(
            "next_observations 가 비었다 — 가설이 있는데 확인 방법이 없으면 "
            "사용자가 다음에 무엇을 할지 알 수 없다(Q7)"
        )

    return rep


def render_markdown(rep: ExpertReport) -> str:
    """사용자 제시용 마크다운. 여러 전문가의 리포트를 나열할 때 한 조각이 된다."""
    L: list[str] = []
    add = L.append

    add(f"## {rep.profile_name or '(전문가 미상)'} — {rep.chip or '(칩 미상)'}")
    add("")
    add(f"- 참조 자료: `{rep.module_root}/{rep.chip}`")
    if rep.build_assumption:
        first = rep.build_assumption.splitlines()[0].strip()
        add(f"- 가정 빌드 설정: {first}")
        add("")
        add("<details><summary>빌드 설정 전문</summary>")
        add("")
        add(rep.build_assumption)
        add("")
        add("</details>")
    add("")

    if rep.errors:
        add("> **구조 오류로 이 리포트를 신뢰할 수 없다:**")
        for e in rep.errors:
            add(f"> - {e}")
        add("")

    if not rep.hypotheses:
        add("가설 없음 — 주어진 자료로 원인 가설을 세우지 못했다.")
        add("")
    for i, h in enumerate(rep.hypotheses, 1):
        add(f"### 가설 {i}. {h.summary}")
        add("")
        add(f"**확신도: {h.confidence or '(미표기)'}**")
        add("")
        add(h.narrative)
        add("")
        if h.evidence:
            add("**근거**")
            add("")
            for e in h.evidence:
                if e.candidates and len(e.candidates) > 1:
                    add(f"- ({e.type}) {e.value} "
                        f"— **위치 후보 {len(e.candidates)}개**: {', '.join(f'`{c}`' for c in e.candidates)}")
                else:
                    add(f"- ({e.type}) {e.value}")
            add("")
        if h.counter_points:
            add("**반증 정황**")
            add("")
            for c in h.counter_points:
                add(f"- {c}")
            add("")

    if rep.next_observations:
        add("### 다음에 확인할 것")
        add("")
        for n in rep.next_observations:
            add(f"- **{n.what}**")
            if n.confirms:
                add(f"  - 맞다면: {n.confirms}")
            if n.refutes:
                add(f"  - 틀리다면: {n.refutes}")
        add("")

    if rep.unresolved:
        add("### 확인하지 못한 것")
        add("")
        for u in rep.unresolved:
            add(f"- {u}")
        add("")

    if rep.warnings or rep.quality_flags:
        add("### 해석 시 감안할 것")
        add("")
        for w in rep.warnings:
            add(f"- {w}")
        for q in rep.quality_flags:
            add(f"- ⚠️ (품질 신호) {q}")
        add("")

    return "\n".join(L)
