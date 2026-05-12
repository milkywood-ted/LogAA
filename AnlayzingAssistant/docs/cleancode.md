# Clean Code 진행 현황

각 파일별 코드 위생 정리 체크리스트. 범위는 **로컬 정리**에 한정 (네이밍·주석·죽은 코드·매직넘버·타입힌트·중복된 짧은 로직 등). 파일 경계를 넘는 구조 변경은 [refactoring.md](refactoring.md) 로 분리.

## 상태 범례

- 📋 대기 — 아직 미착수
- 🔧 진행 — 일부 정리됨 / 진행 중
- ✅ 완료 — 현재 시점에서 추가 정리 사항 없음
- ➖ 제외 — 정리 대상 아님 (빈 파일, 생성 파일 등)

## 체크리스트

### core/

| 상태 | 파일 | LOC | 비고 |
| ---- | ---- | --- | ---- |
| ➖ | [core/\_\_init\_\_.py](../core/__init__.py) | 0 | 빈 파일 |
| ✅ | [core/config.py](../core/config.py) | 315 | 2026-04-19 |
| ✅ | [core/context_strategy.py](../core/context_strategy.py) | 179 | 2026-04-19 |
| ✅ | [core/db.py](../core/db.py) | 197 | 2026-04-19 |
| ✅ | [core/kb_search.py](../core/kb_search.py) | 437 | 2026-04-19 |
| ✅ | [core/llm.py](../core/llm.py) | 77 | 2026-04-19 |
| ✅ | [core/log_loader.py](../core/log_loader.py) | 226 | 2026-04-19 |
| ✅ | [core/log_refiner.py](../core/log_refiner.py) | 471 | 2026-04-19 |
| ✅ | [core/master_rule.py](../core/master_rule.py) | 264 | 2026-04-19 |
| ✅ | [core/observability.py](../core/observability.py) | 104 | 2026-04-19 |
| ✅ | [core/parser_registry.py](../core/parser_registry.py) | 71 | 2026-04-19 |
| ✅ | [core/pattern_db.py](../core/pattern_db.py) | 96 | 2026-04-19 |
| 📋 | [core/pattern_generator.py](../core/pattern_generator.py) | 319 | |
| 📋 | [core/pattern_matcher.py](../core/pattern_matcher.py) | 344 | |
| 📋 | [core/pattern_seeder.py](../core/pattern_seeder.py) | 275 | |
| 📋 | [core/pipeline.py](../core/pipeline.py) | 696 | 최대 파일 — 우선순위 높음 |
| 📋 | [core/profile.py](../core/profile.py) | 401 | |
| 📋 | [core/reflection.py](../core/reflection.py) | 184 | |
| 📋 | [core/report_generator.py](../core/report_generator.py) | 525 | |

### pages/

| 상태 | 파일 | LOC | 비고 |
| ---- | ---- | --- | ---- |
| 📋 | [pages/1_🔍_분석.py](../pages/1_🔍_분석.py) | 665 | |
| 📋 | [pages/2_📊_결과.py](../pages/2_📊_결과.py) | 200 | |
| 📋 | [pages/3_📚_케이스_업데이트_및_관리.py](../pages/3_📚_케이스_업데이트_및_관리.py) | 553 | |
| 📋 | [pages/4_🎯_문제_패턴_관리.py](../pages/4_🎯_문제_패턴_관리.py) | 510 | |
| 📋 | [pages/5_📋_분석_프로파일_관리.py](../pages/5_📋_분석_프로파일_관리.py) | 291 | |
| 📋 | [pages/6_⚙️_설정.py](../pages/6_⚙️_설정.py) | 874 | 최대 파일 — 우선순위 높음 |
| 📋 | [pages/6b_🔌_연결_확인.py](../pages/6b_🔌_연결_확인.py) | 174 | |

### ui/ · root

| 상태 | 파일 | LOC | 비고 |
| ---- | ---- | --- | ---- |
| 📋 | [ui/pattern_form.py](../ui/pattern_form.py) | 248 | |
| 📋 | [app.py](../app.py) | 46 | |

