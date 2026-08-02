#!/usr/bin/env python3
"""자료 규약(contract) — 소비자 코드가 분석자료에 대해 무엇을 가정하는지 한곳에 모은다.

설계 문서: `../신규 문제 분석 파이프라인 설계.md` §4 "자료 규약"

왜 있는가
---------
`excerpt.py` 등 소비자 도구는 분석자료의 여러 규약에 의존한다 — TSV 컬럼 순서,
통짜 유지 파일명, 레벨 어휘, 인용 형식 등. 이 가정들이 코드에 하드코딩돼 있으면
자료가 갱신될 때 **조용히 나빠진다**: 에러가 아니라 "발췌가 덜 되거나 엉뚱하게
되는" 형태로 나타나 결과를 보기 전엔 모른다. 실제로 자료 커밋 `d4012348f`
(인덱스 904→870행)를 알아챈 것은 우연히 측정 재현을 시도했기 때문이었다.

해결: 가정을 **선언**으로 끌어내고(이 모듈), 자료가 스스로 선언하게 하며
(`MANIFEST.json`), 선언과 실제가 맞는지 검증한다(`verify_material.py`).

세 부류로 나뉜다 (설계 문서 §4)
-------------------------------
① **설정값** — 이름·목록·어휘. 매니페스트로 완전히 넘어간다. 자료가 바꾸면
   소비자는 자동으로 따라가며 사람 개입이 필요 없다.
② **형식 파싱 알고리즘** — 인용 정규식, 헤딩 파싱. 소비자 코드에 남는다.
   `schema_version` 이 방어한다 — 자료가 구조를 바꾸면 버전을 올리고, 소비자는
   "모르는 스키마"로 **크게 실패**한다(조용한 오파싱 대신).
③ **소비자 정책** — window, fallback 규칙, 예산 목표. 우리 것이며 매니페스트에
   넣지 않는다. 자료 세트에 넣으면 정책까지 얼어붙어 튜닝이 막힌다.

이 모듈이 다루는 것은 ①과 ②의 버전 게이트다.

매니페스트가 없으면
-------------------
내장 기본값으로 동작하되 **경고한다**. 자료 저장소를 고칠 수 있는지 여부에
파이프라인이 막히지 않도록 한 것이다.

의존성: Python 3 표준 라이브러리만 (자료 저장소 `analysis/tools` 관례와 동일).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST_NAME = "MANIFEST.json"

# 이 소비자 코드가 파싱할 수 있는 자료 스키마 버전.
# 자료가 인용 형식·문서 구조 같은 **구조**를 바꾸면 자료 쪽에서 이 번호를 올리고,
# 소비자는 아래 목록에 없는 버전을 만나면 실패한다 — 조용히 잘못 파싱하지 않기 위해.
SUPPORTED_SCHEMA_VERSIONS = (1,)

# 매니페스트가 없을 때 쓰는 값. 2026-07-28 기준 sdp_frc·sdp_drm-dp 를 관찰해 정했다.
DEFAULTS: dict = {
    "schema_version": 1,
    "log_index": {
        "columns": ["match_key", "format", "level", "file:line", "subsystem"],
        # dmesg 기대 로그가 아닌 레벨. CONFIG_T2D_DEBUGD 게이트라 컴파일 자체가
        # 빠질 수 있다 (sdp_drm-dp README §4.6).
        "excluded_levels": ["T2D"],
        # 관찰된 레벨 어휘. 여기 없는 값이 나오면 "모르는 어휘"로 보고한다 —
        # 새 모듈이 다른 체계를 쓸 수 있기 때문이다(FRC 와 DP 도 서로 다르다).
        "known_levels": ["ALERT", "ERROR", "WARN", "DEFAULT", "INFO", "COMMENT", "T2D"],
    },
    "docs": {
        "glob": "*.md",
        # 기계 생성 중간 산출물 접두. DP 에만 존재한다.
        "skip_prefix": "_",
        # 발췌하지 않고 통짜로 유지할 문서 — 큐레이션된 의미 해석이라 쪼개면
        # 맥락이 깨진다. 작아서 예산 부담도 없다.
        "always_whole": ["10_summary_and_findings.md", "11_log_triage.md"],
        # 번호 슬롯 관례. 여기 없는 번호의 문서가 나오면 "모르는 문서"로 보고한다.
        "known_slots": ["00", "01", "02", "03", "04", "05", "06",
                        "07", "08", "09", "10", "11", "12"],
    },
    # ── 개념 계층 ─────────────────────────────────────────────────────────
    # § 발췌는 "관측된 file:line 을 인용하는 섹션" 만 고르는데, **개념을 설명하는
    # 문서는 코드를 인용하지 않는다**. 그래서 구현 상세(02~09, 인용 57~171건)는
    # 잘 들어오고 개념은 하나도 안 들어온다 — 실측에서 이 구멍이 확인됐다
    # (01_architecture_composition 은 인용이 4건뿐이라 사실상 절대 안 걸린다).
    #
    # 결과적으로 "디바이스가 뭘 하는 물건인지" 모르는 채로 로그를 읽게 되고,
    # 로그의 의미·상관관계와 얽힌 논리를 못 짚는다. 그래서 인용과 무관하게
    # **항상 주입**하는 계층을 둔다.
    "conceptual": {
        # 저장소 루트의 용어집. module_root 밖이라 지금까지 로드된 적이 없다.
        # `{module}` 은 module_root 의 디렉토리명으로 치환된다.
        "glossary": "{module}_specific_information.md",
        # module_root 기준 경로. **번호 슬롯을 고정하지 않고 이름으로 찾는다**
        # (`resolve_doc` 참조) — 같은 내용의 문서가 모듈마다 다른 번호를 달고
        # 있기 때문이다. 없으면 건너뛰고 그 사실을 보고한다.
        "module_docs": [
            "log_analysis/*_log_grammar.md",
            "log_analysis/*_context_and_correlation.md",
        ],
        # 칩 디렉토리 내 파일. 인용이 적어 § 발췌에 안 걸리는 것들.
        "chip_docs": ["01_architecture_composition.md"],
        # 크기 상한을 두지 않는다 (2026-08-02 확정).
        #
        # 전에는 32,000자 상한을 두고 넘으면 경고했다. 그런데 이 계층은 **자르지
        # 않는다** — 용어집을 중간에서 끊으면 필요한 항목이 사라져 쓸모가 없어지기
        # 때문이다. 자르지 않는 값에 대한 경고는 트립와이어일 뿐인데, 그 숫자에
        # 근거가 없었다: DP 는 개념 계층이 50.3k자(용어집 하나가 22.9k)인데도
        # 프롬프트 전체는 99.9k/132k 토큰으로 **여유가 있다**. 멀쩡한 구성에서
        # 경고가 뜨면 진짜 초과와 구분되지 않는다 — 예산 합산 오경보(§4)와 같은
        # 부류다.
        #
        # 진짜 제약은 입력 예산이고 그것은 `analyze.budget_summary()` 가 본다.
        # 여기서는 크기를 **보고만** 하고 판정하지 않는다.
    },

    # 모듈별 dmesg 식별 정규식. **모집단(분모) 정의에만 쓴다** — 매칭률 프로브·
    # 픽스처·검증이며, 분석 경로는 이것으로 로그를 거르지 않는다.
    #
    # 어느 모듈이든 이 값은 **하한**이다. 두 자료가 같은 사실을 기록한다:
    #   FRC `log_analysis/01 §1` — "`pr_err`/`printk`/`dev_err` 등 커널 표준
    #     매크로는 `[S_F]` prefix 가 없다. 주로 **에러/부팅**."
    #   DP  `log_analysis/01 §3` — `printk(KERN_x)` 109~186건, `pr_err` 직접
    #     호출이 접두 없이 나간다.
    # 접두 없는 채널은 **어떤 정규식으로도 식별할 수 없다**(문자열 자체가 유일
    # 단서다). 그래서 모집단 기준 매칭률은 실제보다 좁은 표본 위의 값이다.
    #
    # 다만 DP 는 자료가 지목한 **구별 가능한** 두 채널을 놓치고 있었다
    # (`log_analysis/01 §3.1`·`§3.2`):
    #   - `SDP_DP_CHK` → `DP_ERROR[0x…`. pontusm/rheam/rheal 은 `pr_err` 라
    #     접두가 없고 oscarp 만 `[DRM-DP:E]` 가 붙는다 — 접두를 기대하면 3칩에서 놓친다.
    #   - `SDP_DRM_ERROR` → `[drm:<함수명>] *ERROR* …`. 프레임워크 `.c` 에 162건이며
    #     **ioctl·프로퍼티·GEM 실패가 대부분 이 형태**다. 자료가 명시한다:
    #     "`[DRM-DP]` 로 필터링하면 프레임워크 오류를 통째로 놓친다."
    # `*ERROR*` 는 DRM 프레임워크 공통이라 dp 전용이 아니다 — 분모가 넓어지는
    # 대신 자료가 "트리아지에서 `*ERROR*` 도 함께 잡아야 한다"(`02 §2`)고 지시한
    # 범위를 따른다.
    "driver_tags": {
        "sdp_frc":     r"\[S_F\]",
        # `\[DRM-DP` 는 oscarp 의 `[DRM-DP:I]` 변형까지 함께 잡는다.
        "sdp_drm-dp":  r"\[DRM-DP|\*ERROR\*|DP_ERROR\[0x",
    },
}


@dataclass
class Contract:
    """자료 규약 — 매니페스트에서 읽었거나 기본값."""
    schema_version: int
    columns: list[str]
    excluded_levels: list[str]
    known_levels: list[str]
    doc_glob: str
    skip_prefix: str
    always_whole: list[str]
    known_slots: list[str]
    driver_tags: dict
    conceptual: dict                 # 개념 계층 선언 (glossary/module_docs/chip_docs)
    source: str                      # "manifest" | "defaults"
    warnings: list[str] = field(default_factory=list)

    def driver_tag_for(self, module_root: Path | str) -> str | None:
        """모듈 경로에서 dmesg 접두를 고른다. 모르면 None."""
        name = Path(module_root).name
        return self.driver_tags.get(name)

    def is_always_whole(self, filename: str) -> bool:
        return filename in self.always_whole

    def slot_of(self, filename: str) -> str | None:
        """파일명 앞 2자리 슬롯 번호. 규약을 안 따르면 None."""
        head = filename[:2]
        return head if head.isdigit() else None


def resolve_doc(base: Path, pattern: str) -> list[Path]:
    """문서를 **번호 슬롯이 아니라 이름으로** 찾는다. 정렬된 실재 파일 목록을 낸다.

    왜 이름으로 찾나
    ----------------
    번호 슬롯 관례는 **칩 디렉토리(00~12)에서만** 안정적이다. `log_analysis/`
    해석 층은 모듈마다 문서 수가 달라 **같은 내용이 다른 번호를 단다**:

    | 내용 | sdp_frc | sdp_drm-dp |
    | --- | --- | --- |
    | 로그 문법 | `01` | `01` |
    | 상태 모델 | `04` | **`03`** |
    | 상관·문맥 | `05` | **`04`** |
    | 크로스모듈 로그 엣지 | `08` | **`05`** |

    번호를 경로에 박아 두면 **자료에 있는데 없다고 보고한다** — 2026-08-02 자료
    갱신(DP `log_analysis/` 신설)에서 실제로 그렇게 됐다. DP 의 상관 문서
    (`04_context_and_correlation.md`, 12.2k자)가 "없음"으로 빠졌고, 그 자료가
    사내 실측에서 지적된 "로그의 의미·상관관계를 못 잡는다"에 정확히 대응하는
    것이었다. Q3(상태 추정)·Q6(모듈 경계)도 근거가 생겼는데 계속 생략됐다.

    번호 없는 경로가 들어오면 그대로 존재만 확인한다 — 칩 디렉토리 문서
    (`10_summary_and_findings.md` 등)는 번호가 곧 규약이라 바꿀 이유가 없다.
    """
    if "*" not in pattern and "?" not in pattern:
        p = base / pattern
        return [p] if p.is_file() else []
    return sorted(p for p in base.glob(pattern) if p.is_file())


def load_contract(material_dir: Path) -> Contract:
    """자료 디렉토리에서 규약을 읽는다.

    `MANIFEST.json` 을 위로 올라가며 찾는다(칩 디렉토리에서 시작해도 되도록).
    없으면 내장 기본값을 쓰되 경고를 남긴다 — 자료 저장소를 고칠 수 있는지에
    파이프라인이 막히지 않게 하려는 것이다.
    """
    warnings: list[str] = []
    data = None
    found_at = None

    for parent in [material_dir, *material_dir.parents]:
        cand = parent / MANIFEST_NAME
        if cand.is_file():
            try:
                data = json.loads(cand.read_text(encoding="utf-8"))
                found_at = cand
            except Exception as e:
                warnings.append(f"{cand} 파싱 실패({e}) — 기본값으로 진행한다")
                data = None
            break

    if data is None:
        if not warnings:
            warnings.append(
                f"{MANIFEST_NAME} 를 찾지 못해 내장 기본값을 쓴다 — 자료가 규약을 "
                f"바꾸면 소비자가 알아채지 못할 수 있다"
            )
        data = DEFAULTS
        source = "defaults"
    else:
        source = f"manifest ({found_at})"

    ver = int(data.get("schema_version", 0))
    if ver not in SUPPORTED_SCHEMA_VERSIONS:
        # 여기서 죽이지 않고 호출자가 판단하게 한다 — verify 는 에러로 보고하고,
        # 파이프라인은 중단할지 경고만 할지 정책적으로 정할 수 있다.
        warnings.append(
            f"schema_version={ver} 는 이 소비자가 모르는 버전이다"
            f"(지원: {SUPPORTED_SCHEMA_VERSIONS}). 자료 구조가 바뀌었다면 "
            f"인용·헤딩 파서를 갱신해야 한다"
        )

    li = {**DEFAULTS["log_index"], **data.get("log_index", {})}
    dc = {**DEFAULTS["docs"], **data.get("docs", {})}

    return Contract(
        schema_version  = ver,
        columns         = list(li["columns"]),
        excluded_levels = list(li["excluded_levels"]),
        known_levels    = list(li["known_levels"]),
        doc_glob        = dc["glob"],
        skip_prefix     = dc["skip_prefix"],
        always_whole    = list(dc["always_whole"]),
        known_slots     = list(dc["known_slots"]),
        driver_tags     = dict(data.get("driver_tags", DEFAULTS["driver_tags"])),
        conceptual      = {**DEFAULTS["conceptual"], **data.get("conceptual", {})},
        source          = source,
        warnings        = warnings,
    )


def conceptual_docs(
    module_root: Path, chip_dir: Path, c: Contract,
) -> tuple[list[tuple[str, str]], list[str]]:
    """개념 계층 문서를 모은다. 반환: ([(라벨, 본문)], [누락 안내]).

    § 발췌와 달리 **인용 여부를 보지 않는다.** 이 문서들은 코드를 인용하지 않아
    발췌 조인에 절대 걸리지 않지만, 없으면 로그의 의미·상관관계와 구조를 이해할
    수 없다.

    누락은 오류가 아니라 **보고 대상**이다 — 모듈마다 자료 구성이 다르므로
    없는 것을 있는 척하면 안 된다.

    문서는 `resolve_doc` 로 **이름으로** 찾는다. 번호를 박아 두면 자료에 있는
    문서를 없다고 보고하게 된다(그 함수의 설명 참조).
    """
    conf = c.conceptual
    out: list[tuple[str, str]] = []
    missing: list[str] = []

    def _read(p: Path) -> str:
        return p.read_bytes().decode("utf-8", errors="replace")

    # 용어집 — module_root 밖(저장소 루트)에 있으므로 위로 올라가며 찾는다.
    gl_name = str(conf.get("glossary", "")).replace("{module}", module_root.name)
    if gl_name:
        for parent in [module_root, *module_root.parents]:
            cand = parent / gl_name
            if cand.is_file():
                out.append((f"{gl_name} (용어·도메인 사실)", _read(cand)))
                break
        else:
            missing.append(f"용어집 `{gl_name}` 을 찾지 못했다 — 도메인 용어 해석이 어려워진다")

    for pat in conf.get("module_docs", []):
        found = resolve_doc(module_root, pat)
        if not found:
            missing.append(f"`{module_root.name}/{pat}` 에 맞는 문서 없음")
            continue
        # 하나를 기대한 패턴에 여러 개가 걸리면 **전부 넣고 알린다** — 임의로
        # 하나를 고르면 나머지를 조용히 버리게 되고, 자료가 문서를 쪼갠 경우
        # 그 사실을 영영 모른다. 크기 부담은 입력 예산 판정이 잡는다.
        if len(found) > 1:
            missing.append(
                f"`{pat}` 에 {len(found)}개가 걸렸다(전부 주입): "
                + ", ".join(p.name for p in found)
            )
        for p in found:
            out.append((f"{p.relative_to(module_root)}", _read(p)))

    for name in conf.get("chip_docs", []):
        p = chip_dir / name
        if p.is_file():
            out.append((f"{name} (구조 전체상)", _read(p)))
        else:
            missing.append(f"`{chip_dir.name}/{name}` 없음")

    # 크기는 판정하지 않는다 — 자르지 않는 계층에 임의 상한 경고를 두면 예산이
    # 멀쩡한데도 경고가 뜬다(DEFAULTS 의 `conceptual` 주석 참조). 실제 제약인
    # 입력 예산은 `analyze.budget_summary()` 가 본다.
    return out, missing


def write_default_manifest(path: Path) -> None:
    """현재 기본값을 매니페스트 파일로 내보낸다.

    자료 저장소에 규약을 선언해 넣을 때의 출발점으로 쓴다 — 소비자가 무엇을
    가정하고 있는지가 그대로 파일이 되므로, 자료 쪽에서 검토·수정하면 된다.
    """
    path.write_text(json.dumps(DEFAULTS, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
