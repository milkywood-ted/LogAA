# SettingsPage.jsx vs 설정.py 비교 분석

> 최초 작성: 2026-05-13 / 최종 업데이트: 2026-05-13  
> 설정.py = `AnlayzingAssistant/pages/6_⚙️_설정.py` (Streamlit)  
> SettingsPage.jsx = `frontend/src/components/SettingsPage.jsx` (React)

---

## 전체 구현 현황

| 설정.py 섹션 | SettingsPage.jsx | 비고 |
| ----------- | ---------------- | ---- |
| LLM 설정 | ✅ 구현됨 | 일부 차이 있음 (아래 참고) |
| Embedding 설정 | ✅ 구현됨 | 일부 차이 있음 (아래 참고) |
| 파이프라인 임계값 | ✅ 구현됨 | num_ctx 조회 포함 |
| 시스템 분석 지침 | ✅ 구현됨 | 기본값 초기화 포함 |
| **마스터 룰** | ❌ 미구현 | |
| **노이즈 패턴** | ❌ 미구현 | |
| **분석 이력** | ❌ 미구현 | |

---

## 미구현 항목 상세

### 1. 마스터 룰

Stage 1(L_common) → Stage 3 사이에 적용되는 전역 로그 스트림 정규화 규칙.

| 기능 | 설명 |
| ---- | ---- |
| 목록 조회 | 등록된 룰 이름·rule_type·패턴·설명 표시 |
| 룰 삭제 | 개별 삭제 버튼 |
| 자연어 입력 → LLM 생성 | 요구사항 입력 시 LLM이 룰 자동 생성 + 미리보기 후 저장 |
| 직접 입력 추가 | 이름·rule_type·regex 패턴·설명 직접 입력 |

### 2. 노이즈 패턴

Stage 1-1 파싱 후 제거할 로그 라인 패턴(regex) 관리.

| 기능 | 설명 |
| ---- | ---- |
| 목록 조회 | 패턴·설명 목록 표시 |
| 패턴 추가 | regex + 설명 입력 |
| 패턴 삭제 | 개별 삭제 버튼 |

### 3. 분석 이력

| 기능 | 설명 |
| ---- | ---- |
| 최근 50건 조회 | verdict·score·케이스명·입력해시·생성시각 표시 |
| 리포트 전문 보기 | 개별 이력 내 report_md 확장 표시 |
| 개별 삭제 | 이력 건별 삭제 |
| 전체 삭제 | 전체 이력 일괄 삭제 |

---

## LLM / Embedding 섹션 잔여 차이

| 항목 | 설정.py | SettingsPage.jsx | 상태 |
| ---- | ------- | ---------------- | ---- |
| 활성 모델 표시 | 드롭다운에 `✅ (활성)` | 모델 목록에 `✅` 표시 | ✅ 구현됨 |
| 저장 시 active 교체 | `rebuild_from_active()` | `active_llm`/`active_embed` 갱신 | ✅ 구현됨 |
| `provider` 입력 | `openai`/`anthropic` 셀렉트 | `openai`/`anthropic` 셀렉트 | ✅ 구현됨 |
| `report_temperature` | 슬라이더 0~1, step 0.05 | 슬라이더 0~1, step 0.05 | ✅ 구현됨 |
| `base_url` 표시 | caption으로 표시 | ❌ 없음 | ❌ 미구현 |
| `api_key` 표시 | 마스킹하여 표시 | ❌ 없음 | ❌ 미구현 |
| 연결 확인 응답 상세 | LLM 응답 텍스트 / 임베딩 벡터 차원 표시 | ✅/❌ 아이콘만 | ❌ 미구현 |

---

## API 응답 형식 (참고)

프론트는 `backend/main.py` (포트 8000)를 호출하며, 이 레이어가 내부 AnalyzingAssistant API 응답을 변환하여 내려줌. 불일치 없음.

| 엔드포인트 | backend/main.py 반환 | 프론트 기대값 |
| --------- | ------------------- | ------------ |
| `/llm/profiles`, `/embedding/profiles` | `{"profiles": [{"name": "..."}]}` | `data.profiles` ✅ |
| `/llm/models`, `/embedding/models` | `{"models": [...]}` | `data.models` ✅ |
| `/llm/check`, `/embedding/check` | `{"connected": true, ...}` | `data.connected` ✅ |
