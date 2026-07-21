"""
core/log_refiner.py

Stage 1 — Common Log Refinement
  1-1  커널 로그 필터링      : 비커널 라인 제거
  1-2  반복/버스트 collapse  : 동일 라인 xN 축약
  1-3  앵커 기반 시간 윈도우 추출 : 관심 구간만 추출
  1-4  처리 조건 기반 파일 선별  : 조건에 부합하는 파일만 선택

Output: L_common (list[LogLine])
"""

import logging
import re
import hashlib
from dataclasses import dataclass, field, replace
from typing import Callable

from core.parser_registry import PARSER_MAP, DEFAULT_ACTIVE_PARSERS

logger = logging.getLogger(__name__)

# "last message repeated N times" syslog 마커
_RE_REPEAT_MARKER = re.compile(r'last message repeated (\d+) times', re.IGNORECASE)

# 버스트 핑거프린팅용 가변 값 제거 패턴 (PID, 주소, IRQ 번호 등)
_RE_VARS = re.compile(
    r'\b0x[0-9a-fA-F]+\b'   # 16진수 주소
    r'|\b[0-9a-fA-F]{8,}\b'  # 긴 hex 문자열
    r'|\bpid\s+\d+\b'        # pid N
    r'|\birq\s+\d+\b'        # irq N
    r'|\bcpu\s*\d+\b'        # cpu N / cpuN
    r'|\bcore\s*\d+\b'       # core N
    r'|\b\d{4,}\b',          # 4자리 이상 숫자 (포트, PID, 주소 등)
    re.IGNORECASE,
)


# ── 데이터 모델 ───────────────────────────────────────────────────────────────

@dataclass
class LogLine:
    """파싱된 커널 로그 한 줄."""
    raw: str                     # 원본 텍스트
    timestamp: float | None      # 커널 uptime (초); 없으면 None
    message: str                 # 타임스탬프 제거 후 메시지 본문
    cpu_id: int | None = None    # CPU ID (dmesg cpuid 형식일 때); 없으면 None
    count: int = 1               # collapse 횟수 (>1 이면 반복된 라인)
    source_file: str = ""        # 원본 파일명; pipeline Stage 1에서 설정됨

    @property
    def fingerprint(self) -> str:
        """가변 값을 제거한 메시지 해시 — 버스트 collapse 식별용."""
        normalized = _RE_VARS.sub('__V__', self.message).strip()
        return hashlib.md5(normalized.encode()).hexdigest()

    def render(self) -> str:
        """사람이 읽기 좋은 형태로 렌더링."""
        if self.timestamp is not None:
            if self.cpu_id is not None:
                ts = f"[{self.cpu_id}-{self.timestamp:>12.6f}]"
            else:
                ts = f"[{self.timestamp:>12.6f}]"
        else:
            ts = "[  no_time   ]"
        suffix = f"  ×{self.count}" if self.count > 1 else ""
        return f"{ts} {self.message}{suffix}"


@dataclass
class RefineConfig:
    """Stage 1 전체 설정."""

    # 0단계: 키워드 전처리 필터 (파일 선별 + 라인 추출, 정제 전 제일 먼저 적용)
    input_keywords: list[str] = field(default_factory=list)
    """키워드 목록 (OR 조건).
    - 파일 선별: 하나라도 포함하는 파일만 통과
    - 라인 추출: 하나라도 포함하는 라인만 남김
    비어 있으면 미적용."""

    # 1-4: 파일 선별 조건 (정제 전에 먼저 적용)
    file_conditions: list[str] = field(default_factory=list)
    """파일이 반드시 포함해야 하는 regex 목록 (AND 조건).
    비어 있으면 모든 파일 통과."""

    # 1-3: 앵커 기반 윈도우
    anchors: list[str] = field(default_factory=list)
    """관심 이벤트를 식별하는 regex 목록. 비어 있으면 윈도우 필터 미적용."""
    window_before_sec: float = 30.0
    """앵커 첫 발생 이전으로 포함할 구간 (초)."""
    window_after_sec: float = 30.0
    """앵커 마지막 발생 이후로 포함할 구간 (초)."""

    # 1-3: 커널 타임스탬프 직접 지정 구간 (앵커보다 우선 적용)
    ts_start: float | None = None
    """포함 구간 시작 커널 uptime (초). None 이면 하한 없음."""
    ts_end: float | None = None
    """포함 구간 종료 커널 uptime (초). None 이면 상한 없음."""

    # 1-1: 사전 정제 — 활성화할 파서 목록
    active_parsers: list[str] = field(default_factory=lambda: list(DEFAULT_ACTIVE_PARSERS))
    """사전 정제에 사용할 파서 이름 목록.
    config/log_parsers.yaml 에 등록된 파서 이름만 유효.
    비어 있으면 커널 로그 필터링을 건너뛰고 모든 라인을 원본 그대로 사용."""

    # 1-2: 반복/버스트 collapse
    burst_window_sec: float = 1.0
    """버스트 판단 슬라이딩 윈도우 크기 (초)."""
    burst_threshold: int = 5
    """이 횟수 이상 동일 fingerprint 가 burst_window_sec 안에 나오면 collapse."""


