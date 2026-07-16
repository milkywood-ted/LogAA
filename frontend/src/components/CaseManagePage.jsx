import { useState, useEffect, useCallback, useMemo } from "react"
import { useNavigate } from "react-router-dom"
import {
  getKBCases, getKBCase, createKBCase, updateKBCase, deleteKBCase, syncKBCases,
  getKBCasePatterns, linkKBCasePattern, unlinkKBCasePattern,
  getKBCaseReferences, addKBCaseReference, deleteKBCaseReference,
  getPatterns, getPattern, createPattern, updatePattern, deletePattern,
  getChips,
} from "../api"

// ─── 공통 헬퍼 ───────────────────────────────────────────────────────────────

function parseList(text) {
  return text.split(",").map(s => s.trim()).filter(Boolean)
}

function ChipBadge({ label, onRemove }) {
  return (
    <span className="pm-ref-tag selected" style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
      {label}
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          style={{ background: "none", border: "none", cursor: "pointer", color: "inherit", padding: 0, lineHeight: 1 }}
        >×</button>
      )}
    </span>
  )
}

// ─── 칩 태그 선택기 ───────────────────────────────────────────────────────────

function ChipTagSelector({ selected, onChange }) {
  const [chips, setChips] = useState([])

  useEffect(() => {
    getChips().then(data => setChips(data.chips || [])).catch(() => {})
  }, [])

  function toggle(chip) {
    onChange(
      selected.includes(chip) ? selected.filter(c => c !== chip) : [...selected, chip]
    )
  }

  // YAML에 없는데 이미 선택된 칩 (삭제된 칩)
  const unknown = selected.filter(c => !chips.includes(c))

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div className="pm-ref-tags">
        {chips.map(chip => (
          <label key={chip} style={{ display: "inline-flex", alignItems: "center", gap: 4, cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={selected.includes(chip)}
              onChange={() => toggle(chip)}
              style={{ width: "auto", margin: 0 }}
            />
            <span style={{ fontSize: 12 }}>{chip}</span>
          </label>
        ))}
      </div>
      {unknown.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4, alignItems: "center" }}>
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>미등록 칩:</span>
          {unknown.map(chip => (
            <span key={chip} style={{ display: "inline-flex", alignItems: "center", gap: 4,
              fontSize: 11, padding: "2px 8px", borderRadius: 100,
              background: "var(--red-light, #fce4ec)", border: "1px solid var(--red, #e53935)",
              color: "var(--red, #e53935)" }}>
              {chip}
              <button type="button" onClick={() => onChange(selected.filter(c => c !== chip))}
                style={{ background: "none", border: "none", cursor: "pointer", color: "inherit", padding: 0, lineHeight: 1, fontSize: 12 }}>
                ×
              </button>
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── 패턴 선택기 (검색 필터 포함) ────────────────────────────────────────────

const PATTERN_TYPE_OPTIONS = ["전체", "PRESENCE", "SEQUENCE", "WINDOW", "ABSENCE", "COMPOSITE"]

function PatternSelector({ allPatterns, selectedIds, onToggle }) {
  const [query, setQuery] = useState("")
  const [typeFilter, setTypeFilter] = useState("전체")

  const filtered = allPatterns.filter(p => {
    const matchType = typeFilter === "전체" || p.type === typeFilter
    const q = query.trim().toLowerCase()
    const matchQuery = !q || p.name.toLowerCase().includes(q) || p.type.toLowerCase().includes(q)
    return matchType && matchQuery
  })

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div className="pm-field" style={{ flexDirection: "row", gap: 6 }}>
        <input
          style={{ flex: 1, minWidth: 0 }}
          placeholder="패턴 이름 검색..."
          value={query}
          onChange={e => setQuery(e.target.value)}
        />
        <select
          style={{ flexShrink: 0, width: "auto" }}
          value={typeFilter}
          onChange={e => setTypeFilter(e.target.value)}
        >
          {PATTERN_TYPE_OPTIONS.map(t => <option key={t} value={t}>{t}</option>)}
        </select>
      </div>
      {filtered.length === 0 ? (
        <span className="pm-hint">검색 결과가 없습니다.</span>
      ) : (
        <div className="pm-ref-tags" style={{ maxHeight: 160, overflowY: "auto", padding: "4px 0" }}>
          {filtered.map(p => (
            <button
              key={p.id}
              type="button"
              className={`pm-ref-tag ${selectedIds.includes(p.id) ? "selected" : ""}`}
              onClick={() => onToggle(p.id)}
            >
              [{p.type}] {p.name}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── 케이스 리포트 스키마 v2 — 상수·헬퍼 ─────────────────────────────────────
// 토큰↔표시 매핑과 검증 규칙은 구현 설계 문서(§1.2, §2.2)를 따른다:
//   Document/로그분석 리포트 및 케이스 스키마 개선/케이스 스키마 개선 구현 설계.md

const VERDICT_LABEL = { defect: "결함", no_defect: "비결함", undetermined: "판정불가" }
const AREA_TYPE_LABEL = { module: "특정 모듈", external: "외부요인", verification: "검증계" }
const REASON_OPTIONS = [
  ["insufficient_logs", "로그부족"], ["not_reproducible", "재현불가"], ["other", "기타"],
]
const EXTERNAL_ITEMS = [["hw", "HW"], ["env", "환경"], ["third_party", "서드파티"], ["customer", "고객사"]]
const VERIFICATION_ITEMS = [["test_env", "테스트 환경"], ["test_script", "테스트 스크립트"], ["measurement", "측정 장치"]]
const ADDITIONAL_ITEMS = [["add_logs", "추가 로그 삽입"], ["secure_repro", "재현 조건 확보"], ["wait_recurrence", "재발 대기"]]
const HANDOVER_ITEMS = [
  ["other_module", "타 모듈 수정"], ["customer", "고객사 수정"], ["third_party", "서드파티 수정"],
  ["hw", "HW 조치"], ["verification", "검증계 담당 조치"],
]
const KEEP_OPTIONS = [
  ["close_no_defect", "비결함 종결"], ["accept_defect", "결함 수용·보류"], ["close_undetermined", "판정불가 종결"],
]
const FIX_TYPE_LABEL = { fix: "오류 수정", defensive: "방어적·회피 수정" }
const AREA_ITEM_LABEL = Object.fromEntries([...EXTERNAL_ITEMS, ...VERIFICATION_ITEMS])

const VERDICT_COLORS = {
  defect:       { bg: "#fce4ec", color: "#c62828", border: "#f8bbd0" },
  no_defect:    { bg: "#e8f5e9", color: "#2e7d32", border: "#a5d6a7" },
  undetermined: { bg: "#eceff1", color: "#546e7a", border: "#cfd8dc" },
}

const EMPTY_ACTIONS = {
  fix: { selected: false, entries: [] },
  additional: { selected: false, items: [], plan: "" },
  keep: { selected: false, detail: null, reason: "" },
  handover: { selected: false, items: [], owner: "", channel: "" },
}

function normalizeActions(a) {
  const base = structuredClone(EMPTY_ACTIONS)
  if (!a || typeof a !== "object") return base
  for (const k of Object.keys(base)) base[k] = { ...base[k], ...(a[k] || {}) }
  return base
}

function today() {
  return new Date().toISOString().slice(0, 10)
}

// "other:<서술>" 토큰 표시용
function tokenLabel(map, tok) {
  return tok.startsWith("other:") ? `기타${tok.slice(6) ? `: ${tok.slice(6)}` : ""}` : (map[tok] || tok)
}

// 열린 건 / 닫힌 건 / 판정 미기재(레거시) — 파생 규칙 (설계 §1.4)
// 조치가 하나라도 선택되면 닫힌 건, 없으면 열린 건
function caseStatus(c) {
  if (!c.verdict) return { key: "legacy", label: "판정 미기재" }
  const a = c.actions || {}
  if (a.fix?.selected || a.additional?.selected || a.handover?.selected || a.keep?.selected) return { key: "closed", label: "닫힌 건" }
  return { key: "open", label: "열린 건" }
}

// 백엔드(§2.2)와 동일한 조건부 필수 규칙 — 최종 방어선은 AA V2 의 422
function validateReport(p) {
  const errs = []
  if (!p.name.trim()) errs.push("케이스 이름을 입력하세요.")
  if (!p.verdict) errs.push("판정을 선택하세요.")
  if (p.verdict === "defect") {
    if (!p.symptom_module.trim()) errs.push("결함 판정에는 문제현상 발현 영역(모듈명)이 필요합니다.")
    if (!p.defect_area_type) errs.push("결함 판정에는 결함영역 선택이 필요합니다.")
  }
  if (p.defect_area_type === "module" && !p.defect_area_module.trim())
    errs.push("결함영역이 '특정 모듈'이면 모듈명이 필요합니다.")
  if ((p.defect_area_type === "external" || p.defect_area_type === "verification") && p.defect_area_items.length === 0)
    errs.push("결함영역 하위 항목을 1개 이상 선택하세요.")
  if (p.verdict === "undetermined" && !p.undetermined_reason) errs.push("판정불가 사유를 선택하세요.")
  if (p.undetermined_reason === "other" && !p.undetermined_reason_note.trim())
    errs.push("판정불가 사유가 '기타'이면 사유 서술이 필요합니다.")
  if (p.analysis_date && !/^\d{4}-\d{2}-\d{2}$/.test(p.analysis_date))
    errs.push("분석 일자는 YYYY-MM-DD 형식이어야 합니다.")
  const a = p.actions
  if (a.fix.selected) {
    if (a.fix.entries.length === 0) errs.push("'수정 및 해결' 조치에는 수정 항목이 1개 이상 필요합니다.")
    if (a.fix.entries.some(e => !e.module.trim())) errs.push("수정 항목에는 수정 모듈이 필요합니다.")
  }
  if (a.keep.selected) {
    if (!a.keep.detail) errs.push("'유지' 조치의 하위 구분을 선택하세요.")
    else if (a.keep.detail === "accept_defect" && !a.keep.reason.trim()) errs.push("'결함 수용·보류'에는 사유가 필요합니다.")
  }
  if (a.handover.selected && !a.handover.owner.trim()) errs.push("'이관/외부 대응'에는 해결 주체가 필요합니다.")
  return errs
}

// ─── 리포트 UI 공용 컴포넌트 ─────────────────────────────────────────────────

function VerdictBadge({ verdict }) {
  if (!verdict) return <span className="cr-badge legacy">판정 미기재</span>
  const c = VERDICT_COLORS[verdict]
  return (
    <span className="cr-badge" style={{ background: c.bg, color: c.color, borderColor: c.border }}>
      {VERDICT_LABEL[verdict]}
    </span>
  )
}

function StatusBadge({ caseData }) {
  const s = caseStatus(caseData)
  if (!s || s.key === "legacy") return null
  return (
    <span
      className="cr-badge"
      style={s.key === "open"
        ? { background: "var(--accent-light)", color: "var(--accent)", borderColor: "var(--accent-border)" }
        : { background: "var(--surface-warm)", color: "var(--text-muted)", borderColor: "var(--border)" }}
    >{s.label}</span>
  )
}

function SectionCard({ no, title, en, children }) {
  return (
    <div className="cr-section">
      <div className="cr-section-head">
        <span className="cr-section-no">{no}</span>
        <span className="cr-section-title">{title}</span>
        {en && <span className="cr-section-en">{en}</span>}
      </div>
      {children}
    </div>
  )
}

function RadioGroup({ name, options, value, onChange }) {
  return (
    <div className="cr-radio-row">
      {options.map(([tok, label]) => (
        <label key={tok} className="cr-check">
          <input type="radio" name={name} checked={value === tok} onChange={() => onChange(tok)} />
          {label}
        </label>
      ))}
    </div>
  )
}

// 체크박스 그룹 + "기타" 텍스트 (토큰: "other:<서술>")
function TokenCheckGroup({ options, items, onChange }) {
  const otherToken = items.find(t => t.startsWith("other:"))
  function toggle(tok) {
    onChange(items.includes(tok) ? items.filter(t => t !== tok) : [...items, tok])
  }
  return (
    <div className="cr-radio-row">
      {options.map(([tok, label]) => (
        <label key={tok} className="cr-check">
          <input type="checkbox" checked={items.includes(tok)} onChange={() => toggle(tok)} />
          {label}
        </label>
      ))}
      <label className="cr-check">
        <input
          type="checkbox"
          checked={otherToken !== undefined}
          onChange={() => onChange(otherToken !== undefined
            ? items.filter(t => !t.startsWith("other:"))
            : [...items, "other:"])}
        />
        기타
      </label>
      {otherToken !== undefined && (
        <input
          type="text" className="cr-other-input" placeholder="기타 내용"
          value={otherToken.slice(6)}
          onChange={e => onChange(items.map(t => (t.startsWith("other:") ? `other:${e.target.value}` : t)))}
        />
      )}
    </div>
  )
}

// 확장형 카드 — 선택 시에만 상세 패널을 마운트 (stale 상태 방지)
function ExpandCard({ type, name, label, en, selected, onToggle, children }) {
  return (
    <div className={`cr-card ${selected ? "selected" : ""}`}>
      <label className="cr-card-head">
        <input type={type} name={name} checked={selected} onChange={onToggle} />
        <span className="cr-card-label">{label}</span>
        {en && <span className="cr-card-en">{en}</span>}
      </label>
      {selected && <div className="cr-card-body">{children}</div>}
    </div>
  )
}

// 수정 및 해결 — 반복 리스트 편집기 (draft + entries)
function FixEditor({ fix, onChange }) {
  const [draft, setDraft] = useState({ type: null, module: "", change: "" })
  const canAdd = draft.type && draft.module.trim()

  function add() {
    if (!canAdd) return
    onChange({ entries: [...fix.entries, { ...draft, module: draft.module.trim() }] })
    setDraft({ type: null, module: "", change: "" })
  }

  return (
    <>
      {fix.entries.map((e, i) => (
        <div key={i} className="cr-fix-row">
          <span className="cr-fix-type">{FIX_TYPE_LABEL[e.type] || e.type}</span>
          <span>{e.module || "—"}</span>
          <span style={{ color: "var(--text-muted)" }}>{e.change || "—"}</span>
          <button
            type="button" className="cr-fix-remove"
            onClick={() => onChange({ entries: fix.entries.filter((_, idx) => idx !== i) })}
          >×</button>
        </div>
      ))}
      <div className="cr-fix-draft">
        <RadioGroup
          name="fix-entry-type"
          options={[["fix", "오류 수정 (Fix)"], ["defensive", "방어적/회피 수정 (Defensive)"]]}
          value={draft.type}
          onChange={v => setDraft(d => ({ ...d, type: v }))}
        />
        <div className="pm-form-row">
          <label className="pm-field pm-field-half">
            <span className="pm-field-label">수정 모듈 *</span>
            <input value={draft.module} onChange={e => setDraft(d => ({ ...d, module: e.target.value }))} />
          </label>
          <label className="pm-field pm-field-half">
            <span className="pm-field-label">변경 내용 / 대상 버전</span>
            <input value={draft.change} onChange={e => setDraft(d => ({ ...d, change: e.target.value }))} />
          </label>
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <button type="button" className="settings-save-btn" onClick={add} disabled={!canAdd}>추가</button>
        </div>
      </div>
    </>
  )
}

// ─── 리포트 읽기 모드 요약 ───────────────────────────────────────────────────

function defectAreaText(c) {
  if (!c.defect_area_type) return null
  if (c.defect_area_type === "module") return `특정 모듈 (${c.defect_area_module || "—"})`
  const items = (c.defect_area_items || []).map(t => tokenLabel(AREA_ITEM_LABEL, t)).join(", ")
  return `${AREA_TYPE_LABEL[c.defect_area_type]}${items ? ` (${items})` : ""}`
}

function ReportSummary({ caseData: c }) {
  const a = normalizeActions(c.actions)
  // 세 좌표 대비: 발현 영역 / 결함영역 / 수정 모듈·해결 주체 (설계 §4.3)
  const resolvers = [
    ...(a.fix.selected ? a.fix.entries.map(e => e.module).filter(Boolean) : []),
    ...(a.handover.selected && a.handover.owner ? [a.handover.owner] : []),
  ]
  const rows = []
  if (c.analyst || c.owner_module)
    rows.push(["분석자 / 담당 모듈", [c.analyst, c.owner_module].filter(Boolean).join(" / ")])
  if (c.log_source) rows.push(["로그 출처 / 기간", c.log_source])
  if (c.verdict === "defect") {
    rows.push(["문제현상 발현 영역", c.symptom_module || "—"])
    rows.push(["결함영역", defectAreaText(c) || "—"])
    if (resolvers.length > 0) rows.push(["수정 모듈 / 해결 주체", resolvers.join(", ")])
  }
  if (c.verdict === "undetermined") {
    const reason = REASON_OPTIONS.find(([tok]) => tok === c.undetermined_reason)
    rows.push(["판정불가 사유", reason
      ? reason[1] + (c.undetermined_reason === "other" && c.undetermined_reason_note ? `: ${c.undetermined_reason_note}` : "")
      : "—"])
  }
  if (rows.length === 0 && !c.verdict_rationale) return null
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {rows.map(([label, value]) => (
        <div key={label} style={{ display: "flex", gap: 8, fontSize: 13 }}>
          <span className="pm-field-label" style={{ minWidth: 150, flexShrink: 0 }}>{label}</span>
          <span>{value}</span>
        </div>
      ))}
      {c.verdict_rationale && (
        <div>
          <div className="pm-field-label" style={{ marginBottom: 4 }}>판정 근거</div>
          <div className="pm-item-desc" style={{ fontSize: 13 }}>{c.verdict_rationale}</div>
        </div>
      )}
    </div>
  )
}

function ActionsSummary({ actions }) {
  const a = normalizeActions(actions)
  const blocks = []
  if (a.fix.selected) {
    blocks.push(
      <div key="fix">
        <div className="pm-field-label" style={{ marginBottom: 4 }}>수정 및 해결</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {a.fix.entries.map((e, i) => (
            <div key={i} className="cr-fix-row">
              <span className="cr-fix-type">{FIX_TYPE_LABEL[e.type] || e.type}</span>
              <span>{e.module || "—"}</span>
              <span style={{ color: "var(--text-muted)" }}>{e.change || "—"}</span>
            </div>
          ))}
        </div>
      </div>
    )
  }
  if (a.additional.selected) {
    blocks.push(
      <div key="additional" style={{ fontSize: 13 }}>
        <div className="pm-field-label" style={{ marginBottom: 4 }}>추가 조치</div>
        {a.additional.items.map(t => tokenLabel(Object.fromEntries(ADDITIONAL_ITEMS), t)).join(", ") || "—"}
        {a.additional.plan && <div style={{ color: "var(--text-muted)" }}>계획: {a.additional.plan}</div>}
      </div>
    )
  }
  if (a.keep.selected) {
    const detail = KEEP_OPTIONS.find(([tok]) => tok === a.keep.detail)
    blocks.push(
      <div key="keep" style={{ fontSize: 13 }}>
        <div className="pm-field-label" style={{ marginBottom: 4 }}>유지</div>
        {detail ? detail[1] : "—"}
        {a.keep.detail === "accept_defect" && a.keep.reason && (
          <span style={{ color: "var(--text-muted)" }}> — 사유: {a.keep.reason}</span>
        )}
      </div>
    )
  }
  if (a.handover.selected) {
    blocks.push(
      <div key="handover" style={{ fontSize: 13 }}>
        <div className="pm-field-label" style={{ marginBottom: 4 }}>이관/외부 대응</div>
        {a.handover.items.map(t => tokenLabel(Object.fromEntries(HANDOVER_ITEMS), t)).join(", ") || "—"}
        <div style={{ color: "var(--text-muted)" }}>
          해결 주체: {a.handover.owner || "—"}
          {a.handover.channel && ` · 채널: ${a.handover.channel}`}
        </div>
      </div>
    )
  }
  if (blocks.length === 0) return null
  return (
    <div>
      <div className="pm-field-label" style={{ marginBottom: 6 }}>조치</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 10, paddingLeft: 2 }}>{blocks}</div>
    </div>
  )
}

// ─── 케이스 폼 ────────────────────────────────────────────────────────────────

const EMPTY_CASE = {
  name: "", description: "", analysis: "",
  keywords: [], profile_refs: [], chip_tags: [],
  analyst: "", owner_module: "", analysis_date: null, log_source: "",
  verdict: null, symptom_module: "",
  defect_area_type: null, defect_area_module: "", defect_area_items: [],
  undetermined_reason: null, undetermined_reason_note: "", verdict_rationale: "",
  actions: EMPTY_ACTIONS, notes: "",
}

function CaseForm({ initial, allPatterns, onSubmit, onCancel, submitting, caseId }) {
  const [f, setF] = useState({
    name: initial.name || "",
    description: initial.description || "",
    analysis: initial.analysis || "",
    analyst: initial.analyst || "",
    owner_module: initial.owner_module || "",
    analysis_date: initial.analysis_date || today(),
    log_source: initial.log_source || "",
    verdict: initial.verdict || null,
    symptom_module: initial.symptom_module || "",
    defect_area_type: initial.defect_area_type || null,
    defect_area_module: initial.defect_area_module || "",
    defect_area_items: initial.defect_area_items || [],
    undetermined_reason: initial.undetermined_reason || null,
    undetermined_reason_note: initial.undetermined_reason_note || "",
    verdict_rationale: initial.verdict_rationale || "",
    actions: normalizeActions(initial.actions),
    notes: initial.notes || "",
  })
  const [keywords, setKeywords] = useState((initial.keywords || []).join(", "))
  const [chipTags, setChipTags] = useState(initial.chip_tags || [])
  const [profileRefs, setProfileRefs] = useState((initial.profile_refs || []).join(", "))
  const [formErrors, setFormErrors] = useState([])
  // 연결 패턴은 별도 관리 (케이스 ID 있을 때만 사용)
  const [linkedPatternIds, setLinkedPatternIds] = useState(
    (initial.patterns || []).map(p => p.id)
  )

  const set = (k, v) => setF(prev => ({ ...prev, [k]: v }))
  const setAction = (k, patch) =>
    setF(prev => ({ ...prev, actions: { ...prev.actions, [k]: { ...prev.actions[k], ...patch } } }))

  function togglePattern(pid) {
    setLinkedPatternIds(prev =>
      prev.includes(pid) ? prev.filter(id => id !== pid) : [...prev, pid]
    )
  }

  // 담당 모듈 prefill: 비어 있으면 첫 profile ref 값을 채운다 (스냅샷 — 이후 수동 수정 가능)
  function onProfileRefsChange(text) {
    setProfileRefs(text)
    const refs = parseList(text)
    if (!f.owner_module && refs.length > 0) set("owner_module", refs[0])
  }

  function submit() {
    const isDefect = f.verdict === "defect"
    const payload = {
      name: f.name.trim(),
      description: f.description,
      analysis: f.analysis,
      keywords: parseList(keywords),
      chip_tags: chipTags,
      profile_refs: parseList(profileRefs),
      analyst: f.analyst,
      owner_module: f.owner_module,
      analysis_date: f.analysis_date || null,
      log_source: f.log_source,
      verdict: f.verdict,
      // 판정과 무관한 잔존 좌표는 정리해서 전송 (검증 혼선 방지)
      symptom_module: isDefect ? f.symptom_module : "",
      defect_area_type: isDefect ? f.defect_area_type : null,
      defect_area_module: isDefect && f.defect_area_type === "module" ? f.defect_area_module : "",
      defect_area_items:
        isDefect && (f.defect_area_type === "external" || f.defect_area_type === "verification")
          ? f.defect_area_items : [],
      undetermined_reason: f.verdict === "undetermined" ? f.undetermined_reason : null,
      undetermined_reason_note:
        f.verdict === "undetermined" && f.undetermined_reason === "other" ? f.undetermined_reason_note : "",
      verdict_rationale: f.verdict_rationale,
      actions: f.actions,
      notes: f.notes,
    }
    const errs = validateReport(payload)
    setFormErrors(errs)
    if (errs.length > 0) {
      window.alert(`저장할 수 없습니다:\n\n${errs.map(e => `• ${e}`).join("\n")}`)
      return
    }
    onSubmit(payload, linkedPatternIds)
  }

  return (
    <div className="pm-form">
      {formErrors.length > 0 && (
        <div className="settings-error">
          {formErrors.map((e, i) => <div key={i}>• {e}</div>)}
        </div>
      )}

      {/* 01 기본 정보 */}
      <SectionCard no="01" title="기본 정보" en="Basic Info">
        <label className="pm-field">
          <span className="pm-field-label">케이스 이름 *</span>
          <input value={f.name} onChange={e => set("name", e.target.value)} placeholder="NVMe 초기화 타임아웃" />
        </label>
        <div className="cr-grid2">
          <label className="pm-field">
            <span className="pm-field-label">분석자</span>
            <input value={f.analyst} onChange={e => set("analyst", e.target.value)} />
          </label>
          <label className="pm-field">
            <span className="pm-field-label">담당 모듈</span>
            <input value={f.owner_module} onChange={e => set("owner_module", e.target.value)}
              placeholder="Profile Refs 입력 시 자동 제안" />
          </label>
          <label className="pm-field">
            <span className="pm-field-label">분석 일자</span>
            <input type="date" value={f.analysis_date || ""} onChange={e => set("analysis_date", e.target.value)} />
          </label>
          <label className="pm-field">
            <span className="pm-field-label">로그 출처 / 기간</span>
            <input value={f.log_source} onChange={e => set("log_source", e.target.value)}
              placeholder="dmesg, 2026-07-01 ~ 07-08" />
          </label>
        </div>
        {caseId ? (
          <ReferencesSection caseId={caseId} />
        ) : (
          <span className="pm-hint">이슈 번호/제목(외부 참조 ID)은 케이스 저장 후 추가할 수 있습니다.</span>
        )}
      </SectionCard>

      {/* 02 현상 및 분석 */}
      <SectionCard no="02" title="현상 및 분석" en="Symptom & Analysis">
        <label className="pm-field">
          <span className="pm-field-label">보고된 현상</span>
          <textarea value={f.description} onChange={e => set("description", e.target.value)} rows={3}
            placeholder="재현 절차·발생 조건 포함 — BGE-M3 임베딩 대상" />
        </label>
        <label className="pm-field">
          <span className="pm-field-label">분석 내용</span>
          <textarea value={f.analysis} onChange={e => set("analysis", e.target.value)} rows={4}
            placeholder="핵심 로그 라인, 타임라인, 분석 근거 요약" />
        </label>
      </SectionCard>

      {/* 03 판정 */}
      <SectionCard no="03" title="판정" en="Verdict">
        <div className="pm-field">
          <span className="pm-field-label">판정 *</span>
          <RadioGroup
            name="verdict"
            options={Object.entries(VERDICT_LABEL)}
            value={f.verdict}
            onChange={v => set("verdict", v)}
          />
        </div>

        {f.verdict === "defect" && (
          <>
            <label className="pm-field">
              <span className="pm-field-label">문제현상 발현 영역 (모듈명) *</span>
              <input style={{ maxWidth: 280 }} value={f.symptom_module}
                onChange={e => set("symptom_module", e.target.value)} />
            </label>
            <div className="pm-field">
              <span className="pm-field-label">결함영역 *</span>
              <ExpandCard type="radio" name="defect-area" label="특정 모듈" en="Module"
                selected={f.defect_area_type === "module"} onToggle={() => set("defect_area_type", "module")}>
                <label className="pm-field">
                  <span className="pm-field-label">모듈명 *</span>
                  <input style={{ maxWidth: 280 }} value={f.defect_area_module}
                    onChange={e => set("defect_area_module", e.target.value)} />
                </label>
              </ExpandCard>
              <ExpandCard type="radio" name="defect-area" label="외부요인" en="External"
                selected={f.defect_area_type === "external"} onToggle={() => set("defect_area_type", "external")}>
                <TokenCheckGroup options={EXTERNAL_ITEMS} items={f.defect_area_items}
                  onChange={v => set("defect_area_items", v)} />
              </ExpandCard>
              <ExpandCard type="radio" name="defect-area" label="검증계" en="Verification"
                selected={f.defect_area_type === "verification"} onToggle={() => set("defect_area_type", "verification")}>
                <TokenCheckGroup options={VERIFICATION_ITEMS} items={f.defect_area_items}
                  onChange={v => set("defect_area_items", v)} />
              </ExpandCard>
            </div>
          </>
        )}

        {f.verdict === "undetermined" && (
          <div className="pm-field">
            <span className="pm-field-label">판정불가 사유 * (단일 선택)</span>
            <RadioGroup
              name="undetermined-reason"
              options={REASON_OPTIONS}
              value={f.undetermined_reason}
              onChange={v => set("undetermined_reason", v)}
            />
            {f.undetermined_reason === "other" && (
              <input placeholder="사유 서술 *" value={f.undetermined_reason_note}
                onChange={e => set("undetermined_reason_note", e.target.value)} />
            )}
          </div>
        )}

        <label className="pm-field">
          <span className="pm-field-label">판정 근거</span>
          <textarea value={f.verdict_rationale} onChange={e => set("verdict_rationale", e.target.value)} rows={3}
            placeholder="판정을 뒷받침하는 로그·코드·스펙 근거" />
        </label>
      </SectionCard>

      {/* 04 조치 — 복수 선택 가능 */}
      <SectionCard no="04" title="조치 (복수 선택 가능)" en="Action">
        <ExpandCard type="checkbox" label="수정 및 해결" en="Fix and Resolve"
          selected={f.actions.fix.selected}
          onToggle={() => setAction("fix", { selected: !f.actions.fix.selected })}>
          <FixEditor fix={f.actions.fix} onChange={patch => setAction("fix", patch)} />
        </ExpandCard>

        <ExpandCard type="checkbox" label="추가 조치" en="Additional Action"
          selected={f.actions.additional.selected}
          onToggle={() => setAction("additional", { selected: !f.actions.additional.selected })}>
          <TokenCheckGroup options={ADDITIONAL_ITEMS} items={f.actions.additional.items}
            onChange={v => setAction("additional", { items: v })} />
          <label className="pm-field">
            <span className="pm-field-label">계획</span>
            <input value={f.actions.additional.plan}
              onChange={e => setAction("additional", { plan: e.target.value })} />
          </label>
        </ExpandCard>

        <ExpandCard type="checkbox" label="유지" en="Keep"
          selected={f.actions.keep.selected}
          onToggle={() => setAction("keep", { selected: !f.actions.keep.selected })}>
          <RadioGroup
            name="keep-detail"
            options={KEEP_OPTIONS}
            value={f.actions.keep.detail}
            onChange={v => setAction("keep", { detail: v })}
          />
          {f.actions.keep.detail === "accept_defect" && (
            <label className="pm-field">
              <span className="pm-field-label">사유 * (기술적 불가 / 우선순위·비용)</span>
              <input value={f.actions.keep.reason}
                onChange={e => setAction("keep", { reason: e.target.value })} />
            </label>
          )}
        </ExpandCard>

        <ExpandCard type="checkbox" label="이관/외부 대응" en="Handover"
          selected={f.actions.handover.selected}
          onToggle={() => setAction("handover", { selected: !f.actions.handover.selected })}>
          <TokenCheckGroup options={HANDOVER_ITEMS} items={f.actions.handover.items}
            onChange={v => setAction("handover", { items: v })} />
          <div className="pm-form-row">
            <label className="pm-field pm-field-half">
              <span className="pm-field-label">해결 주체 *</span>
              <input value={f.actions.handover.owner}
                onChange={e => setAction("handover", { owner: e.target.value })} />
            </label>
            <label className="pm-field pm-field-half">
              <span className="pm-field-label">티켓 / 전달 채널</span>
              <input value={f.actions.handover.channel}
                onChange={e => setAction("handover", { channel: e.target.value })} />
            </label>
          </div>
        </ExpandCard>
      </SectionCard>

      {/* 05 특이사항 / 비고 */}
      <SectionCard no="05" title="특이사항 / 비고" en="Notes">
        <textarea className="cr-notes" value={f.notes} onChange={e => set("notes", e.target.value)} rows={3}
          placeholder="관련 이슈 링크, 후속 확인 필요 사항, 리뷰어 코멘트 등" />
      </SectionCard>

      {/* 시스템 필드 — 검색/임베딩/패턴 매칭용 */}
      <SectionCard no="SYS" title="검색 / 매칭 설정" en="System">
        <label className="pm-field">
          <span className="pm-field-label">키워드 (콤마 구분)</span>
          <input value={keywords} onChange={e => setKeywords(e.target.value)} placeholder="ata, timeout, nvme" />
        </label>
        <div className="pm-field">
          <span className="pm-field-label">
            Chip Tags
            {chipTags.length > 0 && (
              <span style={{ marginLeft: 6, color: "var(--accent)", fontWeight: 700 }}>{chipTags.join(", ")}</span>
            )}
          </span>
          <ChipTagSelector selected={chipTags} onChange={setChipTags} />
        </div>
        <label className="pm-field">
          <span className="pm-field-label">Profile Refs (콤마 구분)</span>
          <input value={profileRefs} onChange={e => onProfileRefsChange(e.target.value)} placeholder="DTV FRC, DTV DP" />
        </label>
        <div className="pm-field">
          <span className="pm-field-label">
            연결 패턴
            {linkedPatternIds.length > 0 && (
              <span style={{ marginLeft: 6, color: "var(--accent)", fontWeight: 700 }}>
                {linkedPatternIds.length}개 선택됨
              </span>
            )}
          </span>
          {allPatterns.length === 0 ? (
            <span className="pm-hint">등록된 패턴이 없습니다.</span>
          ) : (
            <PatternSelector
              allPatterns={allPatterns}
              selectedIds={linkedPatternIds}
              onToggle={togglePattern}
            />
          )}
        </div>
      </SectionCard>

      <div className="pm-form-actions">
        <button className="settings-btn-sm" onClick={onCancel} disabled={submitting}>취소</button>
        <button className="settings-save-btn" onClick={submit} disabled={submitting || !f.name.trim()}>
          {submitting ? "저장 중..." : "저장"}
        </button>
      </div>
    </div>
  )
}

// ─── 외부 참조 ID 인라인 편집 ─────────────────────────────────────────────────

function ReferencesSection({ caseId }) {
  const [refs, setRefs] = useState([])
  const [system, setSystem] = useState("")
  const [refId, setRefId] = useState("")
  const [error, setError] = useState(null)
  const [copiedId, setCopiedId] = useState(null)

  const reload = useCallback(async () => {
    try { setRefs(await getKBCaseReferences(caseId)) } catch {}
  }, [caseId])

  useEffect(() => { reload() }, [reload])

  async function handleAdd() {
    if (!system.trim() || !refId.trim()) return
    setError(null)
    const isDup = refs.some(r =>
      r.system.toLowerCase() === system.trim().toLowerCase() &&
      r.reference_id.toLowerCase() === refId.trim().toLowerCase()
    )
    if (isDup) {
      setError("이미 등록된 외부 참조 ID 입니다.")
      return
    }
    try {
      await addKBCaseReference(caseId, { system: system.trim(), reference_id: refId.trim() })
      setSystem("")
      setRefId("")
      await reload()
    } catch (e) {
      setError(e.message || "추가 실패")
    }
  }

  async function handleDelete(rid) {
    setError(null)
    try {
      await deleteKBCaseReference(caseId, rid)
      await reload()
    } catch (e) {
      setError(e.message || "삭제 실패")
    }
  }

  async function handleCopy(r) {
    try {
      await navigator.clipboard.writeText(r.reference_id)
      setCopiedId(r.id)
      setTimeout(() => setCopiedId(prev => (prev === r.id ? null : prev)), 1500)
    } catch {
      setError("클립보드 복사에 실패했습니다. (HTTPS 또는 localhost에서만 지원)")
    }
  }

  return (
    <div className="pm-field" style={{ marginTop: 8 }}>
      <span className="pm-field-label">외부 참조 ID</span>
      {error && <div className="settings-error" style={{ fontSize: 12 }}>{error}</div>}
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {refs.map(r => (
          <div key={r.id} style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span
              className="pm-ref-tag selected"
              style={{ cursor: "pointer" }}
              title="클릭하면 참조 ID가 클립보드에 복사됩니다"
              onClick={() => handleCopy(r)}
            >{copiedId === r.id ? "복사됨 ✓" : `${r.system}: ${r.reference_id}`}</span>
            <button className="settings-btn-sm pm-danger" onClick={() => handleDelete(r.id)}>삭제</button>
          </div>
        ))}
      </div>
      <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
        <input
          className="pm-field"
          style={{ flex: 1, padding: "6px 8px", fontSize: 12, border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", background: "var(--surface)", color: "var(--text)" }}
          placeholder="시스템 (예: Jira)"
          value={system}
          onChange={e => setSystem(e.target.value)}
        />
        <input
          style={{ flex: 2, padding: "6px 8px", fontSize: 12, border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", background: "var(--surface)", color: "var(--text)" }}
          placeholder="참조 ID (예: DTV-1234)"
          value={refId}
          onChange={e => setRefId(e.target.value)}
          onKeyDown={e => e.key === "Enter" && handleAdd()}
        />
        <button className="settings-btn-sm" onClick={handleAdd} disabled={!system.trim() || !refId.trim()}>추가</button>
      </div>
    </div>
  )
}

// ─── 케이스 상세 모달 ────────────────────────────────────────────────────────

function CaseDetailModal({ caseId, allPatterns, onClose, onUpdated, onDeleted }) {
  const [caseData, setCaseData] = useState(null)
  const [editMode, setEditMode] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)

  const loadCase = useCallback(async () => {
    try {
      setCaseData(await getKBCase(caseId))
    } catch {
      setError("케이스를 불러오지 못했습니다.")
    }
  }, [caseId])

  useEffect(() => { loadCase() }, [loadCase])

  // Esc 닫기 — 수정 중에는 무시
  useEffect(() => {
    function onKey(e) { if (e.key === "Escape" && !editMode) onClose() }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose, editMode])

  async function handleSubmit(data, linkedPatternIds) {
    setSubmitting(true)
    setError(null)
    try {
      await updateKBCase(caseId, data)
      const current = await getKBCasePatterns(caseId)
      const currentIds = current.map(p => p.id)
      for (const pid of linkedPatternIds) {
        if (!currentIds.includes(pid)) await linkKBCasePattern(caseId, pid)
      }
      for (const pid of currentIds) {
        if (!linkedPatternIds.includes(pid)) await unlinkKBCasePattern(caseId, pid)
      }
      setEditMode(false)
      await loadCase()
      onUpdated()
    } catch (e) {
      setError(e.message || "저장에 실패했습니다.")
    } finally {
      setSubmitting(false)
    }
  }

  async function handleDelete() {
    if (!window.confirm(`케이스 '${caseData.name}' 을 삭제할까요?\nChromaDB 임베딩도 함께 제거됩니다.`)) return
    try {
      await deleteKBCase(caseId)
      onDeleted()
    } catch (e) {
      setError(e.message || "삭제에 실패했습니다.")
    }
  }

  // 수정 중 닫기 방지 — 바깥 클릭/Esc 무시, ✕는 확인 후 닫기
  function handleClose() {
    if (editMode && !window.confirm("수정 중인 내용이 사라집니다. 닫으시겠습니까?")) return
    onClose()
  }

  return (
    <div className="as-modal-overlay" onClick={e => e.target === e.currentTarget && !editMode && onClose()}>
      <div className="as-modal" style={{ maxWidth: 780 }}>
        <div className="as-modal-header">
          <span className="as-modal-title">
            {caseData ? `#${caseData.id} ${caseData.name}` : "케이스 상세"}
          </span>
          <button className="as-modal-close" onClick={handleClose}>✕</button>
        </div>

        <div className="as-modal-body">
          {error && <div className="settings-error">{error}</div>}

          {!caseData ? (
            <div className="pm-loading">불러오는 중...</div>
          ) : editMode ? (
            <CaseForm
              initial={caseData}
              allPatterns={allPatterns}
              onSubmit={handleSubmit}
              onCancel={() => { setEditMode(false); setError(null) }}
              submitting={submitting}
              caseId={caseId}
            />
          ) : (
            <>
              {/* 읽기 모드 */}
              <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                {/* 판정·상태 요약 */}
                <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                  <VerdictBadge verdict={caseData.verdict} />
                  <StatusBadge caseData={caseData} />
                  {caseData.analysis_date && (
                    <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                      분석 일자: {caseData.analysis_date}
                    </span>
                  )}
                </div>
                <ReportSummary caseData={caseData} />
                {caseData.description && (
                  <div>
                    <div className="pm-field-label" style={{ marginBottom: 4 }}>설명</div>
                    <div className="pm-item-desc" style={{ fontSize: 13 }}>{caseData.description}</div>
                  </div>
                )}
                {caseData.analysis && (
                  <div>
                    <div className="pm-field-label" style={{ marginBottom: 4 }}>분석 내용</div>
                    <div className="pm-item-desc" style={{ fontSize: 13 }}>{caseData.analysis}</div>
                  </div>
                )}
                <ActionsSummary actions={caseData.actions} />
                {caseData.notes && (
                  <div>
                    <div className="pm-field-label" style={{ marginBottom: 4 }}>특이사항 / 비고</div>
                    <div className="pm-item-desc" style={{ fontSize: 13 }}>{caseData.notes}</div>
                  </div>
                )}
                {(caseData.keywords?.length > 0 || caseData.chip_tags?.length > 0) && (
                  <div>
                    <div className="pm-field-label" style={{ marginBottom: 6 }}>태그</div>
                    <div className="pm-item-tags">
                      {(caseData.chip_tags || []).map(t => (
                        <span key={t} className="keyword-tag">{t}</span>
                      ))}
                      {(caseData.keywords || []).map(k => (
                        <span key={k} className="keyword-tag" style={{ opacity: 0.7 }}>{k}</span>
                      ))}
                    </div>
                  </div>
                )}
                {caseData.profile_refs?.length > 0 && (
                  <div>
                    <div className="pm-field-label" style={{ marginBottom: 6 }}>Profile Refs</div>
                    <div className="pm-item-tags">
                      {caseData.profile_refs.map(r => (
                        <span key={r} className="pm-ref-tag selected">{r}</span>
                      ))}
                    </div>
                  </div>
                )}
                {caseData.patterns?.length > 0 && (
                  <div>
                    <div className="pm-field-label" style={{ marginBottom: 6 }}>연결 패턴</div>
                    <div className="pm-item-tags">
                      {caseData.patterns.map(p => (
                        <span key={p.id} className="pm-ref-tag selected">[{p.type}] {p.name}</span>
                      ))}
                    </div>
                  </div>
                )}
                <ReferencesSection caseId={caseId} />
                <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                  생성: {caseData.created_at} · 수정: {caseData.updated_at}
                </div>
              </div>
            </>
          )}
        </div>

        {!editMode && caseData && (
          <div className="as-modal-footer" style={{ gap: 8 }}>
            <button className="settings-btn-sm pm-danger" onClick={handleDelete}>삭제</button>
            <div style={{ flex: 1 }} />
            <button className="settings-btn-sm" onClick={onClose}>닫기</button>
            <button className="settings-save-btn" onClick={() => setEditMode(true)}>수정</button>
          </div>
        )}
      </div>
    </div>
  )
}

// ─── 케이스 탭 ────────────────────────────────────────────────────────────────

function CasesTab() {
  const [cases, setCases] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [creating, setCreating] = useState(false)   // 새 케이스 생성 폼 표시
  const [submitting, setSubmitting] = useState(false)
  const [allPatterns, setAllPatterns] = useState([])
  const [syncing, setSyncing] = useState(false)
  const [detailId, setDetailId] = useState(null)    // 상세 모달에 열린 케이스 id
  const [searchQuery, setSearchQuery] = useState("")
  const [selectedProfile, setSelectedProfile] = useState("")

  const reload = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [cs, ps] = await Promise.all([getKBCases(), getPatterns()])
      setCases(cs)
      setAllPatterns(ps)
    } catch {
      setError("케이스 목록을 불러오지 못했습니다.")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { reload() }, [reload])

  const allProfiles = useMemo(() => {
    const seen = new Set()
    for (const c of cases) {
      for (const p of (c.profile_refs || [])) seen.add(p)
    }
    return [...seen].sort()
  }, [cases])

  const filteredCases = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    return cases.filter(c => {
      if (q && !c.name.toLowerCase().includes(q)) return false
      if (selectedProfile && !(c.profile_refs || []).includes(selectedProfile)) return false
      return true
    })
  }, [cases, searchQuery, selectedProfile])

  async function handleCreateSubmit(data, linkedPatternIds) {
    setSubmitting(true)
    setError(null)
    try {
      const created = await createKBCase(data)
      for (const pid of linkedPatternIds) {
        await linkKBCasePattern(created.id, pid)
      }
      setCreating(false)
      await reload()
    } catch (e) {
      const msg = e.message || "저장에 실패했습니다."
      setError(msg)
      window.alert(`케이스 저장 실패:\n${msg}`)
    } finally {
      setSubmitting(false)
    }
  }

  async function handleSync() {
    if (!window.confirm("ChromaDB 전체 동기화를 실행할까요?\nSQLite 전체 케이스를 재임베딩합니다.")) return
    setSyncing(true)
    setError(null)
    try {
      const result = await syncKBCases()
      alert(`동기화 완료: ${result.synced}개 케이스`)
    } catch (e) {
      setError(e.message || "동기화에 실패했습니다.")
    } finally {
      setSyncing(false)
    }
  }

  if (loading) return <div className="pm-loading">불러오는 중...</div>

  return (
    <div className="pm-tab">
      {error && <div className="settings-error">{error}</div>}

      {creating ? (
        <div className="pm-edit-wrap">
          <div className="pm-edit-title">새 케이스</div>
          <CaseForm
            initial={{ ...EMPTY_CASE, patterns: [] }}
            allPatterns={allPatterns}
            onSubmit={handleCreateSubmit}
            onCancel={() => { setCreating(false); setError(null) }}
            submitting={submitting}
          />
        </div>
      ) : (
        <>
          <div className="pm-list-header">
            <span className="pm-count">케이스 {filteredCases.length}/{cases.length}개</span>
            <div style={{ display: "flex", gap: 8 }}>
              <button className="settings-btn-sm" onClick={handleSync} disabled={syncing}>
                {syncing ? "동기화 중..." : "ChromaDB 동기화"}
              </button>
              <button className="settings-save-btn" onClick={() => setCreating(true)}>+ 새 케이스</button>
            </div>
          </div>

          <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
            <div className="pm-field" style={{ flex: 1, flexDirection: "row", marginBottom: 0 }}>
              <input
                type="text"
                placeholder="케이스 제목 검색..."
                value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
              />
            </div>
            <select
              value={selectedProfile}
              onChange={e => setSelectedProfile(e.target.value)}
              style={{ minWidth: 140, padding: "4px 8px", borderRadius: 4, border: "1px solid var(--border)", background: "var(--bg-secondary)", color: "var(--text)", fontSize: 13 }}
            >
              <option value="">전체 프로파일</option>
              {allProfiles.map(p => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
            {(searchQuery || selectedProfile) && (
              <button
                className="settings-btn-sm"
                onClick={() => { setSearchQuery(""); setSelectedProfile("") }}
              >초기화</button>
            )}
          </div>

          {filteredCases.length === 0 ? (
            <div className="pm-empty">{cases.length === 0 ? "등록된 케이스가 없습니다." : "검색 결과가 없습니다."}</div>
          ) : (
            <div className="pm-list">
              {filteredCases.map(c => (
                <div
                  key={c.id}
                  className="pm-item"
                  style={{ cursor: "pointer" }}
                  onClick={() => setDetailId(c.id)}
                >
                  <div className="pm-item-main">
                    <div className="pm-item-name">
                      <span style={{ color: "var(--text-muted)", fontSize: 11 }}>#{c.id}</span>
                      {c.name}
                      <VerdictBadge verdict={c.verdict} />
                      <StatusBadge caseData={c} />
                    </div>
                    <div className="pm-item-tags">
                      {(c.profile_refs || []).map(p => (
                        <span key={p} className="pm-ref-tag selected" onClick={e => e.stopPropagation()}>{p}</span>
                      ))}
                      {(c.chip_tags || []).map(t => (
                        <span key={t} className="keyword-tag" onClick={e => e.stopPropagation()}>{t}</span>
                      ))}
                      {(c.keywords || []).map(k => (
                        <span key={k} className="keyword-tag" style={{ opacity: 0.7 }} onClick={e => e.stopPropagation()}>{k}</span>
                      ))}
                    </div>
                  </div>
                  <div className="pm-item-actions" onClick={e => e.stopPropagation()}>
                    <span style={{ fontSize: 11, color: "var(--text-muted)" }}>클릭하여 상세 보기</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {detailId && (
        <CaseDetailModal
          caseId={detailId}
          allPatterns={allPatterns}
          onClose={() => setDetailId(null)}
          onUpdated={() => reload()}
          onDeleted={() => { setDetailId(null); reload() }}
        />
      )}
    </div>
  )
}

// ─── 패턴 폼 ─────────────────────────────────────────────────────────────────

const PATTERN_TYPES = ["PRESENCE", "SEQUENCE", "WINDOW", "ABSENCE", "COMPOSITE"]
const OPERATORS = ["AND", "OR", "NOT"]

const EMPTY_PATTERN = {
  name: "", type: "PRESENCE", description: "", keywords: [],
  weight: 1.0, analysis_guidelines: "", chip_tags: [],
  pattern: "", event_dedup_window_sec: "",
  steps: [], step_dedup: false, non_overlapping: false,
  window_sec: "", count_threshold: "", count_unique_only: false,
  trigger_pattern: "", absent_pattern: "",
  operator: "AND", components: [],
}

function PatternForm({ initial, allPatternNames, onSubmit, onCancel, submitting }) {
  const [d, setD] = useState({ ...EMPTY_PATTERN, ...initial,
    keywords: (initial.keywords || []).join(", "),
    chip_tags: (initial.chip_tags || []).join(", "),
    steps: (initial.steps || []).join("\n"),
    components: initial.components || [],
  })

  function set(key, val) { setD(prev => ({ ...prev, [key]: val })) }

  function toggleComponent(name) {
    setD(prev => ({
      ...prev,
      components: prev.components.includes(name)
        ? prev.components.filter(n => n !== name)
        : [...prev.components, name],
    }))
  }

  function submit() {
    onSubmit({
      name: d.name.trim(),
      type: d.type,
      description: d.description,
      keywords: parseList(d.keywords),
      weight: parseFloat(d.weight) || 1.0,
      analysis_guidelines: d.analysis_guidelines,
      chip_tags: parseList(d.chip_tags),
      pattern: d.pattern || null,
      event_dedup_window_sec: d.event_dedup_window_sec !== "" ? parseFloat(d.event_dedup_window_sec) : null,
      steps: d.steps ? d.steps.split("\n").map(s => s.trim()).filter(Boolean) : [],
      step_dedup: d.step_dedup,
      non_overlapping: d.non_overlapping,
      window_sec: d.window_sec !== "" ? parseFloat(d.window_sec) : null,
      count_threshold: d.count_threshold !== "" ? parseInt(d.count_threshold) : null,
      count_unique_only: d.count_unique_only,
      trigger_pattern: d.trigger_pattern || null,
      absent_pattern: d.absent_pattern || null,
      operator: d.operator || null,
      components: d.components,
    })
  }

  return (
    <div className="pm-form">
      {/* 공통 필드 */}
      <label className="pm-field">
        <span className="pm-field-label">이름 *</span>
        <input value={d.name} onChange={e => set("name", e.target.value)} placeholder="NVMe timeout" />
      </label>
      <label className="pm-field">
        <span className="pm-field-label">타입</span>
        <select value={d.type} onChange={e => set("type", e.target.value)}>
          {PATTERN_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
        </select>
      </label>
      <label className="pm-field">
        <span className="pm-field-label">설명</span>
        <input value={d.description} onChange={e => set("description", e.target.value)} />
      </label>
      <label className="pm-field">
        <span className="pm-field-label">키워드 (콤마 구분)</span>
        <input value={d.keywords} onChange={e => set("keywords", e.target.value)} placeholder="ata, nvme, timeout" />
      </label>
      <label className="pm-field">
        <span className="pm-field-label">가중치</span>
        <input type="number" step="0.1" value={d.weight} onChange={e => set("weight", e.target.value)} />
      </label>
      <label className="pm-field">
        <span className="pm-field-label">Chip Tags (콤마 구분)</span>
        <input value={d.chip_tags} onChange={e => set("chip_tags", e.target.value)} placeholder="RheaM, RoseM" />
      </label>

      {/* 타입별 필드 */}
      {d.type === "PRESENCE" && (
        <>
          <label className="pm-field">
            <span className="pm-field-label">Regex 패턴</span>
            <input value={d.pattern} onChange={e => set("pattern", e.target.value)} placeholder="nvme.*timeout" />
          </label>
          <label className="pm-field">
            <span className="pm-field-label">Dedup 윈도우 (초)</span>
            <input type="number" step="0.1" value={d.event_dedup_window_sec}
              onChange={e => set("event_dedup_window_sec", e.target.value)} placeholder="0" />
          </label>
        </>
      )}
      {d.type === "SEQUENCE" && (
        <>
          <label className="pm-field">
            <span className="pm-field-label">Steps (줄 단위 regex)</span>
            <textarea value={d.steps} onChange={e => set("steps", e.target.value)} rows={4}
              placeholder={"step1 regex\nstep2 regex\nstep3 regex"} />
          </label>
          <div style={{ display: "flex", gap: 16 }}>
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
              <input type="checkbox" checked={d.step_dedup} onChange={e => set("step_dedup", e.target.checked)} />
              Step Dedup
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
              <input type="checkbox" checked={d.non_overlapping} onChange={e => set("non_overlapping", e.target.checked)} />
              Non-overlapping
            </label>
          </div>
        </>
      )}
      {d.type === "WINDOW" && (
        <>
          <label className="pm-field">
            <span className="pm-field-label">Regex 패턴</span>
            <input value={d.pattern} onChange={e => set("pattern", e.target.value)} />
          </label>
          <div style={{ display: "flex", gap: 12 }}>
            <label className="pm-field" style={{ flex: 1 }}>
              <span className="pm-field-label">Window (초)</span>
              <input type="number" step="1" value={d.window_sec} onChange={e => set("window_sec", e.target.value)} />
            </label>
            <label className="pm-field" style={{ flex: 1 }}>
              <span className="pm-field-label">Count Threshold</span>
              <input type="number" step="1" value={d.count_threshold} onChange={e => set("count_threshold", e.target.value)} />
            </label>
          </div>
          <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
            <input type="checkbox" checked={d.count_unique_only} onChange={e => set("count_unique_only", e.target.checked)} />
            Unique Only
          </label>
        </>
      )}
      {d.type === "ABSENCE" && (
        <>
          <label className="pm-field">
            <span className="pm-field-label">Trigger 패턴</span>
            <input value={d.trigger_pattern} onChange={e => set("trigger_pattern", e.target.value)} />
          </label>
          <label className="pm-field">
            <span className="pm-field-label">Absent 패턴</span>
            <input value={d.absent_pattern} onChange={e => set("absent_pattern", e.target.value)} />
          </label>
          <label className="pm-field">
            <span className="pm-field-label">Window (초)</span>
            <input type="number" step="1" value={d.window_sec} onChange={e => set("window_sec", e.target.value)} />
          </label>
        </>
      )}
      {d.type === "COMPOSITE" && (
        <>
          <label className="pm-field">
            <span className="pm-field-label">연산자</span>
            <select value={d.operator} onChange={e => set("operator", e.target.value)}>
              {OPERATORS.map(op => <option key={op} value={op}>{op}</option>)}
            </select>
          </label>
          <div className="pm-field">
            <span className="pm-field-label">구성 패턴</span>
            {allPatternNames.length === 0 ? (
              <span className="pm-hint">다른 패턴이 없습니다.</span>
            ) : (
              <div className="pm-ref-tags">
                {allPatternNames.map(n => (
                  <button key={n} type="button"
                    className={`pm-ref-tag ${d.components.includes(n) ? "selected" : ""}`}
                    onClick={() => toggleComponent(n)}
                  >{n}</button>
                ))}
              </div>
            )}
          </div>
        </>
      )}

      <label className="pm-field">
        <span className="pm-field-label">분석 지침</span>
        <textarea value={d.analysis_guidelines} onChange={e => set("analysis_guidelines", e.target.value)} rows={3} />
      </label>

      <div className="pm-form-actions">
        <button className="settings-btn-sm" onClick={onCancel} disabled={submitting}>취소</button>
        <button className="settings-save-btn" onClick={submit} disabled={submitting || !d.name.trim()}>
          {submitting ? "저장 중..." : "저장"}
        </button>
      </div>
    </div>
  )
}

// ─── 패턴 상세 모달 ──────────────────────────────────────────────────────────

const TYPE_COLORS = {
  PRESENCE:  { bg: "#e8f5e9", color: "#2e7d32", border: "#a5d6a7" },
  SEQUENCE:  { bg: "#e3f2fd", color: "#1565c0", border: "#90caf9" },
  WINDOW:    { bg: "#fff3e0", color: "#e65100", border: "#ffcc80" },
  ABSENCE:   { bg: "#fce4ec", color: "#880e4f", border: "#f48fb1" },
  COMPOSITE: { bg: "#f3e5f5", color: "#6a1b9a", border: "#ce93d8" },
}

function PatternDetailModal({ patternId, allPatternNames, onClose, onUpdated, onDeleted }) {
  const [patternData, setPatternData] = useState(null)
  const [editMode, setEditMode] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)

  const loadPattern = useCallback(async () => {
    try {
      setPatternData(await getPattern(patternId))
    } catch {
      setError("패턴을 불러오지 못했습니다.")
    }
  }, [patternId])

  useEffect(() => { loadPattern() }, [loadPattern])

  // Esc 닫기 — 수정 중에는 무시
  useEffect(() => {
    function onKey(e) { if (e.key === "Escape" && !editMode) onClose() }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose, editMode])

  async function handleSubmit(data) {
    setSubmitting(true)
    setError(null)
    try {
      await updatePattern(patternId, data)
      setEditMode(false)
      await loadPattern()
      onUpdated()
    } catch (e) {
      setError(e.message || "저장에 실패했습니다.")
    } finally {
      setSubmitting(false)
    }
  }

  async function handleDelete() {
    if (!window.confirm(`패턴 '${patternData.name}' 을 삭제할까요?\n참조하는 COMPOSITE 패턴도 함께 삭제될 수 있습니다.`)) return
    try {
      const result = await deletePattern(patternId)
      if (result.deleted_composites?.length) {
        alert(`함께 삭제된 COMPOSITE 패턴: ${result.deleted_composites.join(", ")}`)
      }
      onDeleted()
    } catch (e) {
      setError(e.message || "삭제에 실패했습니다.")
    }
  }

  const typeStyle = patternData ? (TYPE_COLORS[patternData.type] || {}) : {}

  // 수정 중 닫기 방지 — 바깥 클릭/Esc 무시, ✕는 확인 후 닫기
  function handleClose() {
    if (editMode && !window.confirm("수정 중인 내용이 사라집니다. 닫으시겠습니까?")) return
    onClose()
  }

  return (
    <div className="as-modal-overlay" onClick={e => e.target === e.currentTarget && !editMode && onClose()}>
      <div className="as-modal" style={{ maxWidth: 780 }}>
        <div className="as-modal-header">
          <span className="as-modal-title">
            {patternData ? (
              <>
                <span style={{ fontSize: 11, color: "var(--text-muted)" }}>#{patternData.id}</span>
                {" "}
                <span style={{ fontSize: 11, fontWeight: 700, padding: "2px 8px", borderRadius: 100,
                  background: typeStyle.bg, color: typeStyle.color, border: `1px solid ${typeStyle.border}` }}>
                  {patternData.type}
                </span>
                {" "}{patternData.name}
              </>
            ) : "패턴 상세"}
          </span>
          <button className="as-modal-close" onClick={handleClose}>✕</button>
        </div>

        <div className="as-modal-body">
          {error && <div className="settings-error">{error}</div>}

          {!patternData ? (
            <div className="pm-loading">불러오는 중...</div>
          ) : editMode ? (
            <PatternForm
              initial={patternData}
              allPatternNames={allPatternNames.filter(n => n !== patternData.name)}
              onSubmit={handleSubmit}
              onCancel={() => { setEditMode(false); setError(null) }}
              submitting={submitting}
            />
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              {/* 공통 메타 */}
              <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
                <div><span className="pm-field-label">가중치</span> <span style={{ fontSize: 13 }}>{patternData.weight}</span></div>
              </div>

              {patternData.description && (
                <div>
                  <div className="pm-field-label" style={{ marginBottom: 4 }}>설명</div>
                  <div className="pm-item-desc" style={{ fontSize: 13 }}>{patternData.description}</div>
                </div>
              )}

              {/* 타입별 세부 필드 */}
              {(patternData.type === "PRESENCE") && (
                <>
                  {patternData.pattern && (
                    <div>
                      <div className="pm-field-label" style={{ marginBottom: 4 }}>Regex 패턴</div>
                      <code style={{ fontSize: 12, background: "var(--bg-secondary)", padding: "4px 8px", borderRadius: 4, display: "block" }}>{patternData.pattern}</code>
                    </div>
                  )}
                  {patternData.event_dedup_window_sec != null && (
                    <div><span className="pm-field-label">Dedup 윈도우</span> <span style={{ fontSize: 13 }}>{patternData.event_dedup_window_sec}초</span></div>
                  )}
                </>
              )}
              {patternData.type === "SEQUENCE" && (
                <>
                  {patternData.steps?.length > 0 && (
                    <div>
                      <div className="pm-field-label" style={{ marginBottom: 4 }}>Steps</div>
                      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                        {patternData.steps.map((s, i) => (
                          <code key={i} style={{ fontSize: 12, background: "var(--bg-secondary)", padding: "3px 8px", borderRadius: 4 }}>
                            {i + 1}. {s}
                          </code>
                        ))}
                      </div>
                    </div>
                  )}
                  <div style={{ display: "flex", gap: 16 }}>
                    {patternData.step_dedup && <span style={{ fontSize: 12 }}>Step Dedup: ON</span>}
                    {patternData.non_overlapping && <span style={{ fontSize: 12 }}>Non-overlapping: ON</span>}
                  </div>
                </>
              )}
              {patternData.type === "WINDOW" && (
                <>
                  {patternData.pattern && (
                    <div>
                      <div className="pm-field-label" style={{ marginBottom: 4 }}>Regex 패턴</div>
                      <code style={{ fontSize: 12, background: "var(--bg-secondary)", padding: "4px 8px", borderRadius: 4, display: "block" }}>{patternData.pattern}</code>
                    </div>
                  )}
                  <div style={{ display: "flex", gap: 16 }}>
                    {patternData.window_sec != null && <div><span className="pm-field-label">Window</span> <span style={{ fontSize: 13 }}>{patternData.window_sec}초</span></div>}
                    {patternData.count_threshold != null && <div><span className="pm-field-label">Count Threshold</span> <span style={{ fontSize: 13 }}>{patternData.count_threshold}</span></div>}
                    {patternData.count_unique_only && <span style={{ fontSize: 12 }}>Unique Only: ON</span>}
                  </div>
                </>
              )}
              {patternData.type === "ABSENCE" && (
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  {patternData.trigger_pattern && (
                    <div>
                      <div className="pm-field-label" style={{ marginBottom: 4 }}>Trigger 패턴</div>
                      <code style={{ fontSize: 12, background: "var(--bg-secondary)", padding: "4px 8px", borderRadius: 4, display: "block" }}>{patternData.trigger_pattern}</code>
                    </div>
                  )}
                  {patternData.absent_pattern && (
                    <div>
                      <div className="pm-field-label" style={{ marginBottom: 4 }}>Absent 패턴</div>
                      <code style={{ fontSize: 12, background: "var(--bg-secondary)", padding: "4px 8px", borderRadius: 4, display: "block" }}>{patternData.absent_pattern}</code>
                    </div>
                  )}
                  {patternData.window_sec != null && <div><span className="pm-field-label">Window</span> <span style={{ fontSize: 13 }}>{patternData.window_sec}초</span></div>}
                </div>
              )}
              {patternData.type === "COMPOSITE" && (
                <div>
                  <div className="pm-field-label" style={{ marginBottom: 6 }}>구성 패턴 ({patternData.operator})</div>
                  <div className="pm-item-tags">
                    {(patternData.components || []).map(n => (
                      <span key={n} className="pm-ref-tag selected">{n}</span>
                    ))}
                  </div>
                </div>
              )}

              {/* 공통 태그 */}
              {((patternData.keywords?.length > 0) || (patternData.chip_tags?.length > 0)) && (
                <div>
                  <div className="pm-field-label" style={{ marginBottom: 6 }}>태그</div>
                  <div className="pm-item-tags">
                    {(patternData.chip_tags || []).map(t => (
                      <span key={t} className="keyword-tag">{t}</span>
                    ))}
                    {(patternData.keywords || []).map(k => (
                      <span key={k} className="keyword-tag" style={{ opacity: 0.7 }}>{k}</span>
                    ))}
                  </div>
                </div>
              )}

              {patternData.analysis_guidelines && (
                <div>
                  <div className="pm-field-label" style={{ marginBottom: 4 }}>분석 지침</div>
                  <div className="pm-item-desc" style={{ fontSize: 13 }}>{patternData.analysis_guidelines}</div>
                </div>
              )}
            </div>
          )}
        </div>

        {!editMode && patternData && (
          <div className="as-modal-footer" style={{ gap: 8 }}>
            <button className="settings-btn-sm pm-danger" onClick={handleDelete}>삭제</button>
            <div style={{ flex: 1 }} />
            <button className="settings-btn-sm" onClick={onClose}>닫기</button>
            <button className="settings-save-btn" onClick={() => setEditMode(true)}>수정</button>
          </div>
        )}
      </div>
    </div>
  )
}

// ─── 패턴 탭 ─────────────────────────────────────────────────────────────────

function PatternsTab() {
  const [patterns, setPatterns] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [editing, setEditing] = useState(null)    // null | "__new__"
  const [submitting, setSubmitting] = useState(false)
  const [typeFilter, setTypeFilter] = useState("전체")
  const [detailId, setDetailId] = useState(null)

  const reload = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setPatterns(await getPatterns())
    } catch {
      setError("패턴 목록을 불러오지 못했습니다.")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { reload() }, [reload])

  function startNew() {
    setEditing("__new__")
  }

  async function handleSubmit(data) {
    setSubmitting(true)
    setError(null)
    try {
      await createPattern(data)
      setEditing(null)
      await reload()
    } catch (e) {
      setError(e.message || "저장에 실패했습니다.")
    } finally {
      setSubmitting(false)
    }
  }

  const allPatternNames = patterns.map(p => p.name)
  const filtered = typeFilter === "전체" ? patterns : patterns.filter(p => p.type === typeFilter)

  if (loading) return <div className="pm-loading">불러오는 중...</div>

  return (
    <div className="pm-tab">
      {error && <div className="settings-error">{error}</div>}

      {editing === "__new__" ? (
        <div className="pm-edit-wrap">
          <div className="pm-edit-title">새 패턴</div>
          <PatternForm
            initial={{ ...EMPTY_PATTERN }}
            allPatternNames={allPatternNames}
            onSubmit={handleSubmit}
            onCancel={() => setEditing(null)}
            submitting={submitting}
          />
        </div>
      ) : (
        <>
          <div className="pm-list-header">
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <span className="pm-count">패턴 {patterns.length}개</span>
              <div style={{ display: "flex", gap: 4 }}>
                {["전체", ...PATTERN_TYPES].map(t => (
                  <button
                    key={t}
                    className={`pm-tab-btn ${typeFilter === t ? "active" : ""}`}
                    style={{ padding: "4px 10px", fontSize: 11 }}
                    onClick={() => setTypeFilter(t)}
                  >{t}</button>
                ))}
              </div>
            </div>
            <button className="settings-save-btn" onClick={startNew}>+ 새 패턴</button>
          </div>

          {filtered.length === 0 ? (
            <div className="pm-empty">등록된 패턴이 없습니다.</div>
          ) : (
            <div className="pm-list">
              {filtered.map(p => {
                const style = TYPE_COLORS[p.type] || {}
                return (
                  <div
                    key={p.id}
                    className="pm-item"
                    style={{ cursor: "pointer" }}
                    onClick={() => setDetailId(p.id)}
                  >
                    <div className="pm-item-main">
                      <div className="pm-item-name">
                        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>#{p.id}</span>
                        <span
                          style={{ fontSize: 10, fontWeight: 700, padding: "2px 8px", borderRadius: 100,
                            background: style.bg, color: style.color, border: `1px solid ${style.border}` }}
                        >{p.type}</span>
                        {p.name}
                      </div>
                      {p.description && <div className="pm-item-desc">{p.description}</div>}
                      <div className="pm-item-tags">
                        <span className="pm-item-desc">weight: {p.weight}</span>
                        {(p.chip_tags || []).map(t => (
                          <span key={t} className="keyword-tag">{t}</span>
                        ))}
                      </div>
                    </div>
                    <div className="pm-item-actions" onClick={e => e.stopPropagation()}>
                      <span style={{ fontSize: 11, color: "var(--text-muted)" }}>클릭하여 상세 보기</span>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </>
      )}

      {detailId && (
        <PatternDetailModal
          patternId={detailId}
          allPatternNames={allPatternNames}
          onClose={() => setDetailId(null)}
          onUpdated={() => reload()}
          onDeleted={() => { setDetailId(null); reload() }}
        />
      )}
    </div>
  )
}

// ─── 페이지 ──────────────────────────────────────────────────────────────────

export default function CaseManagePage() {
  const navigate = useNavigate()
  const [tab, setTab] = useState("cases")

  return (
    <div className="settings-page">
      <div className="settings-header">
        <button className="settings-back-btn" onClick={() => navigate(-1)}>← 돌아가기</button>
        <span className="settings-header-title">Case 관리</span>
      </div>

      <div className="pm-tabs">
        <button className={`pm-tab-btn ${tab === "cases" ? "active" : ""}`} onClick={() => setTab("cases")}>
          케이스
        </button>
        <button className={`pm-tab-btn ${tab === "patterns" ? "active" : ""}`} onClick={() => setTab("patterns")}>
          패턴
        </button>
      </div>

      <div className="settings-body">
        {tab === "cases" ? <CasesTab /> : <PatternsTab />}
      </div>
    </div>
  )
}
