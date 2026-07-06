"""
core/pattern_matcher.py

Stage 4 — Pattern Matching

4-0  이벤트 정규화 (Fingerprinting)  : LogLine.fingerprint 재사용
4-1  패턴 타입별 매칭                 : PRESENCE / SEQUENCE / WINDOW / ABSENCE / COMPOSITE
4-2  점수 계산                        : score = Σ(weight × matched) / Σ(weight)

Input  : list[RefinedEntry]  (Stage 3 출력)
Output : MatchResult
"""

import logging
import re
from dataclasses import dataclass, field

from core.log_refiner import LogLine, RefinedEntry

logger = logging.getLogger(__name__)


# ── 결과 타입 ─────────────────────────────────────────────────────────────────

@dataclass
class PatternResult:
    """문제 패턴 1건의 매칭 결과."""
    name: str
    type: str
    matched: bool
    weight: float
    evidence: list[LogLine] = field(default_factory=list)
    """매칭에 기여한 로그 라인들."""
    analysis_guidelines: str = ""
    """패턴별 분석지침 — Stage 5 LLM 프롬프트에 주입된다."""


@dataclass
class MatchResult:
    """Stage 4 전체 결과 — Stage 5 Report Generation 에 전달된다."""
    matched: list[PatternResult]
    unmatched: list[PatternResult]
    score: float                    # 0.0 ~ 1.0


# ── PatternMatcher ────────────────────────────────────────────────────────────

