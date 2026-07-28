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
    # 모듈별 dmesg 접두. `\[DRM-DP` 는 oscarp 의 `[DRM-DP:I]` 변형까지 함께 잡는다.
    "driver_tags": {
        "sdp_frc":     r"\[S_F\]",
        "sdp_drm-dp":  r"\[DRM-DP",
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
        source          = source,
        warnings        = warnings,
    )


def write_default_manifest(path: Path) -> None:
    """현재 기본값을 매니페스트 파일로 내보낸다.

    자료 저장소에 규약을 선언해 넣을 때의 출발점으로 쓴다 — 소비자가 무엇을
    가정하고 있는지가 그대로 파일이 되므로, 자료 쪽에서 검토·수정하면 된다.
    """
    path.write_text(json.dumps(DEFAULTS, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
