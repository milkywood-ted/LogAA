#!/usr/bin/env python3
"""Stage 2 프롬프트 조립 — 공통 질문 Q1~Q7 + 관측/가설 2단계.

설계 문서: `Document/신규 문제 분석 파이프라인/신규 문제 분석 파이프라인 설계.md`
           §4 "Stage 2 LLM 호출 구조", §9 Phase 3

**LLM 을 호출하지 않는다.** 프롬프트 문자열을 만들기만 하므로 LLM·네트워크 없이
전부 검증할 수 있다 — 호출은 `analyze.py` 가 담당한다.

왜 공통 질문이 성립하나
-----------------------
신규 문제는 유형이 사전에 파악되지 않아 "문제 유형별 질문 세트"를 만들 수 없다.
그런데도 공통 질문이 성립하는 이유는 문제들이 비슷해서가 아니라 **참고자료의
모양이 고정돼 있어서**다 — 00~12 번호 슬롯이 각각 답할 수 있는 질문이 정해져
있으므로, 문제가 무엇이든 그 슬롯들에 던질 질문은 같다.

2단계로 나누는 이유
-------------------
LLM 의 최대 실패 모드는 **먼저 결론을 정하고 그에 맞는 근거만 골라 읽는 것**이다.
관측(A군)을 먼저 확정한 뒤 그 고정된 목록에서만 가설(B·C군)을 세우게 하면
calibration 이 개선되고, `ExpertReport` 의 `counter_points`·`confidence` 가
형식적으로 채워지는 것을 막는 장치가 된다.

**주의**: 2단계가 1단계보다 실제로 나은지는 미검증 가정이다(설계 §4). 호출이
2배가 되고 1차 요약에서 정보가 유실될 수 있으므로, 같은 입력으로 나란히 돌려
비교한 뒤 확정해야 한다 — `analyze.py --mode single` 로 1단계 경로를 제공한다.

근거 없는 질문은 던지지 않는다
------------------------------
자료 커버리지가 모듈마다 다르다 — Q3(상태 추정)는 `log_analysis/04_state_model.md`,
Q6(모듈 경계)는 `log_analysis/06_cross_module_edges.md` 가 필요한데 **DP 에는 둘 다
없다**(FRC 에만 있음, 설계 §4 "자료의 모듈별 비대칭"). 근거 없는 질문을 던지면
LLM 이 추측으로 메우므로, **자료가 없으면 질문을 생략하고 그 사실을 리포트에
남긴다**(`ExpertReport.unresolved`).

의존성: Python 3 표준 라이브러리만.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# ── 공통 질문 정의 ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Question:
    """공통 질문 하나. `requires_*` 가 없으면 항상 물을 수 있다."""
    qid: str
    group: str                      # "A"(관측) | "B"(대조) | "C"(행동)
    title: str
    body: str
    requires_module: str = ""       # module_root 기준 상대경로
    requires_chip: str = ""         # chip 디렉토리 내 파일명


QUESTIONS: tuple[Question, ...] = (
    Question(
        "Q1", "A", "정상 흐름 대비 이탈점",
        "관측된 로그의 순서가 `12_event_sequences` 의 정상 경로와 어디서 갈라지는가? "
        "갈라지는 지점을 로그 라인으로 지목하라.",
        requires_chip="12_event_sequences.md",
    ),
    Question(
        "Q2", "A", "부재 증거",
        "정상이라면 나와야 할 로그 중 빠진 것이 있는가? 있다면 (a) 로그 레벨 게이팅으로 "
        "안 찍힌 것인지 (b) 실제로 그 코드가 실행되지 않은 것인지 구분하고, 구분할 수 "
        "없으면 없다고 하라. **로그가 없다는 것이 그 일이 없었다는 뜻은 아니다.**",
    ),
    Question(
        "Q3", "A", "상태 추정",
        "로그 시점에 드라이버가 상태 모델의 어느 상태였는가? 그 상태에서 신고된 증상이 "
        "설명되는가?",
        requires_module="log_analysis/04_state_model.md",
    ),
    Question(
        "Q4", "A", "커버리지 갭",
        "관측 목록에 '미매칭'으로 표시된 라인들은 무엇으로 보이는가 — 타 모듈 로그인지, "
        "자료 스냅샷 이후 추가된 코드인지, 인덱스 누락인지? 판단 불가면 불가라고 하라.",
    ),
    Question(
        "Q5", "B", "알려진 결함 대조",
        "`10_summary_and_findings` 의 결함·검토 포인트 중 이 관측과 부합하는 것이 있는가? "
        "**없으면 '없음'이라고 답하라 — 억지로 맞추지 말 것.** 부합한다면 어느 항목이고 "
        "어떤 관측이 그것을 가리키는지 밝혀라.",
        requires_chip="10_summary_and_findings.md",
    ),
    Question(
        "Q6", "B", "모듈 경계 이탈",
        "증거가 이 모듈 안에서 설명되는가, 아니면 외부 심볼·형제 모듈로 넘어가는가? "
        "넘어간다면 어느 경계인지 지목하라.",
        requires_module="log_analysis/06_cross_module_edges.md",
    ),
    Question(
        "Q7", "C", "다음 관측 제안",
        "`07_debug_interfaces` 기준으로, 어떤 debugfs·procfs·t2d 노드를 켜거나 읽으면 "
        "위 가설을 확증 또는 반증할 수 있는가? **코드 변경보다 기존 observability 를 "
        "먼저 제안하라.** 각 제안에 대해 '무엇이 나오면 가설이 맞고, 무엇이 나오면 "
        "틀리는지'를 함께 밝혀라.",
        requires_chip="07_debug_interfaces.md",
    ),
)


# ── 시스템 지침 ───────────────────────────────────────────────────────────────
#
# `analysis/sdp_frc/README.md` §4 "오류 감소 규칙"(그 프로젝트에서 실제로 낸
# 오류에서 도출된 것)을 그대로 승계한다. 새로 만든 규칙이 아니라 자료가 이미
# 지키라고 명시한 규율이다.

SYSTEM_GUIDE = """당신은 삼성 DTV SoC 커널 드라이버의 로그를 분석한다.