# ── 메인 클래스 ───────────────────────────────────────────────────────────────

class LogRefiner:
    """Stage 1 — Common Log Refinement."""

    # ── public API ────────────────────────────────────────────────────────────

    def refine(
        self,
        raw_log: str,
        config: RefineConfig,
        on_warning: Callable[[str], None] | None = None,
    ) -> list[LogLine]:
        """
        raw_log 문자열을 받아 L_common 을 반환한다.

        Steps:
            1-1  parse + 커널 라인 필터링
            1-2  반복/버스트 collapse
            1-3  앵커 기반 시간 윈도우 추출

        Parameters
        ----------
        on_warning : 앵커 미발견 등 사용자에게 알려야 할 경고 발생 시 호출되는 콜백.
                     None 이면 logger.warning 으로만 기록.
        """
        lines = self._parse_and_filter(raw_log, config.active_parsers)   # 1-1
        lines = self._collapse(lines, config)                              # 1-2
        lines = self._apply_window(lines, config, on_warning)              # 1-3
        return lines

    def select_files(
        self,
        files: dict[str, str],
        conditions: list[str],
    ) -> dict[str, str]:
        """
        1-4: 처리 조건 기반 파일 선별.

        Parameters
        ----------
        files      : {파일명: 내용} 딕셔너리
        conditions : 각 파일이 반드시 포함해야 하는 regex 패턴 목록
                     (모든 조건이 AND 로 적용됨)

        Returns
        -------
        조건을 모두 만족하는 파일만 포함한 딕셔너리
        """
        if not conditions:
            return files

        compiled = [re.compile(c, re.IGNORECASE) for c in conditions]
        return {
            name: content
            for name, content in files.items()
            if all(pat.search(content) for pat in compiled)
        }

    # ── 1-1  커널 로그 파싱 & 필터링 ─────────────────────────────────────────

    def _parse_and_filter(self, raw_log: str, active_parsers: list[str]) -> list[LogLine]:
        """
        각 줄을 커널 로그 포맷으로 파싱한다.
        active_parsers 에 지정된 포맷만 시도하며,
        인식되지 않는 줄(비커널 라인)은 버린다.

        active_parsers 가 비어 있으면 모든 라인을 timestamp=None 으로 그대로 포함한다.

        지원 형식:
          - dmesg:         [  1234.567890] message
          - syslog kernel: Apr 11 15:47:00 host kernel: [  1234.567890] message
          - journalctl -k: kernel:  [  1234.567890] message
        """
        parsers = [
            PARSER_MAP[name]
            for name in active_parsers
            if name in PARSER_MAP
        ]

        result: list[LogLine] = []
        for raw in raw_log.splitlines():
            line = raw.rstrip()
            if not line:
                continue
            if not parsers:
                # 파서 미선택 → 모든 라인을 원본 그대로 포함
                result.append(LogLine(raw=line, timestamp=None, message=line))
            else:
                ll = self._try_parse(line, parsers)
                if ll is not None:
                    result.append(ll)
        return result

    def _try_parse(self, line: str, parsers: list) -> LogLine | None:
        """한 줄을 파싱. 지정된 파서 중 하나라도 매칭되면 LogLine 반환, 아니면 None."""
        for pattern, cpu_id_group, ts_group, msg_group, use_search in parsers:
            m = pattern.search(line) if use_search else pattern.match(line)
            if m:
                ts_str = m.group(ts_group) if ts_group else None
                ts = float(ts_str) if ts_str else None
                msg = m.group(msg_group).strip()
                cpu_id_str = m.group(cpu_id_group) if cpu_id_group else None
                cpu_id = int(cpu_id_str) if cpu_id_str is not None else None
                return LogLine(raw=line, timestamp=ts, message=msg, cpu_id=cpu_id)
        return None

    # ── 1-2  반복/버스트 collapse ─────────────────────────────────────────────

    def _collapse(self, lines: list[LogLine], config: RefineConfig) -> list[LogLine]:
        """세 단계로 반복/버스트를 축약한다."""
        lines = self._absorb_repeat_markers(lines)   # "last message repeated N times" 처리
        lines = self._collapse_consecutive(lines)    # 연속 동일 메시지 축약
        lines = self._collapse_bursts(lines, config) # 짧은 시간 내 burst 축약
        return lines

    def _absorb_repeat_markers(self, lines: list[LogLine]) -> list[LogLine]:
        """
        'last message repeated N times' 마커를 직전 라인의 count 에 합산하고
        마커 라인 자체는 제거한다.
        """
        result: list[LogLine] = []
        for ll in lines:
            m = _RE_REPEAT_MARKER.search(ll.message)
            if m and result:
                result[-1].count += int(m.group(1))
            else:
                result.append(ll)
        return result

    def _collapse_consecutive(self, lines: list[LogLine]) -> list[LogLine]:
        """연속으로 동일한 message 를 가진 라인을 하나로 묶는다."""
        if not lines:
            return lines
        result = [lines[0]]
        for ll in lines[1:]:
            if ll.message == result[-1].message:
                result[-1].count += ll.count
            else:
                result.append(ll)
        return result

    def _collapse_bursts(self, lines: list[LogLine], config: RefineConfig) -> list[LogLine]:
        """
        burst_window_sec 안에 burst_threshold 회 이상 같은 fingerprint 가
        등장하면, 첫 번째 라인만 남기고 나머지는 count 에 합산한다.

        타임스탬프가 없는 라인은 burst 판단에서 제외한다.
        """
        if config.burst_threshold <= 0:
            return lines

        result: list[LogLine] = []
        skip: set[int] = set()

        for i, ll in enumerate(lines):
            if i in skip:
                continue
            if ll.timestamp is None:
                result.append(ll)
                continue

            # 같은 fingerprint 를 가진 인접 라인 인덱스를 수집
            same: list[int] = [i]
            for j in range(i + 1, len(lines)):
                nxt = lines[j]
                if nxt.timestamp is None:
                    continue
                if nxt.timestamp - ll.timestamp > config.burst_window_sec:
                    break
                if nxt.fingerprint == ll.fingerprint:
                    same.append(j)

            if len(same) >= config.burst_threshold:
                # 대표 라인 하나만 남기고 count 합산
                result.append(replace(ll, count=sum(lines[k].count for k in same)))
                skip.update(same[1:])
            else:
                result.append(ll)

        return result

    # ── 1-3  앵커 기반 시간 윈도우 추출 ──────────────────────────────────────

    def _apply_window(
        self,
        lines: list[LogLine],
        config: RefineConfig,
        on_warning: Callable[[str], None] | None = None,
    ) -> list[LogLine]:
        """
        시간 구간을 계산한 뒤 해당 구간에 속하는 라인만 반환한다.

        구간 결정 우선순위:
          1. ts_start / ts_end 직접 지정 → 해당 값을 그대로 사용.
             둘 중 하나만 지정해도 됨 (None 쪽은 한계 없음).
          2. anchors 패턴 → 매칭 타임스탬프 기준으로 window_before/after 적용.
          3. 아무것도 지정하지 않으면 전체 반환.

        타임스탬프 없는 라인은 직전 라인이 윈도우 안에 있을 때만 포함.
        """
        t_start: float
        t_end: float

        direct = config.ts_start is not None or config.ts_end is not None

        if direct:
            # 직접 지정 모드 — 한쪽만 지정해도 동작
            t_start = config.ts_start if config.ts_start is not None else float("-inf")
            t_end   = config.ts_end   if config.ts_end   is not None else float("inf")

        elif config.anchors:
            # 앵커 기반 모드
            anchor_pats = [re.compile(a, re.IGNORECASE) for a in config.anchors]
            anchor_ts: list[float] = [
                ll.timestamp
                for ll in lines
                if ll.timestamp is not None
                and any(p.search(ll.message) for p in anchor_pats)
            ]

            if not anchor_ts:
                msg = (
                    f"앵커 패턴 {config.anchors!r}이 로그에서 매칭되지 않았습니다. "
                    "시간 윈도우 필터를 적용하지 않고 전체 로그를 사용합니다."
                )
                logger.warning(msg)
                if on_warning is not None:
                    on_warning(msg)
                return lines  # 앵커 미발견 → 원본 그대로

            t_start = min(anchor_ts) - config.window_before_sec
            t_end   = max(anchor_ts) + config.window_after_sec

        else:
            return lines  # 필터 조건 없음 → 전체 반환

        result: list[LogLine] = []
        prev_in_window = False
        for ll in lines:
            if ll.timestamp is None:
                if prev_in_window:
                    result.append(ll)
            elif t_start <= ll.timestamp <= t_end:
                result.append(ll)
                prev_in_window = True
            else:
                prev_in_window = False

        return result


