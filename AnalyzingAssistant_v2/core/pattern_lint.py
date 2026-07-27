"""
core/pattern_lint.py

패턴 문자열의 정규식 오작성 검출 + 이스케이프 헬퍼.

배경
----
패턴 필드(pattern / steps / trigger_pattern / absent_pattern)는 Stage 4 에서
정규식으로 해석된다. 그러나 실제 작성은 로그 원문을 그대로 붙여넣는 방식이
대부분이라, 메타문자가 섞이면 의도와 다르게 매칭된다.
Python 3.11 부터는 `C++` 같은 표현이 possessive quantifier 로 정상 컴파일되어
예외조차 발생하지 않으므로, 입력 시점 검출이 유일한 방어선이다.

탐지 원리
--------
"메타문자가 있는지" 가 아니라 "정규식으로서 무의미하거나 자기모순인 구조인지"
를 본다. 의도한 정규식은 항상 의미가 있고, 리터럴 오작성은 무의미한 구조를
만든다 — 이 비대칭이 오경보를 억제한다.
(`ata.*exception|ata.*error` 같은 정상 패턴은 경고하지 않는다.)

공개 API
--------
lint(pattern)                    → list[LintIssue]
lint_pattern_fields(data)        → list[FieldIssue]
escape_at(pattern, positions)    → str
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator, Mapping

# 정규식으로 해석되는 패턴 필드 — 이 목록이 린트 대상이다.
SCALAR_FIELDS: tuple[str, ...] = ("pattern", "trigger_pattern", "absent_pattern")
LIST_FIELDS: tuple[str, ...] = ("steps",)

ERROR = "error"
WARNING = "warning"

_QUANTIFIERS = frozenset("*+?")


# ── 결과 타입 ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LintIssue:
    """패턴 문자열 1건에서 발견된 문제."""
    severity: str      # ERROR | WARNING
    rule: str          # 규칙 식별자 (프론트엔드 분기용)
    message: str       # 사용자에게 보여줄 설명
    start: int         # 문제 구간 시작 인덱스 (원본 문자열 기준)
    end: int           # 문제 구간 끝 인덱스 (배타적)

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "rule":     self.rule,
            "message":  self.message,
            "start":    self.start,
            "end":      self.end,
        }


@dataclass(frozen=True)
class FieldIssue:
    """패턴 dict 안에서 어느 필드의 문제인지까지 특정한 결과."""
    field: str         # "pattern" / "steps[0]" / "trigger_pattern" ...
    value: str         # 문제가 발견된 원본 문자열
    issue: LintIssue

    def to_dict(self) -> dict:
        return {"field": self.field, "value": self.value, **self.issue.to_dict()}


# ── 토큰 스캐너 ───────────────────────────────────────────────────────────────
#
# 정규식을 정규식으로 검사하면 이스케이프·문자클래스 중첩에서 쉽게 틀린다.
# 한 번 훑으면서 각 문자의 (이스케이프 여부, 문자클래스 내부 여부) 를 확정한다.

@dataclass(frozen=True)
class _Tok:
    pos: int           # 원본 문자열에서의 인덱스 (이스케이프면 백슬래시 위치)
    char: str          # 이스케이프면 백슬래시 뒤 문자
    in_class: bool     # [...] 내부인지
    escaped: bool      # 백슬래시로 이스케이프되었는지

    @property
    def is_meta(self) -> bool:
        """정규식 문법으로 작동하는 문자인지 (이스케이프·클래스 내부는 제외)."""
        return not self.escaped and not self.in_class


def _scan(pattern: str) -> list[_Tok]:
    """패턴을 토큰 목록으로 변환한다. `[` 와 `]` 자체는 in_class=False 로 둔다."""
    toks: list[_Tok] = []
    i = 0
    n = len(pattern)
    in_class = False

    while i < n:
        ch = pattern[i]

        if ch == "\\" and i + 1 < n:
            toks.append(_Tok(i, pattern[i + 1], in_class, True))
            i += 2
            continue

        if not in_class and ch == "[":
            in_class = True
            toks.append(_Tok(i, ch, False, False))
        elif in_class and ch == "]":
            in_class = False
            toks.append(_Tok(i, ch, False, False))
        else:
            toks.append(_Tok(i, ch, in_class, False))

        i += 1

    return toks


def _class_spans(toks: list[_Tok]) -> list[tuple[int, int]]:
    """문자 클래스 [...] 의 (여는 `[` 토큰 인덱스, 닫는 `]` 토큰 인덱스) 목록."""
    spans: list[tuple[int, int]] = []
    open_at: int | None = None
    for k, t in enumerate(toks):
        if t.escaped:
            continue
        if t.char == "[" and not t.in_class and open_at is None:
            open_at = k
        elif t.char == "]" and open_at is not None:
            spans.append((open_at, k))
            open_at = None
    return spans


def _group_spans(toks: list[_Tok]) -> list[tuple[int, int]]:
    """그룹 (...) 의 (여는 토큰 인덱스, 닫는 토큰 인덱스) 목록. 중첩 지원."""
    spans: list[tuple[int, int]] = []
    stack: list[int] = []
    for k, t in enumerate(toks):
        if not t.is_meta:
            continue
        if t.char == "(":
            stack.append(k)
        elif t.char == ")" and stack:
            spans.append((stack.pop(), k))
    return spans


def _next_char(toks: list[_Tok], k: int) -> str | None:
    """k 번째 토큰 바로 뒤의 문법상 유효한 문자. 없으면 None."""
    if k + 1 >= len(toks):
        return None
    nxt = toks[k + 1]
    return None if nxt.escaped else nxt.char


# ── 규칙 ──────────────────────────────────────────────────────────────────────

def _rule_possessive(toks: list[_Tok]) -> Iterator[LintIssue]:
    """`++` `*+` `?+` — Python 3.11+ 의 possessive quantifier.

    손으로 작성할 일이 사실상 없으므로 `C++` 같은 리터럴 오작성으로 간주한다.
    (`.*?` `+?` 등 lazy quantifier 는 정상이므로 대상이 아니다.)
    """
    for k, t in enumerate(toks):
        if not t.is_meta or t.char != "+" or k == 0:
            continue
        prev = toks[k - 1]
        if prev.is_meta and prev.char in _QUANTIFIERS:
            yield LintIssue(
                WARNING,
                "possessive_quantifier",
                f"'{prev.char}+' 는 possessive quantifier 로 해석되어 앞 문자의 반복을 뜻합니다. "
                "리터럴 '+' 를 의도했다면 이스케이프가 필요합니다.",
                prev.pos,
                t.pos + 1,
            )


def _rule_literal_quantifier(toks: list[_Tok]) -> Iterator[LintIssue]:
    """리터럴 1문자에 붙은 `*` / `+` — `func+0x0` 같은 오작성.

    사람이 정규식을 쓸 때는 `.*` 를 쓰지 `c+` 를 쓰지 않는다.
    `?` 는 `errors?` 처럼 정당한 용법이 흔하므로 대상에서 제외한다.
    """
    for k, t in enumerate(toks):
        if not t.is_meta or t.char not in ("*", "+") or k == 0:
            continue
        if _next_char(toks, k) == "+":
            continue                           # `C++` — possessive 규칙이 더 정확히 설명한다
        prev = toks[k - 1]
        if prev.escaped:                       # \d+ \w* — 정상
            continue
        if not prev.is_meta:                   # 클래스 내부 문자 — 대상 아님
            continue
        if prev.char in ".)]}" or prev.char in _QUANTIFIERS:
            continue                           # .* [0-9]+ (ab)+ a{2,}+ — 정상
        yield LintIssue(
            WARNING,
            "literal_quantifier",
            f"'{prev.char}{t.char}' 는 '{prev.char}' 의 반복으로 해석됩니다. "
            f"리터럴 '{t.char}' 를 의도했다면 이스케이프가 필요합니다.",
            prev.pos,
            t.pos + 1,
        )


def _rule_noop_group(toks: list[_Tok], pattern: str) -> Iterator[LintIssue]:
    """대안(`|`) 도 수량자도 없는 그룹 `(...)` — 정규식으로 아무 의미가 없다.

    `mmc0: error (retry)` 처럼 로그 원문의 괄호를 그대로 옮긴 경우다.
    """
    for open_k, close_k in _group_spans(toks):
        inner = toks[open_k + 1 : close_k]
        if inner and inner[0].is_meta and inner[0].char == "?":
            continue                           # (?:...) (?=...) — 특수 그룹
        if any(t.is_meta and t.char == "|" for t in inner):
            continue                           # 대안이 있으면 의미 있음
        if _next_char(toks, close_k) in ("*", "+", "?", "{"):
            continue                           # 수량자가 붙으면 의미 있음
        start, end = toks[open_k].pos, toks[close_k].pos + 1
        yield LintIssue(
            WARNING,
            "noop_group",
            f"'{pattern[start:end]}' 는 대안도 수량자도 없어 정규식으로는 의미가 없습니다. "
            "로그 원문의 괄호라면 이스케이프가 필요합니다.",
            start,
            end,
        )


def _rule_taglike_class(toks: list[_Tok], pattern: str) -> Iterator[LintIssue]:
    """범위(`a-z`) 도 부정(`^`) 도 없는 문자 클래스 `[...]` — 로그 태그로 보인다.

    `[drm] init failed` 는 "d, r, m 중 한 글자" 로 해석되어 오탐을 만든다.
    """
    for open_k, close_k in _class_spans(toks):
        inner = toks[open_k + 1 : close_k]
        if len(inner) < 2:
            continue                           # [a] 등 — 오작성으로 보기 어렵다
        if inner[0].char == "^" and not inner[0].escaped:
            continue                           # 부정 클래스 — 의도적
        # 양 끝이 아닌 위치의 `-` 는 범위 지정 → 의도적인 문자 클래스
        if any(
            t.char == "-" and not t.escaped and 0 < idx < len(inner) - 1
            for idx, t in enumerate(inner)
        ):
            continue
        start, end = toks[open_k].pos, toks[close_k].pos + 1
        yield LintIssue(
            WARNING,
            "taglike_class",
            f"'{pattern[start:end]}' 는 괄호 안의 문자 중 한 글자와 매칭됩니다. "
            "로그 태그를 의도했다면 이스케이프가 필요합니다.",
            start,
            end,
        )


def _rule_bare_dot(toks: list[_Tok]) -> Iterator[LintIssue]:
    """수량자가 붙지 않은 단독 `.` — 임의의 1문자와 매칭된다.

    `.*` `.+` 는 명백한 정규식 의도이므로 제외한다.
    """
    for k, t in enumerate(toks):
        if not t.is_meta or t.char != ".":
            continue
        if _next_char(toks, k) in ("*", "+", "?", "{"):
            continue
        yield LintIssue(
            WARNING,
            "bare_dot",
            "'.' 은 임의의 1문자와 매칭됩니다. "
            "로그 원문의 마침표라면 이스케이프가 필요합니다.",
            t.pos,
            t.pos + 1,
        )


# ── 공개 API ──────────────────────────────────────────────────────────────────

def lint(pattern: str) -> list[LintIssue]:
    """
    패턴 문자열 1건을 검사해 문제 목록을 위치 순으로 반환한다.

    컴파일에 실패하면 ERROR 1건만 반환한다 — 구조를 신뢰할 수 없어
    나머지 규칙의 결과가 무의미하기 때문이다.
    """
    if not pattern:
        return []

    try:
        re.compile(pattern)
    except re.error as e:
        pos = e.pos if e.pos is not None else 0
        return [LintIssue(
            ERROR,
            "compile_failed",
            f"정규식으로 해석할 수 없습니다: {e.msg}",
            pos,
            min(pos + 1, len(pattern)),
        )]

    toks = _scan(pattern)
    issues = [
        *_rule_possessive(toks),
        *_rule_literal_quantifier(toks),
        *_rule_noop_group(toks, pattern),
        *_rule_taglike_class(toks, pattern),
        *_rule_bare_dot(toks),
    ]
    return sorted(issues, key=lambda i: (i.start, i.rule))


def iter_pattern_fields(data: Mapping) -> Iterator[tuple[str, str]]:
    """
    패턴 dict 에서 정규식으로 해석되는 필드를 (필드명, 값) 으로 순회한다.

    타입(PRESENCE/SEQUENCE/...)을 보지 않고 존재하는 필드만 훑으므로,
    부분 dict 나 API 요청 모델에도 그대로 쓸 수 있다.
    """
    for name in SCALAR_FIELDS:
        value = data.get(name)
        if isinstance(value, str) and value:
            yield name, value

    for name in LIST_FIELDS:
        for i, value in enumerate(data.get(name) or []):
            if isinstance(value, str) and value:
                yield f"{name}[{i}]", value


def lint_pattern_fields(data: Mapping) -> list[FieldIssue]:
    """패턴 dict 전체를 검사한다. 반환 순서는 iter_pattern_fields 순서를 따른다."""
    return [
        FieldIssue(field=name, value=value, issue=issue)
        for name, value in iter_pattern_fields(data)
        for issue in lint(value)
    ]


def has_error(issues: list[FieldIssue]) -> bool:
    return any(i.issue.severity == ERROR for i in issues)


def escape_at(pattern: str, positions: list[int]) -> str:
    """
    지정한 인덱스의 문자 앞에 백슬래시를 넣는다.

    일부만 리터럴로 바꾸고 나머지는 정규식으로 유지하는 혼합 케이스를 위한 것이다.
    범위를 벗어난 인덱스는 무시한다.

        escape_at("[drm] ata.*", [0, 4]) == r"\\[drm\\] ata.*"
    """
    targets = {p for p in positions if 0 <= p < len(pattern)}
    return "".join(
        ("\\" + ch) if i in targets else ch
        for i, ch in enumerate(pattern)
    )


def format_issues(issues: list[FieldIssue]) -> str:
    """로그·예외 메시지용 한 줄 요약 목록."""
    return "\n".join(
        f"  - {i.field}={i.value!r} [{i.issue.rule}] {i.issue.message}"
        for i in issues
    )