목적은 **확정 진단이 아니라 사람이 디버깅에 쓸 참고자료**를 만드는 것이다.
최종 판단은 사람이 한다. 확신이 없으면 없다고 말하는 것이 틀린 확신보다 낫다.

반드시 지킬 규율:

1. **사실 / 추론 / 가정을 구분한다.** 서술의 각 문장에 (사실)·(추론)·(가정) 을
   붙인다. 로그나 문서에서 직접 읽은 것만 (사실)이다.
2. **모든 주장에 근거를 붙인다.** 로그 라인 원문 또는 `파일:라인` 인용.
   근거 없는 단정을 하지 않는다.
3. **모르면 모른다고 한다.** 주어진 자료로 판단할 수 없으면 그렇게 적는다.
   빈칸을 그럴듯한 추측으로 메우지 않는다.
4. **자료의 '추정'·'TBD'·'Open questions' 위에 확정 결론을 쌓지 않는다.**
   자료가 불확실하다고 표시한 것은 불확실한 채로 다룬다.
5. **칩을 혼동하지 않는다.** 주어진 칩의 자료만 근거로 쓴다. `파일:라인` 은
   칩마다 다르다.
6. **로그가 없다는 것은 그 일이 없었다는 뜻이 아니다.** 로그 레벨 게이팅으로
   찍히지 않았을 수 있다.
7. **위치가 하나로 특정되지 않으면 후보를 모두 제시한다.** 관측 목록에 후보가
   여럿으로 표시된 라인은, 그중 하나를 임의로 골라 확정처럼 쓰지 않는다.
