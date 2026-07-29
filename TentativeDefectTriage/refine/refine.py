#!/usr/bin/env python3
"""Stage 1 — 전문가별 로그 정제.

설계 문서: `Document/신규 문제 분석 파이프라인/신규 문제 분석 파이프라인 설계.md`
           §4 "Stage 1 — 전문가별 로그 정제", §9 Phase 1

무엇을 하나
-----------
raw 로그를 받아 **한 전문가(분석 프로파일)의 관점으로** 정제한다. 전문가마다
`prefilter_keywords` 가 다르므로 같은 로그도 전문가별로 다르게 정제된다 — 이것이
기존 `AnalyzingAssistant_v2/core/log_refiner.py`(케이스 매칭 전제의 공통 1회
정제)를 재사용하지 않고 새로 구현한 이유다.

    포맷 파싱 → 반복/버스트 collapse → 키워드 필터 → 예산 강제

앵커 기반 시간 윈도우는 부가 옵션으로 분류돼 여기 없다(설계 §4).

핵심 설계 판단
--------------
**1) collapse 지문은 16진값만 마스킹하고 정수는 건드리지 않는다.**
드라이버 로그는 정수에 의미를 담는다 — `idx is invalid %d %d`,
`CUR OP MODE = %x (%d)`, `FRC CLK DIVIDE OPT : %d` (rheam 인덱스 실측). 정수를
마스킹하면 서로 다른 idx·모드·옵션이 한 줄로 합쳐져 **디버깅 대상 그 자체가
사라진다**. 반면 `0x%x` 주소·`regaddr:0x%px` 는 호출마다 달라지는 게 정상이라
마스킹해도 안전하다. §9 Phase 1 체크리스트 "collapse 가 서로 다른 이벤트를 잘못
합치지 않는가" 에 대한 답이다.

**2) 예산 초과 시 가운데를 버린다.**
머리(부팅·초기화 맥락)와 꼬리(실패 시점)는 둘 다 정보가 많고, 가운데는 정상
반복인 경우가 많다(그리고 burst collapse 가 이미 반복을 줄였다). 어느 쪽이
중요한지는 문제에 따라 다르므로 한쪽만 남기는 선택은 위험하다. **버린 사실을
결과에 명시**해 다운스트림이 "안 본 것"을 "없는 것"으로 오해하지 않게 한다.

**3) 결정론적이다.** 같은 입력·같은 프로파일이면 같은 출력이 나온다(§9 Phase 1
체크리스트). 난수·시각·해시순회 순서에 의존하지 않는다.

의존성: Python 3 표준 라이브러리만.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

# ── 파싱 ──────────────────────────────────────────────────────────────────────

# 커널/드라이버 로그의 선두 타임스탬프 대괄호.
#   dmesg          [  1234.567890] msg
#   dmesg(cpu id)  [0-  1234.567890] msg
#   inlog          [1234.567890][S_F] msg   (CAPTURE_SPEC — 라인 포맷은 dmesg 와 동일)
_RE_TS = re.compile(r"^\s*\[\s*(?:(\d+)-)?\s*(\d+\.\d+)\s*\]\s*(.*)$")

# "last message repeated N times" — syslog 계열이 삽입하는 축약 마커.
_RE_REPEAT = re.compile(r"last message repeated (\d+) times", re.IGNORECASE)

# 버스트 지문에서 마스킹할 값.
#
# **16진값만 마스킹한다.** 드라이버 로그는 정수에 의미를 담으므로(모듈 docstring
# 참고) 정수를 지우면 서로 다른 이벤트가 합쳐진다. 주소·레지스터값은 호출마다
# 달라지는 게 정상이라 지워도 이벤트 동일성이 유지된다.
_RE_VOLATILE = re.compile(
    r"0x[0-9a-fA-F]+"          # 0x 접두 16진 (주소·마스크·레지스터값)
    r"|\b[0-9a-fA-F]{8,}\b",   # 접두 없는 긴 16진 런
)


@dataclass
class LogLine:
    """정제 파이프라인이 다루는 로그 한 줄."""
    raw: str
    ts: float | None            # 커널 uptime(초). 없으면 None
    message: str                # 타임스탬프 제거 후 본문
    source_file: str = ""
    cpu: int | None = None
    count: int = 1              # collapse 로 합쳐진 개수 (>1 이면 반복)

    @property
    def fingerprint(self) -> str:
        """버스트 판정용 지문 — 휘발성 값만 마스킹한 본문."""
        return _RE_VOLATILE.sub("<V>", self.message).strip()

    def render(self) -> str:
        ts = f"[{self.ts:>13.6f}]" if self.ts is not None else "[   no_time   ]"
        suffix = f"  ×{self.count}" if self.count > 1 else ""
        return f"{ts} {self.message}{suffix}"


@dataclass
class RefineConfig:
    """Stage 1 설정. 전문가(프로파일)마다 `keywords` 가 다르다."""

    keywords: list[str] = field(default_factory=list)
    """프로파일의 `prefilter_keywords`. OR 매칭, 대소문자 무시. 비면 필터 미적용."""

    burst_window_sec: float = 1.0
    """이 시간 안에 같은 지문이 반복되면 버스트로 본다."""

    burst_threshold: int = 5
    """버스트로 판정할 최소 반복 횟수. 0 이하면 버스트 collapse 미적용."""

    budget_tokens: int = 28_000
    """출력 상한(추정 토큰). 설계 §4 "출력 크기 목표".
    개념 계층(용어집·구조·상관키)이 주입되면서 50k→40k 로 낮췄다 — 로그는 반복이
    많아 줄여도 손실이 적은 반면, 개념이 없으면 로그를 해석할 수 없다. 0 이하면 미적용."""

    chars_per_token: float = 1.5
    """자→토큰 환산비. **보수적으로 낮게 잡는다**(같은 자수를 더 많은 토큰으로
    셈) — 예산을 넘기지 않는 쪽으로 틀리는 게 안전하기 때문이다. 정확한 값은
    실제 모델 토크나이저로 재야 한다(설계 §4 주의)."""


@dataclass
class RefineResult:
    lines: list[LogLine]
    stats: dict
    warnings: list[str] = field(default_factory=list)

    def render(self) -> str:
        return "\n".join(l.render() for l in self.lines)

    @property
    def chars(self) -> int:
        return sum(len(l.render()) + 1 for l in self.lines)


# ── 1) 포맷 파싱 ──────────────────────────────────────────────────────────────

def parse_lines(text: str, source_file: str = "") -> list[LogLine]:
    """텍스트를 LogLine 목록으로 파싱한다.

    타임스탬프가 없는 줄도 **버리지 않는다** — 드라이버가 무조건 출력하는 로그나
    이어붙은 다중 라인이 여기 해당하며, 버리면 맥락이 끊긴다. `ts=None` 으로 두고
    순서만 보존한다.
    """
    out: list[LogLine] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        m = _RE_TS.match(line)
        if m:
            cpu = int(m.group(1)) if m.group(1) else None
            out.append(LogLine(raw=line, ts=float(m.group(2)),
                               message=m.group(3).strip(),
                               source_file=source_file, cpu=cpu))
        else:
            out.append(LogLine(raw=line, ts=None, message=line.strip(),
                               source_file=source_file))
    return out


# ── 2) 반복/버스트 collapse ───────────────────────────────────────────────────

def _absorb_repeat_markers(lines: list[LogLine]) -> list[LogLine]:
    """"last message repeated N times" 를 직전 줄의 count 에 합치고 마커는 버린다."""
    out: list[LogLine] = []
    for ll in lines:
        m = _RE_REPEAT.search(ll.message)
        if m and out:
            out[-1] = replace(out[-1], count=out[-1].count + int(m.group(1)))
        else:
            out.append(ll)
    return out


def _collapse_consecutive(lines: list[LogLine]) -> list[LogLine]:
    """연속된 동일 **본문**을 하나로 합친다.

    지문이 아니라 본문 완전 일치를 쓴다 — 연속 중복은 정보가 없는 게 확실하지만,
    지문만 같은(값이 다른) 줄은 서로 다른 이벤트일 수 있기 때문이다.
    """
    if not lines:
        return lines
    out = [lines[0]]
    for ll in lines[1:]:
        if ll.message == out[-1].message:
            out[-1] = replace(out[-1], count=out[-1].count + ll.count)
        else:
            out.append(ll)
    return out


def _collapse_bursts(lines: list[LogLine], cfg: RefineConfig) -> tuple[list[LogLine], int]:
    """`burst_window_sec` 안에 같은 지문이 `burst_threshold` 회 이상이면 대표 1줄로 축약.

    타임스탬프 없는 줄은 시간 판정이 불가능하므로 버스트 대상에서 제외한다(보존).
    반환: (결과, 축약으로 사라진 줄 수)
    """
    if cfg.burst_threshold <= 0:
        return lines, 0

    out: list[LogLine] = []
    skip: set[int] = set()
    removed = 0

    for i, ll in enumerate(lines):
        if i in skip:
            continue
        if ll.ts is None:
            out.append(ll)
            continue

        fp = ll.fingerprint
        same = [i]
        for j in range(i + 1, len(lines)):
            nxt = lines[j]
            if nxt.ts is None:
                continue
            if nxt.ts - ll.ts > cfg.burst_window_sec:
                break
            if nxt.fingerprint == fp:
                same.append(j)

        if len(same) >= cfg.burst_threshold:
            total = sum(lines[k].count for k in same)
            out.append(replace(ll, count=total))
            skip.update(same[1:])
            removed += len(same) - 1
        else:
            out.append(ll)

    return out, removed


# ── 3) 키워드 필터 ────────────────────────────────────────────────────────────

def filter_by_keywords(lines: list[LogLine], keywords: list[str]) -> list[LogLine]:
    """키워드 중 하나라도 본문에 포함된 줄만 남긴다(OR, 대소문자 무시).

    줄 단위로 걸러도 남은 줄의 타임스탬프와 상대 순서는 그대로이므로, 드라이버
    로그 사이의 시퀀스(Stage 2 Q1 이 쓰는 것)는 보존된다. 잘려나가는 것은 이
    전문가와 무관한 타 서브시스템 로그다.
    """
    if not keywords:
        return lines
    kws = [k.lower() for k in keywords if k.strip()]
    if not kws:
        return lines
    return [l for l in lines if any(k in l.message.lower() for k in kws)]


# ── 4) 예산 강제 ──────────────────────────────────────────────────────────────

def enforce_budget(lines: list[LogLine], cfg: RefineConfig) -> tuple[list[LogLine], str | None]:
    """추정 토큰이 예산을 넘으면 **가운데를 버리고** 머리·꼬리를 남긴다.

    머리는 부팅·초기화 맥락, 꼬리는 실패 시점을 담는 경우가 많고 어느 쪽이
    중요한지는 문제마다 다르다 — 한쪽만 남기면 절반의 문제에서 틀린다.
    반환: (결과, 경고 문구 또는 None)
    """
    if cfg.budget_tokens <= 0 or not lines:
        return lines, None

    def est_tokens(ls: list[LogLine]) -> float:
        return sum(len(l.render()) + 1 for l in ls) / cfg.chars_per_token

    if est_tokens(lines) <= cfg.budget_tokens:
        return lines, None

    # 이분 탐색으로 예산에 맞는 최대 유지 줄 수를 찾는다(선형 축소보다 안정적).
    lo, hi = 0, len(lines)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        head = mid // 2
        tail = mid - head
        if est_tokens(lines[:head] + lines[len(lines) - tail:]) <= cfg.budget_tokens:
            lo = mid
        else:
            hi = mid - 1

    keep = lo
    head, tail = keep // 2, keep - keep // 2
    dropped = len(lines) - keep
    kept = lines[:head] + lines[len(lines) - tail:]

    msg = (f"예산({cfg.budget_tokens:,} 토큰) 초과로 가운데 {dropped:,}줄을 버렸다 "
           f"(머리 {head:,} + 꼬리 {tail:,}줄 유지). "
           f"버려진 구간에 관련 로그가 있었을 수 있다 — 결과 해석 시 감안할 것.")
    return kept, msg


# ── 통합 ──────────────────────────────────────────────────────────────────────

def refine(raw_logs: dict[str, str], cfg: RefineConfig) -> RefineResult:
    """raw 로그를 한 전문가의 관점으로 정제한다.

    Parameters
    ----------
    raw_logs : {파일명: 내용}
    cfg      : 전문가별 설정 (`keywords` 가 프로파일마다 다르다)
    """
    warnings: list[str] = []

    # 파일명 정렬로 결정론 보장 — dict 순서에 의존하지 않는다.
    parsed: list[LogLine] = []
    for name in sorted(raw_logs):
        parsed.extend(parse_lines(raw_logs[name], source_file=name))

    n_parsed = len(parsed)
    n_no_ts = sum(1 for l in parsed if l.ts is None)

    after_marker = _absorb_repeat_markers(parsed)
    after_consec = _collapse_consecutive(after_marker)
    after_burst, burst_removed = _collapse_bursts(after_consec, cfg)
    n_collapsed = n_parsed - len(after_burst)

    filtered = filter_by_keywords(after_burst, cfg.keywords)
    if cfg.keywords and not filtered:
        warnings.append(
            f"키워드 {cfg.keywords} 로 남은 줄이 0이다 — 이 전문가와 무관한 "
            f"로그이거나 키워드가 맞지 않는다. 프로파일을 확인할 것."
        )

    final, budget_warn = enforce_budget(filtered, cfg)
    if budget_warn:
        warnings.append(budget_warn)

    chars = sum(len(l.render()) + 1 for l in final)
    stats = {
        "lines_parsed":      n_parsed,
        "lines_no_timestamp": n_no_ts,
        "lines_after_collapse": len(after_burst),
        "collapsed_away":    n_collapsed,
        "burst_removed":     burst_removed,
        "lines_after_filter": len(filtered),
        "lines_final":       len(final),
        "chars_final":       chars,
        "est_tokens":        int(chars / cfg.chars_per_token),
        "budget_tokens":     cfg.budget_tokens,
    }
    return RefineResult(lines=final, stats=stats, warnings=warnings)


# ── CLI ───────────────────────────────────────────────────────────────────────

def _read(path: Path) -> str:
    """바이트로 읽고 관용적으로 디코드한다(소스·로그에 인코딩이 섞여 있다)."""
    return path.read_bytes().decode("utf-8", errors="replace")


def main() -> None:
    ap = argparse.ArgumentParser(description="Stage 1 — 전문가별 로그 정제")
    ap.add_argument("logs", nargs="+", type=Path, help="로그 파일 (여러 개 가능)")
    ap.add_argument("--keywords", default="",
                    help="프로파일 prefilter_keywords (쉼표 구분). 비면 필터 미적용")
    ap.add_argument("--burst-window", type=float, default=1.0)
    ap.add_argument("--burst-threshold", type=int, default=5)
    ap.add_argument("--budget-tokens", type=int, default=50_000)
    ap.add_argument("--chars-per-token", type=float, default=1.5)
    ap.add_argument("--out", type=Path, default=None, help="정제 결과 저장 (생략 시 통계만)")
    args = ap.parse_args()

    raw = {}
    for p in args.logs:
        if not p.is_file():
            sys.exit(f"파일을 찾을 수 없음: {p}")
        raw[p.name] = _read(p)

    cfg = RefineConfig(
        keywords        = [k.strip() for k in args.keywords.split(",") if k.strip()],
        burst_window_sec= args.burst_window,
        burst_threshold = args.burst_threshold,
        budget_tokens   = args.budget_tokens,
        chars_per_token = args.chars_per_token,
    )
    res = refine(raw, cfg)

    if args.out:
        args.out.write_text(res.render() + "\n", encoding="utf-8")
        print(f"정제 결과 기록: {args.out}", file=sys.stderr)

    s = res.stats
    print(f"파싱 {s['lines_parsed']:,}줄 (타임스탬프 없음 {s['lines_no_timestamp']:,})", file=sys.stderr)
    print(f"→ collapse 후 {s['lines_after_collapse']:,}줄 "
          f"(축약 {s['collapsed_away']:,}, 그중 버스트 {s['burst_removed']:,})", file=sys.stderr)
    print(f"→ 키워드 필터 후 {s['lines_after_filter']:,}줄", file=sys.stderr)
    print(f"→ 최종 {s['lines_final']:,}줄 · {s['chars_final']:,}자 "
          f"· 추정 {s['est_tokens']:,}토큰 (예산 {s['budget_tokens']:,})", file=sys.stderr)
    for w in res.warnings:
        print(f"[경고] {w}", file=sys.stderr)


if __name__ == "__main__":
    main()