## 진행 로그

정리 완료 시 아래에 한 줄로 기록 (완료일 KST · 파일 · 요약).

<!-- 예시: 2026-04-19 · core/llm.py · 미사용 import 제거, 매직넘버 상수화 -->

- 2026-04-19 · [core/config.py](../core/config.py) · 하드코딩 URL·API 키·모델·임계값 상수화, `list[LLMProfile]` 제네릭 타입힌트, LLM/Embed 프로파일 로드 분기 `_load_profiles()` 헬퍼로 DRY, `_find_active()` 헬퍼 추출, `_default_config()` 분리, `dataclasses.replace` import 통합
- 2026-04-19 · [core/context_strategy.py](../core/context_strategy.py) · `truncate_context` 우선순위 3블록 반복을 segments 리스트+루프로 DRY, 잘림 suffix 문자열을 상수화
- 2026-04-19 · [core/db.py](../core/db.py) · 파일 상단 docstring 테이블 목록에 `master_rules`/`analysis_logs` 누락 보완, `analysis_logs.stage` SQL 주석의 stale 값 나열을 일반 설명으로 교체
- 2026-04-19 · [core/kb_search.py](../core/kb_search.py) · 미사용 상수 `DEFAULT_THRESHOLD` 제거 (주석이 이미 "안 쓰임" 명시), JSON 배열 안전 파싱 try/except 3곳을 `_parse_json_list()` 헬퍼로 DRY
- 2026-04-19 · [core/llm.py](../core/llm.py) · `timeout = cfg.llm_timeout if cfg.llm_timeout is not None else None` 동어반복 ternary 제거 (`cfg.llm_timeout` 으로 단순화)
- 2026-04-19 · [core/log_loader.py](../core/log_loader.py) · `Union[str, Path]` → PEP 604 `str | Path` (3곳 + `from typing import Union` 제거), 스트리밍 임계값 매직넘버 `50` 을 `_DEFAULT_STREAM_THRESHOLD_MB` 상수로 추출
- 2026-04-19 · [core/log_refiner.py](../core/log_refiner.py) · `Optional[X]` → `X | None` 통일 (3곳 + `from typing import Optional` 제거) — 같은 파일 내 `float | None` 과 `Optional[float]` 섞여 있던 것 일관화
- 2026-04-19 · [core/log_refiner.py](../core/log_refiner.py) · (후속) `_collapse_bursts` 의 수동 `LogLine(...)` 재구성을 `dataclasses.replace()` 로 치환 — master_rule.py 와 동일한 `cpu_id` 누락 버그 동반 수정
- 2026-04-19 · [core/observability.py](../core/observability.py) · 변경 없음 — 검토 결과 의미 있는 로컬 정리 대상 없음 (early-exit `.clear()` 는 의도적 동작, `except Exception: pass` 는 파이프라인 보호 목적)
- 2026-04-19 · [core/parser_registry.py](../core/parser_registry.py) · `_load()` 내부 지역변수 10개의 장황한 타입 힌트 블록 제거 (`description:str` PEP 8 위반 포함) → `entry[...]` 직접 접근으로 단순화, 미사용 `from typing import Any` 제거
- 2026-04-19 · [core/pattern_db.py](../core/pattern_db.py) · 변경 없음 — conn 라우팅/`_do_insert` 구조 깔끔, `int(bool(...))` 은 SQLite boolean 관용구, 컴포넌트 미발견 silent skip 은 Page 3/4 부분 입력 허용 의도
- 2026-04-19 · [core/master_rule.py](../core/master_rule.py) · `_dedup_consecutive` 의 수동 `LogLine(...)` 재구성을 `dataclasses.replace()` 로 치환 (8줄→1줄, 병합 시 `cpu_id` 누락 버그 동반 수정), `_parse_rule_response` 에서 이미 `required` 로 검증된 `comment`/`explanation` 필드의 불필요한 `.get(..., "")` 제거 — `data["..."]` 직접 접근으로 일관화