# ── Stage 3 — Case-Specific Log Refinement ───────────────────────────────────
#
# L_common → 패턴 키워드 기반 재필터링 → L_refined
#
# HIT  : matched_case.keywords 로 필터 → L_refined 1개, 케이스의 모든 패턴이 공유
# MISS : 패턴별 keywords 로 필터 → L_refined N개 (패턴마다 1개)
#
# Stage 4 는 RefinedEntry 목록을 순회하며 각 (pattern, lines) 쌍을 처리한다.

@dataclass
class RefinedEntry:
    """Stage 4 처리 단위 — 패턴 1개 + 그 패턴 전용으로 필터링된 로그."""
    pattern: dict           # DB 에서 로드된 패턴 (steps / components 포함)
    lines: list[LogLine]    # keyword 필터링된 L_refined


def refine_for_case(
    l_common: list[LogLine],
    matched_case,               # core.kb_search.MatchedCase  (순환 import 방지로 타입 힌트 생략)
) -> list[RefinedEntry]:
    """
    HIT 경로: matched_case.keywords 로 L_common 을 필터링해
    케이스의 모든 패턴이 공유하는 단일 L_refined 를 만든다.

    Parameters
    ----------
    l_common      : Stage 1 출력
    matched_case  : Stage 2 HIT 결과 (MatchedCase)

    Returns
    -------
    패턴 수만큼의 RefinedEntry — 모두 같은 lines 를 가진다.
    """
    lines = _filter_by_keywords(l_common, matched_case.keywords)
    return [
        RefinedEntry(pattern=p, lines=lines)
        for p in matched_case.patterns
    ]


