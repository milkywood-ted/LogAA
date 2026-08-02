#!/usr/bin/env python3
"""후보가 여럿인 로그 라인을 **소스와 대조해** 좁힌다.

설계 문서: `Document/신규 문제 분석 파이프라인/신규 문제 분석 파이프라인 설계.md` §4

왜 필요한가
-----------
자료 갱신(`0fe6b7c97`)으로 `SDP_DP_CHK` 시그니처가 인덱스에 들어왔는데,
`match_key` 가 **칩당 94~175행에서 전부 동일**하다(`DP_ERROR[0x`). 로그 한 줄이
후보 175곳을 관측 집합에 넣으면 § 발췌가 7 → 57 개로 터지고 프롬프트가 입력
여유를 넘긴다.

자료가 해법을 적어 뒀다 (`log_analysis/01_log_grammar.md §3.1`):

> 인덱스에서 고정부는 `DP_ERROR[0x` 하나뿐이므로 매칭은 그것으로 하고,
> 뒤의 `= <식>`이 곧 실패한 호출이다. `match_key`가 같은 행이 칩당 94~175개이므로
> **행 하나로 위치가 특정되지 않는다** — `= <식>`의 함수명을 소스에서 다시 grep해
> 호출부를 찾는다.

추측하지 않는다
---------------
좁히는 근거는 **런타임 값이 소스에 실제로 있는가** 뿐이다. 두 형태를 본다:

- **표현식 대조** — `%s` 가 호출식처럼 생겼고(`(`…`)`) 후보 지점 소스에 **그대로**
  나타난다. `SDP_DP_CHK(chkType, retVar, fcall)` 이 `#fcall` 로 호출식을 문자열화
  하므로 성립한다.
- **함수명 대조** — `%s` 가 식별자이고 후보를 **감싼 함수의 이름과 같다**.
  `pr_err("DRM-DP : %s return :%d\\n", __func__, ret)` 부류(칩당 28곳)가 여기 걸린다.

둘 다 안 걸리면 **좁히지 않는다.** `__FILE__` 을 찍는 부류(`[%s] NULL handle error`,
28곳)는 값이 파일명이라 같은 파일 안에서는 아무것도 구분하지 못하는데, 위 두 검사
어디에도 걸리지 않으므로 자동으로 그렇게 된다 — 별도 예외 처리가 필요 없다.

성공하든 실패하든 **어떻게 좁혔는지 항상 보고한다**. 유도된 위치를 인덱스가 준
위치와 구분 없이 내놓으면, 읽는 쪽이 확정도를 오판한다.

의존성: Python 3 표준 라이브러리 + 같은 디렉토리 `source_slice.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

from source_slice import SOURCE_SUBDIR, _find_enclosing, _read

# printf 변환 지정자. 자료의 `format` 열은 소스의 포맷 문자열 그대로다.
_CONV = re.compile(
    r"%[-+ #0]*[0-9*]*(?:\.[0-9*]+)?(?:hh|h|ll|l|L|z|j|t)?([diouxXeEfgGaAcsp%])"
)

# 호출식으로 인정할 최소 형태 — 괄호 쌍을 갖춘 것. `#fcall` 문자열화의 산물이다.
_RE_CALL_EXPR = re.compile(r"^[A-Za-z_][\w\s.*&\->\[\]]*\(.*\)$")
_RE_IDENT = re.compile(r"^[A-Za-z_]\w*$")

_MIN_VALUE_LEN = 4       # 이보다 짧은 값은 우연 일치 위험이 커서 쓰지 않는다
_WINDOW = 5              # 후보 라인부터 앞으로 몇 줄까지 호출식이 이어질 수 있는가


def format_to_regex(fmt: str) -> tuple[re.Pattern | None, int]:
    """자료의 `format` 열을 런타임 라인 매칭용 정규식으로 바꾼다.

    `%s` 만 캡처한다 — 소스와 대조할 수 있는 것은 문자열 값뿐이다. 숫자 지정자는
    자리만 맞춘다. 반환: (정규식, `%s` 캡처 수). `%s` 가 없으면 (None, 0).
    """
    fmt = fmt.replace("\\n", "\n").replace("\\t", "\t")
    parts: list[str] = []
    pos = ngroups = 0
    for m in _CONV.finditer(fmt):
        parts.append(re.escape(fmt[pos:m.start()]))
        conv = m.group(1)
        if conv == "%":
            parts.append("%")
        elif conv == "s":
            parts.append("(.+?)")
            ngroups += 1
        elif conv in "diu":
            parts.append(r"-?\d+")
        elif conv in "oxXp":
            parts.append(r"[0-9a-fA-Fx]+")
        elif conv == "c":
            parts.append(".")
        else:
            parts.append(r"\S+")
        pos = m.end()

    if ngroups == 0:
        return None, 0

    tail = fmt[pos:].rstrip("\n")
    parts.append(re.escape(tail))
    # 마지막 `%s` 뒤에 리터럴이 없으면 lazy 캡처가 1자만 먹는다 — 줄 끝까지 늘린다.
    if not tail:
        parts.append(r"\s*$")
    return re.compile("".join(parts)), ngroups


def extract_values(line: str, fmt: str) -> list[str]:
    """런타임 라인에서 `format` 의 `%s` 자리에 들어간 값을 뽑는다."""
    rx, n = format_to_regex(fmt)
    if rx is None:
        return []
    m = rx.search(line)
    if not m:
        return []
    return [_norm(g) for g in m.groups() if g]


def _norm(s: str) -> str:
    """공백을 하나로 접는다.

    전처리기 `#` 문자열화는 토큰 사이 공백을 **공백 하나**로 만드는데, 소스 쪽은
    줄바꿈·탭으로 흩어져 있다(`SDP_DP_CHK(DP_RETURN, dp_err\\n\\t\\t, foo(a, b))`).
    양쪽을 같은 기준으로 접어야 대조가 성립한다.
    """
    return re.sub(r"\s+", " ", s).strip()


class _SourceCache:
    """같은 파일을 후보 수만큼 다시 읽지 않기 위한 캐시."""

    def __init__(self, src_root: Path) -> None:
        self.root = src_root
        self._files: dict[str, list[str] | None] = {}

    def lines(self, rel: str) -> list[str] | None:
        if rel not in self._files:
            p = self.root / rel
            self._files[rel] = _read(p) if p.is_file() else None
        return self._files[rel]


def _candidate_matches(
    cache: _SourceCache, file_line: str, value: str, stop_at: int,
) -> bool:
    """후보 지점의 소스가 런타임 값을 뒷받침하는가.

    `stop_at` — 같은 파일의 **다음 후보 라인**(1-based). 창이 거기까지만 간다.
    """
    rel, _, ln = file_line.rpartition(":")
    if not ln.isdigit():
        return False
    lines = cache.lines(rel)
    if lines is None:
        return False
    i = int(ln) - 1
    if not (0 <= i < len(lines)):
        return False

    # ① 표현식 대조 — 후보 라인부터 앞으로 훑는다. **뒤로는 가지 않고, 다음
    #    후보 앞에서 멈춘다.** 둘 다 필요하다: 매크로 호출은 기록된 라인에서
    #    시작하고, 창이 다음 호출부를 삼키면 서로를 오탐한다(실측 — 1550 이
    #    1553 의 `dp_in_modeset_extin` 을 먹어 2곳으로 나왔다). 라인 근접만으로
    #    귀속시키는 것은 자료 README §4-1 이 기록한 오판 부류다.
    if _RE_CALL_EXPR.match(value):
        end = min(i + _WINDOW, stop_at - 1 if stop_at else len(lines), len(lines))
        window = _norm(" ".join(lines[i:max(end, i + 1)]))
        if value in window:
            return True

    # ② 함수명 대조 — `__func__` 부류.
    if _RE_IDENT.match(value):
        name, _s, _e, note = _find_enclosing(lines, int(ln))
        if not note and name == value:
            return True

    return False


def narrow(
    line: str,
    entries: list[dict],
    material_root: Path,
) -> tuple[list[str] | None, str]:
    """후보를 소스 대조로 좁힌다.

    Parameters
    ----------
    line : 정제된 런타임 로그 한 줄
    entries : 이 라인에 걸린 인덱스 행들 (`format`·`file_line` 필요)
    material_root : 자료 저장소 루트 (`tztv-media-sec/` 를 담고 있는 곳)

    Returns
    -------
    (좁혀진 `file:line` 목록, 설명). 좁히지 못하면 (None, 사유) — **호출자가 원래
    후보를 그대로 유지해야 한다.** 빈 목록을 반환하지 않는 이유는 "좁혀서 0개"와
    "좁히지 못함"이 전혀 다른 상태이기 때문이다.
    """
    src_root = material_root / SOURCE_SUBDIR
    if not src_root.is_dir():
        return None, f"소스 트리 없음({src_root})"

    values: set[str] = set()
    for fmt in {e.get("format", "") for e in entries}:
        for v in extract_values(line, fmt):
            if len(v) >= _MIN_VALUE_LEN and (
                _RE_CALL_EXPR.match(v) or _RE_IDENT.match(v)
            ):
                values.add(v)
    if not values:
        return None, "포맷에 소스와 대조할 수 있는 %s 값이 없다"

    # 같은 파일 안 후보들의 라인 번호 — 창의 끝을 정하는 데 쓴다.
    sites: dict[str, list[int]] = {}
    for e in entries:
        rel, _, ln = e["file_line"].rpartition(":")
        if ln.isdigit():
            sites.setdefault(rel, []).append(int(ln))
    for v in sites.values():
        v.sort()

    def _stop_after(file_line: str) -> int:
        rel, _, ln = file_line.rpartition(":")
        if not ln.isdigit():
            return 0
        cur, rest = int(ln), sites.get(rel, [])
        nxt = [n for n in rest if n > cur]
        return nxt[0] if nxt else 0

    cache = _SourceCache(src_root)
    hits = {
        e["file_line"]
        for e in entries
        for v in values
        if _candidate_matches(cache, e["file_line"], v, _stop_after(e["file_line"]))
    }
    shown = ", ".join(sorted(values)[:2])
    if not hits:
        return None, (
            f"런타임 값(`{shown}`)이 후보 어느 지점에도 없다 — "
            f"자료 스냅샷과 실행 바이너리가 다를 수 있다"
        )
    return sorted(hits), f"소스 대조(`{shown}`)로 {len(entries)}행 → {len(hits)}곳"
