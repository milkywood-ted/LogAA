#!/usr/bin/env python3
"""관측된 `file:line` 을 감싸는 **함수만** 소스에서 뽑는다.

설계 문서: `Document/신규 문제 분석 파이프라인/신규 문제 분석 파이프라인 설계.md` §4

왜 이것만 하나 (벌크 소스 주입은 여전히 안 한다)
------------------------------------------------
Phase 3 에서 소스 접근을 미도입으로 확정했다. 근거는 자료가 기록한 **소스 오독
사고 5건**(파일명으로 역할 오분류, `return 0` 스텁을 동작으로 기록, IRQ 핸들러
위치 오판, 선언만 보고 호출 추정, 인코딩 때문에 파일이 안 보여 "없다"고 기록)
이었다 — 검증 없이 원시 C 를 벌크로 주입하면 같은 실패 모드에 재노출된다.

그 논거는 **"LLM 이 소스를 뒤져 찾게 하는 것"** 에 대한 것이다. 여기서 하는 일은
다르다:

- 찾지 않는다. **로그가 어디서 찍혔는지 이미 안다**(로그인덱스가 `file:line` 을 준다).
- 그 지점을 감싸는 함수 하나만 뽑는다. 양이 관측 위치 수로 **묶여 있다**.
- 목적이 좁다 — 레시피(`02_triage_recipe.md` §1-3)가 말하는 "**무슨 상태에서
  찍히는가**"(감싼 함수·if 조건)를 보는 것.

즉 "소스를 읽어라" 가 아니라 "이 로그를 낸 코드가 이것이다" 를 붙여 주는 것이다.

함수 경계를 추측하지 않는다
---------------------------
자료 README §4-1 이 기록한 실제 오판: `misc_register` 호출부가 probe 본문이 아니라
그 앞에 텍스트로 위치한 **비동기 워크 함수 안**에 있어 "정상" 으로 오판했다가
정정했다. 라인 번호 순서만으로 함수 소속을 단정하면 안 된다.

그래서 여는 중괄호부터 **짝을 맞춰** 함수 범위를 구하고, 대상 라인이 그 범위 밖이면
**"함수 밖"으로 보고한다** — 가장 가까운 함수에 억지로 귀속시키지 않는다.

의존성: Python 3 표준 라이브러리만.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# 소스 트리 위치. 분석자료 저장소 안에 함께 있다.
SOURCE_SUBDIR = "tztv-media-sec"

# 함수 정의 시작 후보: 열 0 에서 시작하고 `(` 를 포함하며 `;` 로 끝나지 않는 줄.
# 전처리기·주석·레이블은 제외한다. 완벽한 C 파서가 아니라 **커널 소스 관례**에
# 기대는 휴리스틱이며, 확신이 서지 않으면 결과를 내지 않는 쪽으로 동작한다.
_RE_FUNC_HEAD = re.compile(r"^[A-Za-z_][A-Za-z0-9_ \t\*\(\),]*\(")

_MAX_FUNC_LINES = 400        # 이보다 길면 함수 추정이 틀렸을 가능성이 높다


@dataclass
class SourceSlice:
    path: str                # 인덱스 표기 그대로 (예: sdp_frc/rheam/sdp_frc_ioctl.c)
    line: int                # 관측된 라인
    func_name: str           # 추정된 함수명 ("" 면 특정 실패)
    start: int               # 함수 시작 라인 (1-based)
    end: int                 # 함수 끝 라인
    text: str
    note: str = ""           # 특정 실패·경계 밖 등 주의사항

    @property
    def ok(self) -> bool:
        return bool(self.text) and not self.note


def _read(p: Path) -> list[str]:
    """바이트로 읽고 관용적으로 디코드한다.

    소스에 ISO-8859/EUC-KR 이 섞여 있어 strict 디코드는 실패한다 — 자료 README
    §4.1 이 기록한 "plain grep 이 파일을 통째로 건너뛰어 '존재하지 않는다'고
    잘못 기록한" 사고와 같은 원인이다.
    """
    return p.read_bytes().decode("utf-8", errors="replace").splitlines()


def _find_enclosing(lines: list[str], target: int) -> tuple[str, int, int, str]:
    """`target`(1-based)을 감싸는 함수 범위를 중괄호 짝맞춤으로 구한다.

    반환: (함수명, 시작, 끝, 주의). 특정 실패 시 함수명은 "".
    """
    idx = target - 1
    if not (0 <= idx < len(lines)):
        return "", 0, 0, f"라인 {target} 이 파일 범위 밖이다(총 {len(lines)}줄)"

    # 대상 라인 위쪽에서 함수 머리 후보를 가까운 것부터 훑는다.
    for head in range(idx, -1, -1):
        line = lines[head]
        if not _RE_FUNC_HEAD.match(line):
            continue
        if line.lstrip().startswith(("#", "//", "/*", "*")):
            continue
        if line.rstrip().endswith(";"):        # 선언(프로토타입)이지 정의가 아니다
            continue

        # 여는 중괄호를 찾는다 — 같은 줄이거나 다음 몇 줄 안.
        open_at = -1
        for k in range(head, min(head + 6, len(lines))):
            if "{" in lines[k]:
                open_at = k
                break
        if open_at == -1:
            continue

        # 짝을 맞춰 끝을 구한다.
        depth = 0
        end_at = -1
        for k in range(open_at, min(open_at + _MAX_FUNC_LINES, len(lines))):
            depth += lines[k].count("{") - lines[k].count("}")
            if depth <= 0:
                end_at = k
                break
        if end_at == -1:
            continue

        if open_at <= idx <= end_at:
            m = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", line)
            name = m.group(1) if m else "(이름 미상)"
            return name, head + 1, end_at + 1, ""

        # **여기가 핵심**: 가장 가까운 함수 머리를 찾았지만 대상 라인이 그 함수의
        # 중괄호 범위 밖이다. 라인 번호가 가깝다는 이유로 귀속시키면 자료 README
        # §4-1 이 기록한 오판을 되풀이하게 된다.
        return "", 0, 0, (
            f"라인 {target} 은 직전 함수 `{line.strip()[:50]}`(:{head+1}~{end_at+1}) "
            f"의 범위 **밖**이다 — 함수에 귀속시키지 않는다"
        )

    return "", 0, 0, f"라인 {target} 을 감싸는 함수 정의를 찾지 못했다"


def slice_functions(
    material_root: Path,
    locations: list[tuple[str, int]],
    *,
    budget_chars: int = 20_000,
) -> tuple[list[SourceSlice], dict]:
    """관측 위치들을 감싸는 함수를 뽑는다.

    같은 함수에 여러 위치가 들어오면 한 번만 낸다 — 관측 라인은 모두 표기한다.
    """
    src_root = material_root / SOURCE_SUBDIR
    out: list[SourceSlice] = []
    seen: set[tuple[str, int, int]] = set()
    stats = {"requested": len(locations), "emitted": 0, "failed": 0,
             "chars": 0, "skipped_over_budget": 0, "notes": []}

    if not src_root.is_dir():
        stats["notes"].append(f"소스 트리를 찾지 못했다: {src_root}")
        return out, stats

    used = 0
    for rel, line in sorted(set(locations)):
        p = src_root / rel
        if not p.is_file():
            stats["failed"] += 1
            stats["notes"].append(f"`{rel}` 소스 파일 없음")
            continue

        lines = _read(p)
        name, start, end, note = _find_enclosing(lines, line)
        if note:
            stats["failed"] += 1
            stats["notes"].append(f"`{rel}:{line}` — {note}")
            continue
        if (rel, start, end) in seen:
            continue
        seen.add((rel, start, end))

        body = "\n".join(f"{start + i:>5}| {t}" for i, t in enumerate(lines[start - 1:end]))
        if used + len(body) > budget_chars:
            stats["skipped_over_budget"] += 1
            continue

        used += len(body)
        out.append(SourceSlice(path=rel, line=line, func_name=name,
                               start=start, end=end, text=body))

    stats["emitted"] = len(out)
    stats["chars"] = used
    return out, stats


def format_slices(slices: list[SourceSlice]) -> str:
    """프롬프트에 넣을 형태로 렌더한다."""
    if not slices:
        return ""
    parts: list[str] = []
    for s in slices:
        parts.append(
            f"[{s.path}:{s.start}-{s.end}  함수 `{s.func_name}`  "
            f"(관측 라인 {s.line})]\n{s.text}"
        )
    return "\n\n".join(parts)