def refine_for_patterns(
    l_common: list[LogLine],
    patterns: list[dict],
) -> list[RefinedEntry]:
    """
    MISS 경로: 패턴별 keywords 로 각각 필터링해
    패턴마다 별도의 L_refined 를 만든다.

    Parameters
    ----------
    l_common : Stage 1 출력
    patterns : DB 에서 로드한 전체 패턴 목록

    Returns
    -------
    키워드 매칭 결과가 1줄 이상인 RefinedEntry 만 반환.
    (빈 로그에 대해 Stage 4 를 실행할 이유가 없음)
    """
    result: list[RefinedEntry] = []
    for p in patterns:
        keywords = p.get("keywords") or []
        lines = _filter_by_keywords(l_common, keywords)
        if lines:                               # 빈 결과는 제외
            result.append(RefinedEntry(pattern=p, lines=lines))
    return result


def _filter_by_keywords(lines: list[LogLine], keywords: list[str]) -> list[LogLine]:
    """
    keywords 중 하나라도 message 에 포함된 라인만 반환한다 (대소문자 무시).
    keywords 가 비어 있으면 전체를 그대로 반환한다.
    """
    if not keywords:
        return lines
    kw_lower = [kw.lower() for kw in keywords]
    return [
        ll for ll in lines
        if any(kw in ll.message.lower() for kw in kw_lower)
    ]


# ── 유틸리티 ──────────────────────────────────────────────────────────────────

def render_lines(lines: list[LogLine]) -> str:
    """L_common 을 사람이 읽을 수 있는 문자열로 변환."""
    return "\n".join(ll.render() for ll in lines)


def prefilter_by_keywords(
    raw_logs: dict[str, str],
    keywords: list[str],
) -> dict[str, str]:
    """
    키워드 기반 전처리 필터 — 정제 파이프라인의 0단계.

    1. 파일 선별: keywords 중 하나라도 포함하는 파일만 통과 (OR)
    2. 라인 추출: 통과된 파일에서 keywords 중 하나라도 포함하는 라인만 남김 (OR)

    keywords 가 비어 있으면 원본 그대로 반환.
    대소문자 무시(case-insensitive).

    Parameters
    ----------
    raw_logs : {파일명: 내용} 딕셔너리
    keywords : 필터 키워드 목록

    Returns
    -------
    {파일명: 필터링된 내용} — 조건을 만족하는 파일/라인만 포함
    """
    if not keywords:
        return raw_logs

    kw_lower = [kw.lower() for kw in keywords]
    result: dict[str, str] = {}

    for name, content in raw_logs.items():
        # 1) 파일 선별: 하나라도 포함되면 통과
        content_lower = content.lower()
        if not any(kw in content_lower for kw in kw_lower):
            continue

        # 2) 라인 추출: 하나라도 포함되는 라인만 남김
        filtered = "\n".join(
            line for line in content.splitlines()
            if any(kw in line.lower() for kw in kw_lower)
        )
        if filtered:
            result[name] = filtered

    return result