class PatternMatcher:
    """
    Stage 4 실행기.

    Streamlit 에서 st.cache_resource 로 싱글턴으로 사용한다.

        @st.cache_resource
        def get_matcher():
            return PatternMatcher()
    """

    def match_entries(self, entries: list[RefinedEntry]) -> MatchResult:
        """
        Stage 3 출력(RefinedEntry 목록)을 받아 Stage 4 전체를 실행한다.

        COMPOSITE 은 다른 패턴 결과에 의존하므로 non-COMPOSITE 을 먼저 처리한다.
        """
        non_composite = [e for e in entries if e.pattern["type"] != "COMPOSITE"]
        composite     = [e for e in entries if e.pattern["type"] == "COMPOSITE"]

        results_by_name: dict[str, PatternResult] = {}

        # 4-1: non-COMPOSITE 먼저 매칭
        for entry in non_composite:
            r = self._match_one(entry.pattern, entry.lines)
            results_by_name[entry.pattern["name"]] = r

        # 4-1: COMPOSITE — 위 결과를 참조
        for entry in composite:
            r = self._match_composite(entry.pattern, results_by_name)
            results_by_name[entry.pattern["name"]] = r

        all_results = list(results_by_name.values())

        # 4-2: 점수 계산
        matched   = [r for r in all_results if r.matched]
        unmatched = [r for r in all_results if not r.matched]
        total_w   = sum(r.weight for r in all_results)
        score     = (sum(r.weight for r in matched) / total_w) if total_w > 0 else 0.0

        return MatchResult(
            matched=matched,
            unmatched=unmatched,
            score=round(score, 4),
        )

    # ── 타입 디스패치 ─────────────────────────────────────────────────────────

    def _match_one(self, pattern: dict, lines: list[LogLine]) -> PatternResult:
        t = pattern.get("type", "")
        if t == "PRESENCE":
            return self._match_presence(pattern, lines)
        if t == "SEQUENCE":
            return self._match_sequence(pattern, lines)
        if t == "WINDOW":
            return self._match_window(pattern, lines)
        if t == "ABSENCE":
            return self._match_absence(pattern, lines)
        raise ValueError(f"알 수 없는 패턴 타입: {t!r}")

    def _base(self, pattern: dict, matched: bool, evidence: list[LogLine]) -> PatternResult:
        return PatternResult(
            name=pattern["name"],
            type=pattern["type"],
            matched=matched,
            weight=float(pattern.get("weight", 1.0)),
            evidence=evidence,
            analysis_guidelines=pattern.get("analysis_guidelines", ""),
        )

    # ── PRESENCE ──────────────────────────────────────────────────────────────

    def _match_presence(self, pattern: dict, lines: list[LogLine]) -> PatternResult:
        """
        pattern.pattern(regex) 가 한 번 이상 등장하는지 확인한다.

        event_dedup_window_sec: 같은 fingerprint 가 이 초 안에 반복되면 1회로 계산.
        """
        pat        = re.compile(pattern["pattern"], re.IGNORECASE)
        dedup_win  = pattern.get("event_dedup_window_sec")
        seen: dict[str, float] = {}   # fingerprint → 마지막 매칭 타임스탬프
        evidence: list[LogLine] = []

        for ll in lines:
            if not pat.search(ll.message):
                continue

            if dedup_win is not None:
                fp   = ll.fingerprint
                ts   = ll.timestamp or 0.0
                last = seen.get(fp)
                if last is not None and ts - last <= dedup_win:
                    continue          # 중복 윈도우 내 재등장 → 무시
                seen[fp] = ts

            evidence.append(ll)

        return self._base(pattern, bool(evidence), evidence)

    # ── SEQUENCE ──────────────────────────────────────────────────────────────

    def _match_sequence(self, pattern: dict, lines: list[LogLine]) -> PatternResult:
        """
        steps 를 이 순서대로 찾는다.

        step_dedup     : 각 step 위치에서 같은 fingerprint 를 두 번 이상 사용하지 않는다.
        non_overlapping: 시퀀스 완료 후 마지막 매칭 라인 이후에만 새 시퀀스 시작.
        """
        steps           = [re.compile(s, re.IGNORECASE) for s in (pattern.get("steps") or [])]
        step_dedup      = bool(pattern.get("step_dedup", False))
        non_overlapping = bool(pattern.get("non_overlapping", False))

        if not steps:
            return self._base(pattern, False, [])

        sequences: list[list[LogLine]] = []   # 완성된 시퀀스 목록
        step_idx   = 0
        current    = []                        # 진행 중인 시퀀스
        barrier_ts: float | None = None        # non_overlapping 장벽
        step_fps: list[set] = [set() for _ in steps]   # step_dedup 용

        for ll in lines:
            # non_overlapping: 장벽 이전 라인 무시
            if barrier_ts is not None and (ll.timestamp or 0.0) <= barrier_ts:
                continue

            if not steps[step_idx].search(ll.message):
                continue

            # step_dedup: 이 step 위치에서 같은 fingerprint 재사용 방지
            if step_dedup:
                fp = ll.fingerprint
                if fp in step_fps[step_idx]:
                    continue
                step_fps[step_idx].add(fp)

            current.append(ll)
            step_idx += 1

            if step_idx == len(steps):
                # 시퀀스 완성
                sequences.append(current[:])
                if non_overlapping:
                    barrier_ts = ll.timestamp or 0.0
                # 상태 초기화
                current   = []
                step_idx  = 0
                step_fps  = [set() for _ in steps]

        evidence = [line for seq in sequences for line in seq]
        return self._base(pattern, bool(sequences), evidence)

    # ── WINDOW ────────────────────────────────────────────────────────────────

    def _match_window(self, pattern: dict, lines: list[LogLine]) -> PatternResult:
        """
        window_sec 안에 pattern 이 count_threshold 회 이상 등장하는지 확인한다.

        count_unique_only: fingerprint 기준 중복 제거 후 카운트.
        """
        pat             = re.compile(pattern["pattern"], re.IGNORECASE)
        window_sec      = float(pattern.get("window_sec", 60.0))
        threshold       = int(pattern.get("count_threshold", 1))
        unique_only     = bool(pattern.get("count_unique_only", False))

        # 매칭 라인 수집
        matching = [ll for ll in lines if pat.search(ll.message)]

        if unique_only:
            seen_fps: set[str] = set()
            deduped: list[LogLine] = []
            for ll in matching:
                fp = ll.fingerprint
                if fp not in seen_fps:
                    seen_fps.add(fp)
                    deduped.append(ll)
            matching = deduped

        # 타임스탬프 없는 경우: 단순 카운트
        timestamped = [ll for ll in matching if ll.timestamp is not None]
        if not timestamped:
            matched = len(matching) >= threshold
            return self._base(pattern, matched, matching[:threshold] if matched else [])

        # 슬라이딩 윈도우 (오름차순 정렬 보장 — L_refined 는 시간순)
        left = 0
        best: list[LogLine] = []

        for right in range(len(timestamped)):
            while (timestamped[right].timestamp - timestamped[left].timestamp) > window_sec:
                left += 1
            window = timestamped[left : right + 1]
            if len(window) >= threshold and len(window) > len(best):
                best = window[:]

        return self._base(pattern, len(best) >= threshold, best)

    # ── ABSENCE ───────────────────────────────────────────────────────────────

    def _match_absence(self, pattern: dict, lines: list[LogLine]) -> PatternResult:
        """
        trigger_pattern 발생 후 window_sec 내에 absent_pattern 이 등장하지 않으면 매칭.

        상태 기계:
          IDLE     → (trigger 매칭)     → WATCHING
          WATCHING → (absent 매칭)      → IDLE  (absence 불성립)
          WATCHING → (window 만료)      → IDLE + evidence 추가
        """
        trigger_pat = re.compile(pattern["trigger_pattern"], re.IGNORECASE)
        absent_pat  = re.compile(pattern["absent_pattern"],  re.IGNORECASE)
        window_sec  = float(pattern.get("window_sec", 30.0))

        state: str                  = "IDLE"
        trigger_line: LogLine | None = None
        trigger_ts: float | None    = None
        evidence: list[LogLine]     = []

        def _expire(current_ts: float | None = None) -> None:
            """윈도우 만료 처리 — evidence 에 trigger 라인 추가."""
            nonlocal state, trigger_line, trigger_ts
            if trigger_line is not None:
                if (current_ts is None
                        or trigger_ts is None
                        or current_ts - trigger_ts > window_sec):
                    evidence.append(trigger_line)
            state = "IDLE"
            trigger_line = trigger_ts = None

        for ll in lines:
            ts = ll.timestamp

            if state == "IDLE":
                if trigger_pat.search(ll.message):
                    state        = "WATCHING"
                    trigger_line = ll
                    trigger_ts   = ts

            elif state == "WATCHING":
                # 윈도우 만료 확인
                if ts is not None and trigger_ts is not None:
                    if ts - trigger_ts > window_sec:
                        _expire(ts)
                        # 이 라인이 새 trigger 일 수 있음
                        if trigger_pat.search(ll.message):
                            state        = "WATCHING"
                            trigger_line = ll
                            trigger_ts   = ts
                        continue

                # absent_pattern 등장 → absence 불성립
                if absent_pat.search(ll.message):
                    state        = "IDLE"
                    trigger_line = trigger_ts = None
                    continue

                # 새 trigger 중첩 — 더 최근 trigger 로 갱신
                if trigger_pat.search(ll.message):
                    trigger_line = ll
                    trigger_ts   = ts

        # 마지막 라인 처리 후 WATCHING 상태이면 만료 여부 확인
        if state == "WATCHING":
            last_ts = lines[-1].timestamp if lines else None
            _expire(last_ts)

        return self._base(pattern, bool(evidence), evidence)

    # ── COMPOSITE ─────────────────────────────────────────────────────────────

    def _match_composite(
        self,
        pattern: dict,
        results_by_name: dict[str, PatternResult],
    ) -> PatternResult:
        """
        AND / OR / NOT 로 하위 패턴 결과를 조합한다.
        참조 패턴이 results_by_name 에 없으면 경고를 남기고 미매칭으로 간주한다.
        """
        operator   = (pattern.get("operator") or "AND").upper()
        components = pattern.get("components") or []
        comp_name  = pattern.get("name", "<unnamed>")

        missing = [name for name in components if name not in results_by_name]
        if missing:
            logger.warning(
                "COMPOSITE 패턴 %r: 참조 패턴 %s 을(를) 찾을 수 없습니다 — 해당 컴포넌트를 미매칭으로 처리합니다.",
                comp_name,
                missing,
            )

        sub: list[PatternResult | None] = [results_by_name.get(name) for name in components]

        if operator == "AND":
            matched = bool(sub) and all(r is not None and r.matched for r in sub)
        elif operator == "OR":
            matched = any(r is not None and r.matched for r in sub)
        elif operator == "NOT":
            if len(components) != 1:
                logger.warning(
                    "COMPOSITE 패턴 %r: NOT 연산자는 컴포넌트 1개만 지원하지만 %d개가 지정되었습니다 — 첫 번째만 사용합니다.",
                    comp_name,
                    len(components),
                )
            r0      = sub[0] if sub else None
            # 참조 패턴이 누락된 경우 NOT 을 성립시키지 않는다 (암묵적 True 방지)
            matched = r0 is not None and not r0.matched
        else:
            logger.warning("COMPOSITE 패턴 %r: 알 수 없는 연산자 %r — 미매칭 처리합니다.", comp_name, operator)
            matched = False

        evidence: list[LogLine] = []
        if matched:
            for r in sub:
                if r is not None:
                    evidence.extend(r.evidence)

        return self._base(pattern, matched, evidence)
