# 분석 페이지 프론트엔드 구현 할일

> 기준일: 2026-05-13  
> 소스: `analyze_page_spec.md` (설정.py 분석) + 현재 frontend 코드

---

## 현재 상태 요약

| 컴포넌트 | 현재 상태 |
| -------- | --------- |
| `Sidebar` | Puller 선택 + Defect fetch + 케이스 목록 ✅ |
| `InfoPanel` | 선택된 케이스 정보(id·설명·파일) 표시 ✅ |
| `ProfileSelector` | 분석 프로파일 선택 UI ✅ |
| `AnalyzeHeader` | "분석 시작" 버튼 UI만 있음 — API 미연결 ❌ |
| `ProgressPanel` | Stage 이름 하드코딩, 실제 진행 미연결 ❌ |
| `ResultPanel` | 결과 표시 틀만 있음 — report를 text로만 표시 ❌ |

**가장 큰 문제: "분석 시작" 버튼이 실제로 아무 API도 호출하지 않음.**

---

## 할일 목록

### 🔴 P0 — 분석 실행 연결 ✅ 완료 (미검증)

#### 1. 분석 API 호출 연결 ✅

- `submitAnalysis()` → `POST /api/defect/analyze` → `job_id` 즉시 반환
- `App.jsx` `handleAnalyze()` 에서 호출, `selectedProfiles` 함께 전달

#### 2. 분석 진행 폴링 연결 ✅

- `pollAnalysis(jobId)` → `GET /api/defect/analyze/{job_id}` 2초 간격 폴링
- `ProgressPanel`: progress bar 기본 표시, "상세 프로그레스" 토글로 stage별 상태 확인
- idle 상태에서 패널 숨김, 분석 시작 후에만 표시

#### 3. 분석 결과 표시 연결 ✅

- `ResultPanel`: verdict + 아이콘 / score / 매칭 케이스 / 매칭 패턴 / report_md 표시
- report_md는 현재 pre-wrap 텍스트 표시 (마크다운 렌더링은 P3로 이관)

> ⚠️ 실제 동작 미검증. 테스트 시 확인 필요:
>
> - AnalyzingAssistant 서버 실행 여부
> - `backend/config.yaml` url 설정
> - stage 문자열 형식(`"이름 — 상세"`) 매핑 동작

---

### 🟠 P1 — 핵심 입력 옵션 (분석.py 동등 수준)

#### 4. ~~문제 설명 입력란~~ — 제외

- 가져온 케이스의 description을 그대로 사용하기로 결정

#### 5. 분석 프로파일 선택 — ✅ 완료

- `ProfileSelector` 컴포넌트 구현, `GET /api/profiles` 백엔드 연결 완료

#### 6. 임시 키워드 필터

- **현재**: 없음
- **해야 할 것**: 쉼표 구분 text input
- **연결**: `input_keywords` → `analyzeDefect()` 파라미터

---

### 🟡 P2 — 고급 설정 (Stage 1 옵션)

#### 7. 파서 선택 (Stage 1-1)

- **현재**: 없음
- **해야 할 것**: 파서별 체크박스 (접기/펼치기 가능한 영역)
- **필요한 백엔드 API**: `GET /api/parsers` — 신규 구현 필요 (`PARSER_DEFS` from `core.parser_registry`)
- **연결**: `parser_names` → `analyzeDefect()` 파라미터

#### 8. 시간 구간 필터 (Stage 1-3)

- **현재**: 없음
- **해야 할 것**: 라디오 3가지 모드 (필터 없음 / 앵커 패턴 / 타임스탬프 직접 지정)
- **연결**: `anchors` → `analyzeDefect()` 파라미터
- **참고**: `window_before/after`, `ts_start/end`는 현재 백엔드 API 파라미터 없음 → 추가 필요

#### 9. 케이스 직접 지정 (Stage 2 스킵)

- **현재**: 없음
- **해야 할 것**: KB 케이스 셀렉트박스 + 선택 시 description·패턴 수·키워드 미리보기
- **필요한 백엔드 API**: `GET /api/kb-cases` — 신규 구현 필요 (DB `cases` 테이블)
- **연결**: `pinned_case_name` → `analyzeDefect()` 파라미터

#### 10. 파일 선별 조건 (Stage 1-4) 및 Burst Collapse 임계값

- **현재**: 없음
- **해야 할 것**: textarea (regex AND 조건) + number input
- **연결**: 현재 백엔드 API에 파라미터 없음 → 추가 필요

---

### 🟢 P3 — 부가 기능

#### 11. 분석 프로파일 관리 페이지

- **현재**: Streamlit `5_📋_분석_프로파일_관리.py`에 완전 구현됨 (프로파일 CRUD + 사전지식 관리)
- **해야 할 것**: React 별도 페이지로 포팅 (선택은 이미 구현, 관리만 추가)
- **범위**: 프로파일 목록·추가·수정·삭제 / 사전지식(SQLite·ChromaDB) CRUD

#### 12. 선별 및 필터만 진행

- **현재**: 없음
- **해야 할 것**: "필터만" 보조 버튼 — LLM 미호출, Stage 1 결과만 표시
- **필요한 백엔드 API**: 현재 없음, 신규 구현 필요

#### 13. ResultPanel 개선

- "파일로 저장" 버튼 실제 동작 구현
- `report_md` 마크다운 렌더링 (현재 plain text)
- 분석 중 stage별 상세 로그(detail) 표시

---

## 필요한 신규 백엔드 API 요약

| API | 메서드 | 설명 | 우선순위 |
| --- | ------ | ---- | -------- |
| `/api/profiles` | GET | 분석 프로파일 목록 | ✅ 완료 |
| `/api/kb-cases` | GET | KB 케이스 이름 목록 | P2 |
| `/api/parsers` | GET | 사용 가능한 파서 목록 | P2 |
| `/api/defect/analyze` AnalyzeRequest 확장 | — | `window_before`, `window_after`, `ts_start`, `ts_end`, `file_conditions`, `burst_threshold` 파라미터 추가 | P2 |
| `/api/defect/filter` | POST | 선별 및 필터만 진행 | P3 |

---

## 컴포넌트별 변경 범위

| 파일 | 변경 내용 |
| ---- | --------- |
| `api.js` | `analyzeDefect()`, `pollAnalysis()` 추가 (P0) / `getKbCases()`, `getParsers()` 추가 (P2) |
| `App.jsx` | 분석 실행·폴링 로직, analysisState에 result 저장 |
| `AnalyzeHeader.jsx` | 분석 옵션 입력 영역 추가 (키워드·고급설정) 또는 별도 패널로 분리 |
| `ProgressPanel.jsx` | 실제 폴링 데이터로 stage 상태 반영 |
| `ResultPanel.jsx` | verdict·score·패턴·케이스·report_md 표시 |
| 신규 `AnalyzeOptions.jsx` | 파서·시간필터·케이스지정 등 고급 옵션 패널 (P2) |
