# Handoff: 로그 분석 리포트 (Log Analysis Report / Case Report Form)

## Overview
A single-page case-report form used to document log analysis findings: basic case info, symptom/analysis notes, a defect verdict, remediation actions, and free-form notes. Selecting a "defect area" or "action" radio reveals a matching detail panel; the "수정 및 해결 (Fix & Resolve)" action lets the user build a repeatable list of fix entries (add/remove).

## About the Design Files
The bundled file is a **design reference built in HTML** (a Claude-authored prototype), not production code to copy verbatim. Treat it as the source of truth for layout, copy, states, and interaction behavior, then **reimplement it in your codebase's actual stack** (React, Vue, native, etc.) using your existing component library, form state management, and styling system. Do not literally embed this HTML in the app.

## Fidelity
**High-fidelity.** Colors, spacing, typography, and copy shown are final. Recreate pixel-close using your own component primitives (inputs, radios, checkboxes, buttons) styled to match the values below.

## Screens / Views
Single screen, one scrollable form ("Case Report"), organized as a title bar + 5 numbered sections.

### Title bar
- White card, `border-radius:10px`, `border:1px solid #e5e7eb`, padding `22px 24px`.
- Left: `<h1>` "Case Report", 25px/700 weight, color `#111827`, `letter-spacing:-0.02em`.
- Right: "초기화" (Reset) button — white bg, `1px solid #d1d5db` border, `#374151` text, 12px/500, padding `8px 15px`, `border-radius:6px`. Hover: border/text turn `#4f46e5`. Clicking resets the entire form (native `form.reset()`) and clears all component state (defect-area selection, action selection, keep sub-choice, fix draft, fix list).

### Section wrapper pattern (repeats for sections 01–05)
- White card, same border/radius as title bar, padding `22px 24px`.
- Header row: numbered badge (`#eef2ff` bg, `#4f46e5` text, `JetBrains Mono` 600 12px, padding `5px 8px`, `border-radius:6px`) + section title (15px/600, `#111827`) + optional English subtitle in `JetBrains Mono` 12px `#9ca3af`.
- Outer form container: `800px` wide, `#f4f5f7` background, `border-radius:12px`, padding `26px`, `gap:16px` between section cards, `box-shadow: 0 24px 60px rgba(0,0,0,0.14)`.

