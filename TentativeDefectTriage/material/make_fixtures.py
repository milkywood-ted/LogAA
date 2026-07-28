#!/usr/bin/env python3
"""측정 재현용 픽스처 생성기 — 설계 문서에 기록된 수치를 다시 만들 수 있게 한다.

설계 문서: `../신규 문제 분석 파이프라인 설계.md` §4 "§ 발췌 축소율 실측"

왜 있는가
---------
설계 문서 §4 의 축소율 수치(2.2배 @window=10 등)는 **실제 로그가 아니라 인덱스
기반 합성 관측집합**으로 잰 것이다. 그 합성 집합을 만든 로직이 없으면 수치를
재현·검증할 수 없어, 문서에 근거로 올린 값이 확인 불가능해진다. 이 스크립트가
그 공백을 메운다.

**실제 로그가 있으면 이 스크립트는 필요 없다** — `excerpt.py --log ... --index ...`
로 관측집합을 직접 도출하는 쪽이 항상 낫다. 이건 사외에서 실로그 없이 설계를
검토할 때 쓰는 대용물이다.

두 가지 픽스처
--------------
1. `observed`  — 합성 관측집합(`file:line` 목록). 축소율 측정의 입력.
2. `log`       — 합성 dmesg. `probe_match_rate.py` 자체의 동작 확인용.

합성 관측집합의 구성 규칙 (§4 수치를 낸 그대로)
----------------------------------------------
- `level == T2D` 행 제외 — dmesg 기대 로그가 아니다(sdp_drm-dp README §4.6).
- 남은 행에서 **상위 10개 subsystem** 선정. 실측에서 관측된 subsystem 이 10종이었던
  것에 맞춘 것이다.
- 그 subsystem 들의 고유 `file:line` 중 **20% 샘플**(seed 고정) — 실제 로그가 인덱스의
  모든 위치를 때리지는 않으므로 희석한 것이다.

이 규칙은 **실제 로그의 분포를 흉내 낸 근사**이지 실측이 아니다. 다만 축소율이
관측집합 크기에 둔감함이 확인됐으므로(§4), 이 근사로도 결론은 흔들리지 않는다.

사용법
------
    ./make_fixtures.py observed --index <11_log_index.tsv> --out observed.txt
    ./make_fixtures.py log      --index <11_log_index.tsv> --out synth_dmesg.txt

    # 설계 문서 §4 수치 재현 (FRC rheam 기준)
    ./make_fixtures.py observed --index .../analysis/sdp_frc/rheam/11_log_index.tsv \
        --out observed_noT2D.txt
    ./excerpt.py --docs-dir .../analysis/sdp_frc/rheam --observed observed_noT2D.txt --window 10
    # → 축소율 2.2배 (117,816자, § 62/226개)

의존성: Python 3 표준 라이브러리만.
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe_match_rate import EXCLUDE_LEVELS_DEFAULT, read_text  # noqa: E402

# §4 수치를 낸 값들. 바꾸면 문서의 수치와 어긋난다.
TOP_SUBSYSTEMS = 10   # 실측에서 관측된 subsystem 종수
SAMPLE_RATIO   = 5    # 1/5 = 20% 샘플
SEED           = 42


def load_rows(index_path: Path, exclude_levels: tuple[str, ...]) -> list[list[str]]:
    """인덱스를 읽어 (레벨 제외 후) 행 목록을 반환한다."""
    lines = read_text(index_path).splitlines()
    rows = [l.split("\t") for l in lines[1:] if l.strip()]
    rows = [r for r in rows if len(r) >= 5]
    kept = [r for r in rows if r[2] not in exclude_levels]
    print(f"인덱스 {len(rows)}행 → 레벨 제외({','.join(exclude_levels)}) 후 {len(kept)}행",
          file=sys.stderr)
    return kept


def make_observed(rows: list[list[str]], top_n: int, ratio: int, seed: int) -> list[str]:
    """상위 subsystem 의 고유 file:line 을 샘플링해 합성 관측집합을 만든다."""
    counts = Counter(r[4] for r in rows)
    top = {s for s, _ in counts.most_common(top_n)}
    locs = sorted({r[3] for r in rows if r[4] in top})

    rng = random.Random(seed)
    n = max(1, len(locs) // ratio)
    sample = sorted(rng.sample(locs, n))

    print(f"상위 {top_n} subsystem: {sorted(top)}", file=sys.stderr)
    print(f"고유 file:line {len(locs)}개 → {100 // ratio}% 샘플 {len(sample)}개", file=sys.stderr)
    return sample


def make_log(rows: list[list[str]], driver_tag: str, min_key_len: int, seed: int) -> list[str]:
    """인덱스의 match_key 로 합성 dmesg 를 만든다 (프로브 자체 동작 확인용).

    구성: 매칭돼야 할 라인 + 무관한 커널 라인 + 매칭되면 안 되는 드라이버 라인.
    프로브가 셋을 각각 옳게 분류하는지 보는 것이 목적이다.
    """
    keys = [r[0] for r in rows if len(r[0].strip()) >= min_key_len][:12]
    if not keys:
        sys.exit("합성에 쓸 match_key 가 없다")

    rng = random.Random(seed)
    out: list[str] = []
    t = 100.0

    for k in keys:                                    # 매칭 기대
        t += 0.5
        out.append(f"[{t:>12.6f}] {driver_tag} {k}")
    for i in range(20):                               # 무관 — 모집단에서 빠져야 함
        t += 0.3
        out.append(f"[{t:>12.6f}] usb 1-1: new high-speed USB device number {i}")
    for i in range(3):                                # 모집단이나 미매칭이어야 함
        t += 0.2
        out.append(f"[{t:>12.6f}] {driver_tag} totally unknown message variant {rng.randint(0, 99)}")

    print(f"합성 로그: 매칭기대 {len(keys)}줄 + 무관 20줄 + 미매칭 3줄", file=sys.stderr)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="측정 재현용 픽스처 생성기")
    ap.add_argument("mode", choices=["observed", "log"], help="생성할 픽스처 종류")
    ap.add_argument("--index", required=True, type=Path, help="11_log_index.tsv 경로")
    ap.add_argument("--out", type=Path, default=None, help="출력 경로 (생략 시 stdout)")
    ap.add_argument("--exclude-level", dest="exclude_levels",
                    default=",".join(EXCLUDE_LEVELS_DEFAULT),
                    help="제외할 level 값(쉼표 구분). 기본 T2D")
    ap.add_argument("--top-subsystems", type=int, default=TOP_SUBSYSTEMS)
    ap.add_argument("--sample-ratio", type=int, default=SAMPLE_RATIO,
                    help=f"1/N 샘플. 기본 {SAMPLE_RATIO}(=20%%)")
    ap.add_argument("--driver-tag", default="[S_F]", help="합성 로그에 붙일 접두 (log 모드)")
    ap.add_argument("--min-key-len", type=int, default=8)
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args()

    if not args.index.is_file():
        sys.exit(f"인덱스를 찾을 수 없음: {args.index}")

    exclude = tuple(x.strip() for x in args.exclude_levels.split(",") if x.strip())
    rows = load_rows(args.index, exclude)
    if not rows:
        sys.exit("레벨 제외 후 남은 행이 없다")

    if args.mode == "observed":
        out_lines = make_observed(rows, args.top_subsystems, args.sample_ratio, args.seed)
    else:
        out_lines = make_log(rows, args.driver_tag, args.min_key_len, args.seed)

    text = "\n".join(out_lines) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"기록: {args.out} ({len(out_lines)}줄)", file=sys.stderr)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