8. **참고자료에 없는 내용을 지어내지 않는다.** 아래 제공된 발췌가 당신이 볼 수
   있는 전부이며, 생략된 부분이 있다면 그 사실이 명시돼 있다."""


# ── 자료에서 전제 추출 ────────────────────────────────────────────────────────

_RE_ASSUMPTION_SECTION = re.compile(r"^##\s*\d*\.?\s*가정 빌드 설정.*$", re.MULTILINE)
_RE_ASSUMPTION_LINE = re.compile(r"^[-*]\s*가정 빌드 설정\s*[:：]\s*(.+)$", re.MULTILINE)


def extract_build_assumption(chip_dir: Path) -> str:
    """참조 문서에서 가정 빌드 설정을 뽑는다.

    `ExpertReport.build_assumption`(설계 §5)의 값이 된다 — 이 전제를 명시하지
    않으면 결론이 다른 칩·빌드에 잘못 도용될 위험이 있어 필수 필드로 뒀다.

    우선순위: `00_review_and_plan.md` 의 "가정 빌드 설정" 절 → 아무 문서의
    "- 가정 빌드 설정: ..." 줄. 못 찾으면 빈 문자열(호출부가 경고한다).
    """
    plan = chip_dir / "00_review_and_plan.md"
    if plan.is_file():
        text = plan.read_bytes().decode("utf-8", errors="replace")
        m = _RE_ASSUMPTION_SECTION.search(text)
        if m:
            rest = text[m.end():]
            # 다음 `##` 헤딩 전까지가 그 절이다.
            nxt = re.search(r"^##\s", rest, re.MULTILINE)
            body = (rest[:nxt.start()] if nxt else rest).strip()
            if body:
                return body

    for name in ("10_summary_and_findings.md", "00_review_and_plan.md"):
        p = chip_dir / name
        if p.is_file():
            m = _RE_ASSUMPTION_LINE.search(
                p.read_bytes().decode("utf-8", errors="replace"))
            if m:
                return m.group(1).strip()
    return ""


def available_questions(
    module_root: Path, chip_dir: Path,
) -> tuple[list[Question], list[tuple[Question, str]]]:
    """근거 자료가 있는 질문과, 자료가 없어 생략된 질문을 나눈다.

    Returns
    -------
    (물을 수 있는 질문, [(생략된 질문, 사유)])
    """
    usable: list[Question] = []
    skipped: list[tuple[Question, str]] = []
    for q in QUESTIONS:
        missing = []
        if q.requires_module and not (module_root / q.requires_module).is_file():
            missing.append(f"{module_root.name}/{q.requires_module}")
        if q.requires_chip and not (chip_dir / q.requires_chip).is_file():
            missing.append(f"{chip_dir.name}/{q.requires_chip}")
        if missing:
            skipped.append((q, f"근거 자료 없음: {', '.join(missing)}"))
        else:
            usable.append(q)
    return usable, skipped


# ── 프롬프트 조립 ─────────────────────────────────────────────────────────────


@dataclass
class PromptContext:
    """프롬프트에 들어갈 재료. 전부 상위 단계가 만들어 넘긴다."""
    profile_name: str
    chip: str
    module_root: str
    build_assumption: str
    problem_text: str
    refined_log: str                      # Stage 1 출력
    excerpt: str                          # Stage 2 § 발췌 결과
    observations: str = ""                # 로그↔코드 매칭 결과(미매칭·복수후보 포함)
    omissions: list[str] = field(default_factory=list)   # 예산 등으로 생략된 것
    skipped_questions: list[tuple[Question, str]] = field(default_factory=list)


def _section(title: str, body: str) -> str:
    return f"━━━ {title} ━━━\n{body.strip() or '(없음)'}\n"


def _common_blocks(ctx: PromptContext) -> str:
    """두 호출이 공유하는 골격 — 문제 유형이 달라도 흔들리지 않는 고정부."""
    premise = (
        f"- 전문가(분석 프로파일): {ctx.profile_name}\n"
        f"- 칩: {ctx.chip}\n"
        f"- 참조 자료: {ctx.module_root}/{ctx.chip}\n"
        f"- 가정 빌드 설정: {ctx.build_assumption or '(자료에서 찾지 못함 — 결론의 적용 범위를 단정하지 말 것)'}\n"
        f"\n이 전제 밖에서는 아래 결론이 유효하지 않다."
    )

    omission_note = ""
    if ctx.omissions:
        omission_note = (
            "\n\n※ 아래는 생략된 부분이다. **안 본 것을 없는 것으로 해석하지 말 것:**\n"
            + "\n".join(f"  - {o}" for o in ctx.omissions)
        )

    return (
        _section("시스템 지침", SYSTEM_GUIDE) + "\n"
        + _section("전제", premise) + "\n"
        + _section("참고자료 (분석 문서 발췌)", ctx.excerpt + omission_note) + "\n"
        + _section("관측 — 로그↔코드 매칭", ctx.observations) + "\n"
        + _section("관측 — 정제된 로그", ctx.refined_log) + "\n"
        + _section("문제 상황 (사용자 입력)", ctx.problem_text) + "\n"
    )


def _questions_block(qs: list[Question], group_label: str) -> str:
    lines = [f"아래 {group_label} 질문에 각각 답하라. 순서를 지키고, 질문 번호를 밝혀라.\n"]
    for q in qs:
        lines.append(f"[{q.qid}] {q.title}\n{q.body}\n")
    return "\n".join(lines)


def _skipped_note(ctx: PromptContext, qs: list[Question]) -> str:
    """생략된 질문을 프롬프트에 알린다 — LLM 이 대신 추측하지 않도록."""
    relevant = [(q, why) for q, why in ctx.skipped_questions if q in qs]
    if not relevant:
        return ""
    body = "\n".join(f"  - [{q.qid}] {q.title} — {why}" for q, why in relevant)
    return (
        "\n※ 아래 질문은 **근거 자료가 없어 생략**했다. 추측으로 답하지 말고, "
        "이 사실을 그대로 한계로 보고하라:\n" + body + "\n"
    )


def build_observation_prompt(ctx: PromptContext) -> str:
    """1차 — 관측(A군). 결론을 내리지 말고 사실만 정리하게 한다."""
    qs = [q for q in QUESTIONS if q.group == "A"
          and q not in [s[0] for s in ctx.skipped_questions]]
    task = (
        "지금은 **관측 정리 단계다. 원인 가설을 세우지 말라.**\n"
        "무엇이 보이고 무엇이 안 보이는지만 정리한다. 원인 추정은 다음 단계에서 한다.\n"
        "가설을 미리 정하고 근거를 맞춰가는 것이 이 작업의 가장 흔한 실패다.\n\n"
        + _questions_block(qs, "관측")
        + _skipped_note(ctx, [q for q in QUESTIONS if q.group == "A"])
    )
    return _common_blocks(ctx) + "\n" + _section("과업", task)


def build_hypothesis_prompt(ctx: PromptContext, observations_result: str) -> str:
    """2차 — 가설·행동(B·C군). 1차에서 확정된 관측만 근거로 쓴다."""
    qs = [q for q in QUESTIONS if q.group in ("B", "C")
          and q not in [s[0] for s in ctx.skipped_questions]]
    task = (
        "1차에서 정리된 관측을 근거로 가설을 세운다.\n"
        "**1차 관측에 없는 사실을 새로 만들어내지 말라.** 관측이 부족해 답할 수 없으면 "
        "그렇게 적는다.\n"
        "가설은 하나로 좁히지 않아도 된다 — 근거가 있는 만큼 복수로 제시하라.\n\n"
        + _questions_block(qs, "대조·행동")
        + _skipped_note(ctx, [q for q in QUESTIONS if q.group in ("B", "C")])
        + "\n" + OUTPUT_SPEC
    )
    return (
        _common_blocks(ctx) + "\n"
        + _section("1차 관측 결과 (확정된 사실 — 이것만 근거로 쓸 것)", observations_result)
        + "\n" + _section("과업", task)
    )


def build_single_prompt(ctx: PromptContext) -> str:
    """1단계 경로 — Q1~Q7 을 한 번에 묻는다.

    2단계가 정말 나은지 비교하기 위한 대조군이다(설계 §4 미검증 가정).
    """
    qs = [q for q in QUESTIONS if q not in [s[0] for s in ctx.skipped_questions]]
    task = (
        "아래 질문에 순서대로 답한 뒤, 지정된 형식으로 결과를 내라.\n"
        "관측(Q1~Q4)을 먼저 정리하고 그 관측만 근거로 가설(Q5~Q7)을 세워라 — "
        "가설을 먼저 정하고 근거를 맞춰가지 말 것.\n\n"
        + _questions_block(qs, "전체")
        + _skipped_note(ctx, list(QUESTIONS))
        + "\n" + OUTPUT_SPEC
    )
    return _common_blocks(ctx) + "\n" + _section("과업", task)


# ── 출력 형식 (ExpertReport, 설계 §5) ─────────────────────────────────────────

OUTPUT_SPEC = """━━━ 출력 형식 ━━━
아래 JSON 객체 **하나만** 출력한다. 설명·마크다운 코드펜스를 덧붙이지 않는다.

