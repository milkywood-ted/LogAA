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

// ─── 케이스 폼 ────────────────────────────────────────────────────────────────

const EMPTY_CASE = {
  name: "", description: "", analysis: "",
  keywords: [], profile_refs: [], chip_tags: [],
}

function CaseForm({ initial, allPatterns, onSubmit, onCancel, submitting }) {
  const [name, setName] = useState(initial.name)
  const [description, setDescription] = useState(initial.description)
  const [analysis, setAnalysis] = useState(initial.analysis)
  const [keywords, setKeywords] = useState((initial.keywords || []).join(", "))
  const [chipTags, setChipTags] = useState(initial.chip_tags || [])
  const [profileRefs, setProfileRefs] = useState((initial.profile_refs || []).join(", "))
  // 연결 패턴은 별도 관리 (케이스 ID 있을 때만 사용)
  const [linkedPatternIds, setLinkedPatternIds] = useState(
    (initial.patterns || []).map(p => p.id)
  )

  function togglePattern(pid) {
    setLinkedPatternIds(prev =>
      prev.includes(pid) ? prev.filter(id => id !== pid) : [...prev, pid]
    )
  }

  function submit() {
    onSubmit(
      {
        name: name.trim(),
        description,
        analysis,
        keywords: parseList(keywords),
        chip_tags: chipTags,
        profile_refs: parseList(profileRefs),
      },
      linkedPatternIds,
    )
  }

  return (
    <div className="pm-form">
      <label className="pm-field">
        <span className="pm-field-label">이름 *</span>
        <input value={name} onChange={e => setName(e.target.value)} placeholder="NVMe 초기화 타임아웃" />
      </label>
      <label className="pm-field">
        <span className="pm-field-label">설명</span>
        <textarea value={description} onChange={e => setDescription(e.target.value)} rows={3}
          placeholder="문제 현상 요약 — BGE-M3 임베딩 대상" />
      </label>
      <label className="pm-field">
        <span className="pm-field-label">분석 내용</span>
        <textarea value={analysis} onChange={e => setAnalysis(e.target.value)} rows={4}
          placeholder="원인 및 해결 방법 요약" />
      </label>
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
        <input value={profileRefs} onChange={e => setProfileRefs(e.target.value)} placeholder="DTV FRC, DTV DP" />
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
      <div className="pm-form-actions">
        <button className="settings-btn-sm" onClick={onCancel} disabled={submitting}>취소</button>
        <button className="settings-save-btn" onClick={submit} disabled={submitting || !name.trim()}>
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

  const reload = useCallback(async () => {
    try { setRefs(await getKBCaseReferences(caseId)) } catch {}
  }, [caseId])

  useEffect(() => { reload() }, [reload])

  async function handleAdd() {
    if (!system.trim() || !refId.trim()) return
    setError(null)
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

  return (
    <div className="pm-field" style={{ marginTop: 8 }}>
      <span className="pm-field-label">외부 참조 ID</span>
      {error && <div className="settings-error" style={{ fontSize: 12 }}>{error}</div>}
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {refs.map(r => (
          <div key={r.id} style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span className="pm-ref-tag selected">{r.system}: {r.reference_id}</span>
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

  // Esc 닫기
  useEffect(() => {
    function onKey(e) { if (e.key === "Escape") onClose() }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose])

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

  return (
    <div className="as-modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="as-modal" style={{ maxWidth: 780 }}>
        <div className="as-modal-header">
          <span className="as-modal-title">
            {caseData ? `#${caseData.id} ${caseData.name}` : "케이스 상세"}
          </span>
          <button className="as-modal-close" onClick={onClose}>✕</button>
        </div>

        <div className="as-modal-body">
          {error && <div className="settings-error">{error}</div>}

          {!caseData ? (
            <div className="pm-loading">불러오는 중...</div>
          ) : editMode ? (
            <>
              <CaseForm
                initial={caseData}
                allPatterns={allPatterns}
                onSubmit={handleSubmit}
                onCancel={() => { setEditMode(false); setError(null) }}
                submitting={submitting}
              />
              <ReferencesSection caseId={caseId} />
            </>
          ) : (
            <>
              {/* 읽기 모드 */}
              <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
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
      setError(e.message || "저장에 실패했습니다.")
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
                    </div>
                    <div className="pm-item-tags">
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
  weight: 1.0, is_required: false, analysis_guidelines: "", chip_tags: [],
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
      is_required: d.is_required,
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
      <div style={{ display: "flex", gap: 12 }}>
        <label className="pm-field" style={{ flex: 1 }}>
          <span className="pm-field-label">가중치</span>
          <input type="number" step="0.1" value={d.weight} onChange={e => set("weight", e.target.value)} />
        </label>
        <label className="pm-field" style={{ flexShrink: 0, justifyContent: "flex-end", paddingBottom: 2 }}>
          <span className="pm-field-label">필수</span>
          <input type="checkbox" checked={d.is_required} onChange={e => set("is_required", e.target.checked)}
            style={{ width: "auto", marginTop: 6 }} />
        </label>
      </div>
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

  useEffect(() => {
    function onKey(e) { if (e.key === "Escape") onClose() }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose])

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

  return (
    <div className="as-modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
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
          <button className="as-modal-close" onClick={onClose}>✕</button>
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
                {patternData.is_required && <div><span style={{ fontSize: 12, color: "var(--accent)" }}>⭐ 필수 패턴</span></div>}
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