### 01 — 기본 정보 (Basic Info)
2-column grid (`1fr 1fr`, gap `16px 20px`):
- 케이스 이름 (Case name) — spans both columns.
- 이슈 번호 / 제목 (Issue # / title)
- 분석자 / 담당 모듈 (Analyst / owning module)
- 분석 일자 (Analysis date) — `<input type="date">`
- 로그 출처 / 기간 (Log source / period)

All inputs: bg `#f9fafb`, border `1px solid #e5e7eb`, `border-radius:6px`, text `#111827` 13px, padding `9px 11px`. Labels: 12px/500 `#6b7280`, `7px` gap above input.

### 02 — 현상 및 분석 (Symptom & Analysis)
Two textareas (same input styling as above, `line-height:1.6`):
- 2.1 보고된 현상 (Reported symptom) — min-height 96px
- 2.2 분석 내용 (Analysis notes) — min-height 130px

### 03 — 판정 (Verdict)
2-column layout: label column `170px` + content column `1fr`, each row separated by `1px solid #eef0f3` top border.
- **판정**: 3 radio options (single-select, `name="verdict"`) — 결함 (Defect) / 비결함 (No defect) / 판정불가 (Undetermined). Accent color `#4f46e5`.
- **문제현상 발현 영역**: "모듈명:" label + single text input (max-width 280px).
- **결함영역** (Defect area): 3 mutually-exclusive expandable cards (radio `name="defect-area"`), each a bordered rounded card (`border:1px solid #e5e7eb`, `border-radius:8px`) with a light header row (`#fafbfc`) and an expanding detail panel shown only when selected:
  - **특정 모듈 (Specific module)**: reveals a single "모듈명" text input (220px).
  - **외부요인 (External factor)**: reveals checkboxes HW / 환경 / 서드파티 / 고객사 / 기타 (with adjacent text input for "기타").
  - **검증계 (Verification system)**: reveals checkboxes 테스트 환경 / 테스트 스크립트 / 측정 장치 / 기타 (with text input).
- **판정불가 사유** (Reason undetermined): checkboxes 로그부족 / 재현불가 / 기타 (+ text input).
- **판정 근거** (Verdict rationale): textarea, min-height 80px.

### 04 — 조치 (Action)
4 mutually-exclusive expandable cards (radio `name="action"`), same card pattern as section 03:

- **수정 및 해결 (Fix & Resolve)**: when selected, shows:
  - A list of already-added fix entries (`sc-for` over `fixList`), each rendered as a row: type badge (오류 수정 / 방어적·회피 수정 / "유형 미지정" if none chosen), 모듈 value, 변경/버전 value, and a "×" remove button (hover turns red `#ef4444`).
  - A dashed-border draft panel below the list: checkboxes "오류 수정 (Fix)" / "방어적/회피 수정 (Defensive)", text inputs "수정 모듈" (150px) and "변경 내용 / 대상 버전" (190px), and a right-aligned "추가" (Add) button (`#4f46e5` bg, white text, hover `#4338ca`). Clicking Add appends the current draft to the list and clears the draft — no-ops if the draft is entirely empty.
- **추가 조치 (Additional action)**: checkboxes 추가 로그 삽입 / 재현 조건 확보 / 재발 대기 / 기타(+input), plus a "계획:" (Plan) free-text line.
- **유지 (Keep)**: nested single-select (radio `name="keep-detail"`) with 3 options: 비결함 종결 (Close as non-defect), 결함 수용·보류 (Accept/defer defect) — reveals a "사유" (reason) input when selected, and 판정불가 종결 (Close as undetermined).
- **이관/외부 대응 (Handover / external handling)**: checkboxes 타 모듈 수정 / 고객사 수정 / 서드파티 수정 / HW 조치 / 검증계 담당 조치, plus "해결 주체:" and "티켓 / 전달 채널:" text inputs.

Card header label: 13px/600 `#111827`; English tag: `JetBrains Mono` 10px `#9ca3af`, `letter-spacing:0.08em`, uppercase.

### 05 — 특이사항 / 비고 (Notes)
Single textarea, min-height 90px, same input styling as elsewhere.

## Interactions & Behavior
- **Defect area** (section 03) and **Action** (section 04) are each single-select radio groups; picking one collapses/expands the matching detail panel (`sc-if` conditional render — implement as conditional render in your framework, not CSS show/hide, so unmounted panels don't hold stale state).
- **Keep** sub-choice is a nested single-select radio (`keep-detail`) inside the "유지" action panel; only visible when "유지" is the selected action.
- **Fix list** (수정 및 해결) is the only repeatable/dynamic list in the form:
  - State: draft `{ fix: bool, defensive: bool, module: string, version: string }` + committed `fixList: []`.
  - "추가" button commits the draft to the list (ignored if draft is fully empty) and clears the draft fields.
  - Each committed row shows a computed type label joining checked fix-type checkboxes with " · ", falling back to "유형 미지정" if neither is checked; empty module/version display as "—".
  - Remove button (×) deletes that row by index.
- **초기화 (Reset)** button: resets all native form fields (`form.reset()`) AND resets all React/component state (area selection, action selection, keep sub-choice, fix draft, fix list) back to initial empty values.
- No client-side validation is implemented in the prototype (all fields optional); confirm required-field rules with stakeholders before building.
- No submit/save action wired up in the prototype — only local UI state. Persistence/API needs to be defined.

## State Management
Suggested state shape (mirrors the prototype's logic class):
```
{
  area: "" | "module" | "external" | "verify",
  act: "" | "fix" | "add" | "keep" | "handover",
  keepSub: "" | "none" | "accept" | "undecided",
  fixDraft: { fix: boolean, defensive: boolean, module: string, version: string },
  fixList: Array<{ fix, defensive, module, version }>
}
```
Plus plain form-field state for every text input/textarea/checkbox not explicitly listed above (기본 정보 fields, 현상 및 분석 textareas, 판정 근거, 특이사항, all the section-03/04 checkbox groups and free-text fields) — the prototype leaves these as uncontrolled native inputs; decide whether your implementation should control them centrally (e.g. one `formData` object) for save/submit.

## Design Tokens

**Colors**
- Page background (canvas behind form): `#d9dbe0`
- Form outer background: `#f4f5f7`
- Card background: `#ffffff`
- Card/input border: `#e5e7eb`
- Divider (section row border): `#eef0f3`
- Primary text: `#111827`
- Secondary/label text: `#6b7280`
- Muted text / placeholders: `#9ca3af`
- Body text (radio/checkbox labels): `#1f2937`
- Input background: `#f9fafb`
- Draft panel dashed border: `#d8dbe0`, background `#fbfbfc`
- Accent / primary action: `#4f46e5` (hover `#4338ca`)
- Accent tint (badges): background `#eef2ff`, text `#4f46e5`
- Destructive (remove hover): `#ef4444`
- Card shadow: `0 24px 60px rgba(0,0,0,0.14)`

**Typography**
- UI font: `'Pretendard', system-ui, sans-serif` (loaded via `pretendard@1.3.9` CDN)
- Monospace accents (badges, tags): `'JetBrains Mono'`, weights 400/500/600 (Google Fonts)
- Title (H1): 25px / 700 / `-0.02em` letter-spacing
- Section title (H2): 15px / 600
- Section badge text: 12px / 600 (JetBrains Mono)
- Body/input text: 13px / 400–500
- Label text: 12px / 500
- English tag text: 10–12px / 500, `0.08em` letter-spacing, JetBrains Mono

**Spacing**
- Outer form padding: 26px
- Section card padding: 22px 24px
- Between-section gap: 16px
- Grid gaps: 16–20px
- Input padding: 9px 11px (inputs), 11px (textareas)

**Radius**
- Cards / outer form: 10–12px
- Inputs / buttons: 5–6px
- Nested detail cards (defect-area / action panels): 8px

## Assets
No images or icons — text and native form controls only. Fonts loaded from CDN (Pretendard, JetBrains Mono); no local asset files to hand off.

## Files
- `로그 분석 리포트.dc.html` — full source of the design (see inline HTML/CSS for exact values; the `<script data-dc-script>` block at the bottom contains the interaction logic referenced above).
