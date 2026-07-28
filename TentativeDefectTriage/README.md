# TentativeDefectTriage

유사 이력이 없는 **신규 defect** 를 분석해, 여러 "분석 전문가"의 관점을 **잠정적(tentative)** 참고자료로 산출하는 파이프라인.

> **설계 문서가 정본이다** — [`Document/신규 문제 분석 파이프라인/신규 문제 분석 파이프라인 설계.md`](../Document/신규%20문제%20분석%20파이프라인/신규%20문제%20분석%20파이프라인%20설계.md). 이 README 는 코드 위치 안내일 뿐이다.

## 무엇이 다른가 (vs `AnalyzingAssistant_v2`)

`AnalyzingAssistant_v2` 는 **유사 케이스가 있을 때** 그것을 찾아 판정한다. 이 파이프라인은 **케이스가 없을 때** 돈다 — 그래서 대체재가 아니라 나란히 존재하는 별개 시스템이다(`v3` 가 아닌 이유).

핵심 차이는 목적이다. **단일 확정 진단을 내지 않는다.** 신규 문제는 정답 데이터가 없고 디버깅은 LLM 이 갖지 못한 경험·통찰이 크게 관여하는 영역이므로, 하나로 단정하는 대신 사용자가 지정한 전문가들이 각자 독립 분석한 결과를 **승자 선정 없이 나열**하고 최종 판단은 사람이 한다. 이름의 `Tentative` 가 그 원칙이다.

## 구조

```text
TentativeDefectTriage/
├── config/
│   └── material.yaml     # 분석자료 경로 + 프로파일 → module_root 매핑
└── material/             # 분석자료를 읽는 도구 묶음 (상호 import — 쪼개지 말 것)
    ├── excerpt.py            # § 발췌 추출기 — Stage 2 의 실제 부품
    ├── material_contract.py  # 자료 규약 선언 로드 (MANIFEST.json 또는 기본값)
    ├── verify_material.py    # 자료 검증기 — 갱신 시 가정이 깨졌는지 확인
    ├── probe_match_rate.py   # 로그인덱스 매칭률 측정
    ├── make_fixtures.py      # 측정 재현용 합성 픽스처
    └── material_baseline.json
```

사용법·측정 결과는 [`material/README.md`](material/README.md).

## 자료를 갱신했다면

```sh
python3 material/verify_material.py --material-root <자료 저장소>
```

자료는 계속 갱신되며 갱신은 대체로 개선이다. 다만 **모르는 사이 소비자 가정이 깨지는 것**을 막아야 하므로, pull 후 이 검증기를 돌린다. 종료 코드 `0` 이상없음 / `1` 경고(사람 판단) / `2` 오류(구조 위반).

변화가 의도된 것이면 `--update-baseline` 으로 받아들인다.

## 현재 상태

**Phase 0 (스캐폴딩) 진행 중.** Stage 2 의 § 발췌 부품과 자료 검증 체계만 구현돼 있고 파이프라인 본체(Stage 1 정제기, Stage 2 LLM 호출, Stage 3 조립)는 없다. 진행 상황은 설계 문서 §9.