{
  "hypotheses": [
    {
      "summary": "한 줄 요약",
      "confidence": "높음 | 중간 | 낮음",
      "narrative": "서술형 근거. 각 문장에 (사실)/(추론)/(가정) 을 붙인다.",
      "evidence": [
        {"type": "log_line",     "value": "로그 라인 원문"},
        {"type": "doc_citation", "value": "파일:라인",
         "candidates": ["후보가 여럿이면 전부 나열, 하나면 생략 가능"]}
      ],
      "counter_points": ["이 가설에 반하는 정황. 없으면 그 이유를 적는다"]
    }
  ],
  "unresolved": ["이 분석으로 확인하지 못한 것"],
  "warnings": ["결과 해석 시 감안할 한계"],
  "next_observations": [
    {"what": "켜거나 읽을 노드·명령",
     "confirms": "무엇이 나오면 가설이 맞는가",
     "refutes":  "무엇이 나오면 가설이 틀리는가"}
  ]
}

주의:
- `hypotheses` 는 복수 가능하다. 근거가 있는 만큼 제시하고, 없으면 빈 배열로 둔다.
- `counter_points` 를 형식적으로 채우지 말라. 스스로 반박을 찾지 못했다면
  그 사실 자체가 과신의 신호다.
- `evidence.candidates` 는 코드 위치가 하나로 특정되지 않을 때 후보를 모두 담는다."""
