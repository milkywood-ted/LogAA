import { useState, useEffect, useCallback, useMemo } from "react"
import { useNavigate } from "react-router-dom"
import {
  getAnalysisProfiles, getAnalysisProfile,
  createAnalysisProfile, updateAnalysisProfile, deleteAnalysisProfile,
  getKnowledgeList, createKnowledge, updateKnowledge, deleteKnowledge,
} from "../api"

// ─── 공통: 콤마 구분 문자열 ↔ 배열 ───
function parseList(text) {
  return text.split(",").map(s => s.trim()).filter(Boolean)
}

// ─── 프로파일 폼 (생성/수정 공용) ───
const EMPTY_PROFILE = {
  name: "", description: "", analysis_guidelines: "",
  prefilter_keywords: [], knowledge_refs: [], category: "",
}

// knowledgeItems: [{id: number, name: string}, ...]
function ProfileForm({ initial, knowledgeItems, onSubmit, onCancel, submitting, onDirtyChange }) {
  const [name, setName] = useState(initial.name)
  const [description, setDescription] = useState(initial.description)
  const [guidelines, setGuidelines] = useState(initial.analysis_guidelines)
  const [keywords, setKeywords] = useState(initial.prefilter_keywords.join(", "))
  const [refIds, setRefIds] = useState(initial.knowledge_refs.map(Number))
  const [refQuery, setRefQuery] = useState("")
  const [category, setCategory] = useState(initial.category ?? "")

  const isDirty = useMemo(() => (
    name !== initial.name ||
    description !== initial.description ||
    guidelines !== initial.analysis_guidelines ||
    keywords !== initial.prefilter_keywords.join(", ") ||
    category !== (initial.category ?? "") ||
    JSON.stringify([...refIds].sort()) !== JSON.stringify([...initial.knowledge_refs].map(Number).sort())
  ), [name, description, guidelines, keywords, category, refIds, initial])

  useEffect(() => { onDirtyChange?.(isDirty) }, [isDirty, onDirtyChange])

  const filteredItems = useMemo(() => {
    const q = refQuery.trim().toLowerCase()
    return q ? knowledgeItems.filter(k => k.name.toLowerCase().includes(q)) : knowledgeItems
  }, [knowledgeItems, refQuery])

  function toggleRef(id) {
    setRefIds(refIds.includes(id) ? refIds.filter(r => r !== id) : [...refIds, id])
  }

  function submit() {
    onSubmit({
      name: name.trim(),
      description,
      analysis_guidelines: guidelines,
      prefilter_keywords: parseList(keywords),
      knowledge_refs: refIds,
      category: category.trim(),
    })
  }

  return (
    <div className="pm-form">
      <label className="pm-field">
        <span className="pm-field-label">이름 *</span>
        <input value={name} onChange={e => setName(e.target.value)} placeholder="DTV DP" />
      </label>
      <label className="pm-field">
        <span className="pm-field-label">설명</span>
        <input value={description} onChange={e => setDescription(e.target.value)} placeholder="DTV DP 문제 분석 프로파일" />
      </label>
      <label className="pm-field">
        <span className="pm-field-label">분석 지침</span>
        <textarea value={guidelines} onChange={e => setGuidelines(e.target.value)} rows={5} />
      </label>
      <label className="pm-field">
        <span className="pm-field-label">카테고리</span>
        <input value={category} onChange={e => setCategory(e.target.value)} placeholder="display, linux, mobile 등" />
      </label>
      <label className="pm-field">
        <span className="pm-field-label">사전정제 키워드 (콤마 구분)</span>
        <input value={keywords} onChange={e => setKeywords(e.target.value)} placeholder="DRM-DP, SOC_FRC" />
      </label>
      <div className="pm-field">
        <span className="pm-field-label">사전지식 참조</span>
        {knowledgeItems.length === 0 ? (
          <span className="pm-hint">등록된 사전지식이 없습니다. 사전지식 탭에서 먼저 추가하세요.</span>
        ) : (
          <>
            <input
              className="pm-ref-search"
              placeholder="사전지식 이름 검색..."
              value={refQuery}
              onChange={e => setRefQuery(e.target.value)}
            />
            <div className="pm-ref-tags">
              {filteredItems.length === 0 ? (
                <span className="pm-hint">검색 결과가 없습니다.</span>
              ) : filteredItems.map(k => (
                <button
                  key={k.id}
                  type="button"
                  className={`pm-ref-tag ${refIds.includes(k.id) ? "selected" : ""}`}
                  onClick={() => toggleRef(k.id)}
                >
                  {k.name}
                </button>
              ))}
            </div>
          </>
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

// ─── 프로파일 관리 탭 ───
function ProfilesTab({ knowledgeItems, onDirtyChange }) {
  const [profiles, setProfiles] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [editing, setEditing] = useState(null)   // null | "__new__" | <name>
  const [editInitial, setEditInitial] = useState(EMPTY_PROFILE)
  const [editingLoad, setEditingLoad] = useState(null)  // 로딩 중인 profile name
  const [submitting, setSubmitting] = useState(false)
  const [formDirty, setFormDirty] = useState(false)

  const reload = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setProfiles(await getAnalysisProfiles())
    } catch {
      setError("프로파일 목록을 불러오지 못했습니다.")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { reload() }, [reload])

  useEffect(() => { onDirtyChange?.(editing !== null && formDirty) }, [editing, formDirty, onDirtyChange])

  function confirmLeave() {
    return !formDirty || window.confirm("변경사항이 있습니다. 저장하지 않고 이동하시겠습니까?")
  }

  function startNew() {
    if (!confirmLeave()) return
    setFormDirty(false)
    setEditInitial(EMPTY_PROFILE)
    setEditing("__new__")
  }

  function cancelEdit() {
    if (!confirmLeave()) return
    setFormDirty(false)
    setEditing(null)
  }

  async function startEdit(name) {
    setEditingLoad(name)
    try {
      const full = await getAnalysisProfile(name)
      setEditInitial({ ...EMPTY_PROFILE, ...full })
      setEditing(name)
    } catch {
      setError(`'${name}' 프로파일을 불러오지 못했습니다.`)
    } finally {
      setEditingLoad(null)
    }
  }

  async function handleSubmit(data) {
    setSubmitting(true)
    setError(null)
    try {
      if (editing === "__new__") {
        await createAnalysisProfile(data)
      } else {
        await updateAnalysisProfile(editing, data)
      }
      setFormDirty(false)
      setEditing(null)
      await reload()
    } catch (e) {
      setError(e.message || "저장에 실패했습니다.")
    } finally {
      setSubmitting(false)
    }
  }

  async function handleDelete(name) {
    if (!window.confirm(`프로파일 '${name}' 을 삭제할까요?`)) return
    setError(null)
    try {
      await deleteAnalysisProfile(name)
      await reload()
    } catch (e) {
      setError(e.message || "삭제에 실패했습니다.")
    }
  }

  if (loading) return <div className="pm-loading">불러오는 중...</div>

  return (
    <div className="pm-tab">
      {error && <div className="settings-error">{error}</div>}

      {editing ? (
        <div className="pm-edit-wrap">
          <div className="pm-edit-title">{editing === "__new__" ? "새 프로파일" : `프로파일 수정: ${editing}`}</div>
          <ProfileForm
            initial={editInitial}
            knowledgeItems={knowledgeItems}
            onSubmit={handleSubmit}
            onCancel={cancelEdit}
            submitting={submitting}
            onDirtyChange={setFormDirty}
          />
        </div>
      ) : (
        <>
          <div className="pm-list-header">
            <span className="pm-count">프로파일 {profiles.length}개</span>
            <button className="settings-save-btn" onClick={startNew}>+ 새 프로파일</button>
          </div>
          {profiles.length === 0 ? (
            <div className="pm-empty">등록된 프로파일이 없습니다.</div>
          ) : (
            <div className="pm-list">
              {profiles.map(p => (
                <div key={p.name} className="pm-item">
                  <div className="pm-item-main">
                    <div className="pm-item-name">{p.name}</div>
                    {p.description && <div className="pm-item-desc">{p.description}</div>}
                    {p.prefilter_keywords?.length > 0 && (
                      <div className="pm-item-tags">
                        {p.prefilter_keywords.map(k => <span key={k} className="keyword-tag">{k}</span>)}
                      </div>
                    )}
                    {p.category && <span className="pm-meta-tag pm-meta-category">{p.category}</span>}
                    {p.knowledge_refs?.length > 0 && (
                      <span className="pm-knowledge-badge">도메인 지식 {p.knowledge_refs.length}개 연결</span>
                    )}
                  </div>
                  <div className="pm-item-actions">
                    <button className="settings-btn-sm" onClick={() => startEdit(p.name)} disabled={editingLoad === p.name}>
                      {editingLoad === p.name ? "불러오는 중..." : "수정"}
                    </button>
                    <button className="settings-btn-sm pm-danger" onClick={() => handleDelete(p.name)}>삭제</button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}

// ─── 도메인 지식 폼 ───
const EMPTY_KNOWLEDGE = {
  name: "", store_type: "sqlite", content: "", description: "",
  category: "", sub_category: "", chip_tags: [],
}

function KnowledgeForm({ initial, onSubmit, onCancel, submitting, onDirtyChange }) {
  const [name, setName] = useState(initial.name)
  const [storeType, setStoreType] = useState(initial.store_type)
  const [content, setContent] = useState(initial.content)
  const [description, setDescription] = useState(initial.description)
  const [category, setCategory] = useState(initial.category ?? "")
  const [subCategory, setSubCategory] = useState(initial.sub_category ?? "")
  const [chipTagsStr, setChipTagsStr] = useState((initial.chip_tags ?? []).join(", "))

  const isDirty = useMemo(() => (
    name !== initial.name ||
    storeType !== initial.store_type ||
    content !== initial.content ||
    description !== initial.description ||
    category !== (initial.category ?? "") ||
    subCategory !== (initial.sub_category ?? "") ||
    chipTagsStr !== (initial.chip_tags ?? []).join(", ")
  ), [name, storeType, content, description, category, subCategory, chipTagsStr, initial])

  useEffect(() => { onDirtyChange?.(isDirty) }, [isDirty, onDirtyChange])

  function submit() {
    onSubmit({
      name: name.trim(),
      store_type: storeType,
      content,
      description,
      category: category.trim(),
      sub_category: subCategory.trim(),
      chip_tags: parseList(chipTagsStr),
    })
  }

  return (
    <div className="pm-form">
      <label className="pm-field">
        <span className="pm-field-label">이름 *</span>
        <input value={name} onChange={e => setName(e.target.value)} placeholder="DP device driver" />
      </label>
      <label className="pm-field">
        <span className="pm-field-label">저장소 타입</span>
        <select value={storeType} onChange={e => setStoreType(e.target.value)}>
          <option value="sqlite">sqlite (구조화 정보, 직접 주입)</option>
          <option value="chromadb">chromadb (비구조화 문서, 임베딩 검색)</option>
        </select>
      </label>
      <label className="pm-field">
        <span className="pm-field-label">설명</span>
        <input value={description} onChange={e => setDescription(e.target.value)} />
      </label>
      <div className="pm-form-row">
        <label className="pm-field pm-field-half">
          <span className="pm-field-label">카테고리</span>
          <input value={category} onChange={e => setCategory(e.target.value)} placeholder="display, linux, mobile 등" />
        </label>
        <label className="pm-field pm-field-half">
          <span className="pm-field-label">서브 카테고리</span>
          <input value={subCategory} onChange={e => setSubCategory(e.target.value)} placeholder="FRC, BW 등" />
        </label>
      </div>
      <label className="pm-field">
        <span className="pm-field-label">칩 태그 (콤마 구분, 비워두면 공통 적용)</span>
        <input value={chipTagsStr} onChange={e => setChipTagsStr(e.target.value)} placeholder="RheaM, RoseM" />
      </label>
      <label className="pm-field">
        <span className="pm-field-label">
          내용 {storeType === "sqlite" ? "(LLM 컨텍스트에 직접 주입)" : "(ChromaDB에 임베딩 — 저장 시 시간이 걸릴 수 있음)"}
        </span>
        <textarea value={content} onChange={e => setContent(e.target.value)} rows={6} />
      </label>
      <div className="pm-form-actions">
        <button className="settings-btn-sm" onClick={onCancel} disabled={submitting}>취소</button>
        <button className="settings-save-btn" onClick={submit} disabled={submitting || !name.trim()}>
          {submitting ? (storeType === "chromadb" ? "임베딩 중..." : "저장 중...") : "저장"}
        </button>
      </div>
    </div>
  )
}

// ─── 도메인 지식 상세 모달 ───
function KnowledgeDetailModal({ item, onClose, onEdit, onDelete }) {
  useEffect(() => {
    function onKey(e) { if (e.key === "Escape") onClose() }
    document.addEventListener("keydown", onKey)
    return () => document.removeEventListener("keydown", onKey)
  }, [onClose])

  return (
    <div className="pm-detail-overlay" onClick={onClose}>
      <div className="pm-detail-modal" onClick={e => e.stopPropagation()}>
        <div className="pm-detail-header">
          <span className="pm-detail-title">
            {item.name}
            <span className={`pm-store-badge ${item.store_type}`}>{item.store_type}</span>
          </span>
          <button className="as-modal-close" onClick={onClose}>✕</button>
        </div>
        <div className="pm-detail-body">
          {item.description && (
            <div className="pm-detail-section">
              <div className="pm-detail-label">설명</div>
              <div className="pm-detail-text">{item.description}</div>
            </div>
          )}
          {(item.category || item.sub_category || item.chip_tags?.length > 0) && (
            <div className="pm-detail-section">
              <div className="pm-detail-label">분류 / 칩</div>
              <div className="pm-detail-meta">
                {item.category && <span className="pm-meta-tag pm-meta-category">{item.category}</span>}
                {item.sub_category && <span className="pm-meta-tag pm-meta-sub">{item.sub_category}</span>}
                {item.chip_tags?.map(t => <span key={t} className="pm-meta-tag pm-meta-chip">{t}</span>)}
              </div>
            </div>
          )}
          <div className="pm-detail-section">
            <div className="pm-detail-label">내용</div>
            <pre className="pm-detail-content">{item.content || <span className="pm-hint">(내용 없음)</span>}</pre>
          </div>
        </div>
        <div className="pm-detail-footer">
          <button className="settings-btn-sm pm-danger" onClick={() => onDelete(item)}>삭제</button>
          <button className="settings-save-btn" onClick={() => onEdit(item)}>수정</button>
        </div>
      </div>
    </div>
  )
}

// ─── 도메인 지식 관리 탭 ───
function KnowledgeTab({ onChanged, onDirtyChange }) {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [editing, setEditing] = useState(null)   // null | "__new__" | <id>
  const [editInitial, setEditInitial] = useState(EMPTY_KNOWLEDGE)
  const [submitting, setSubmitting] = useState(false)
  const [formDirty, setFormDirty] = useState(false)
  const [detailItem, setDetailItem] = useState(null)
  const [searchQuery, setSearchQuery] = useState("")
  const [storeFilter, setStoreFilter] = useState("all")  // "all" | "sqlite" | "chromadb"

  const filteredItems = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    return items.filter(k => {
      if (storeFilter !== "all" && k.store_type !== storeFilter) return false
      if (!q) return true
      return k.name.toLowerCase().includes(q) || k.description?.toLowerCase().includes(q)
    })
  }, [items, searchQuery, storeFilter])

  const reload = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getKnowledgeList()
      setItems(data)
      onChanged?.(data.map(k => ({ id: k.id, name: k.name })))
    } catch {
      setError("사전지식 목록을 불러오지 못했습니다.")
    } finally {
      setLoading(false)
    }
  }, [onChanged])

  useEffect(() => { reload() }, [reload])

  useEffect(() => { onDirtyChange?.(editing !== null && formDirty) }, [editing, formDirty, onDirtyChange])

  function confirmLeave() {
    return !formDirty || window.confirm("변경사항이 있습니다. 저장하지 않고 이동하시겠습니까?")
  }

  function startNew() {
    if (!confirmLeave()) return
    setFormDirty(false)
    setEditInitial(EMPTY_KNOWLEDGE)
    setEditing("__new__")
  }

  function startEdit(k) {
    if (!confirmLeave()) return
    setDetailItem(null)
    setFormDirty(false)
    setEditInitial({ ...EMPTY_KNOWLEDGE, ...k })
    setEditing(k.id)
  }

  function cancelEdit() {
    if (!confirmLeave()) return
    setFormDirty(false)
    setEditing(null)
  }

  async function handleSubmit(data) {
    setSubmitting(true)
    setError(null)
    try {
      if (editing === "__new__") {
        await createKnowledge(data)
      } else {
        await updateKnowledge(editing, data)
      }
      setFormDirty(false)
      setEditing(null)
      await reload()
    } catch (e) {
      setError(e.message || "저장에 실패했습니다.")
    } finally {
      setSubmitting(false)
    }
  }

  async function handleDelete(k) {
    if (!window.confirm(`사전지식 '${k.name}' 을 삭제할까요?`)) return
    setDetailItem(null)
    setError(null)
    try {
      await deleteKnowledge(k.id)
      await reload()
    } catch (e) {
      setError(e.message || "삭제에 실패했습니다.")
    }
  }

  if (loading) return <div className="pm-loading">불러오는 중...</div>

  return (
    <div className="pm-tab">
      {error && <div className="settings-error">{error}</div>}

      {editing ? (
        <div className="pm-edit-wrap">
          <div className="pm-edit-title">{editing === "__new__" ? "새 사전지식" : "사전지식 수정"}</div>
          <KnowledgeForm
            initial={editInitial}
            onSubmit={handleSubmit}
            onCancel={cancelEdit}
            submitting={submitting}
            onDirtyChange={setFormDirty}
          />
        </div>
      ) : (
        <>
          <div className="pm-list-header">
            <span className="pm-count">
              {filteredItems.length !== items.length
                ? `${filteredItems.length} / 전체 ${items.length}개`
                : `사전지식 ${items.length}개`}
            </span>
            <button className="settings-save-btn" onClick={startNew}>+ 새 사전지식</button>
          </div>
          <div className="pm-filter-bar">
            <input
              className="pm-search-input"
              placeholder="이름 또는 설명 검색..."
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
            />
            <div className="pm-filter-btns">
              {["all", "sqlite", "chromadb"].map(v => (
                <button
                  key={v}
                  className={`pm-filter-btn ${storeFilter === v ? "active" : ""}`}
                  onClick={() => setStoreFilter(v)}
                >
                  {v === "all" ? "전체" : v}
                </button>
              ))}
            </div>
          </div>
          {items.length === 0 ? (
            <div className="pm-empty">등록된 사전지식이 없습니다.</div>
          ) : filteredItems.length === 0 ? (
            <div className="pm-empty">검색 결과가 없습니다.</div>
          ) : (
            <div className="pm-list">
              {filteredItems.map(k => (
                <div key={k.id} className="pm-item">
                  <div className="pm-item-main">
                    <div className="pm-item-name">
                      <button className="pm-item-title-btn" onClick={() => setDetailItem(k)}>
                        {k.name}
                      </button>
                      <span className={`pm-store-badge ${k.store_type}`}>{k.store_type}</span>
                      {k.category && <span className="pm-meta-tag pm-meta-category">{k.category}</span>}
                      {k.sub_category && <span className="pm-meta-tag pm-meta-sub">{k.sub_category}</span>}
                      {k.chip_tags?.map(t => <span key={t} className="pm-meta-tag pm-meta-chip">{t}</span>)}
                    </div>
                    {k.description && <div className="pm-item-desc">{k.description}</div>}
                  </div>
                  <div className="pm-item-actions">
                    <button className="settings-btn-sm" onClick={() => startEdit(k)}>수정</button>
                    <button className="settings-btn-sm pm-danger" onClick={() => handleDelete(k)}>삭제</button>
                  </div>
                </div>
              ))}
            </div>
          )}
          {detailItem && (
            <KnowledgeDetailModal
              item={detailItem}
              onClose={() => setDetailItem(null)}
              onEdit={startEdit}
              onDelete={handleDelete}
            />
          )}
        </>
      )}
    </div>
  )
}

// ─── 페이지 ───
export default function ProfileManagePage() {
  const navigate = useNavigate()
  const [tab, setTab] = useState("profiles")
  const [knowledgeItems, setKnowledgeItems] = useState([])  // [{id, name}, ...]
  const [tabDirty, setTabDirty] = useState(false)

  // 프로파일 탭에서 knowledge_refs 다중선택에 쓰기 위해 지식 목록을 항상 확보
  const refreshKnowledgeItems = useCallback((items) => {
    if (items) setKnowledgeItems(items)
  }, [])

  useEffect(() => {
    getKnowledgeList().then(data => setKnowledgeItems(data.map(k => ({ id: k.id, name: k.name })))).catch(() => {})
  }, [])

  function switchTab(next) {
    if (next === tab) return
    if (tabDirty && !window.confirm("변경사항이 있습니다. 저장하지 않고 이동하시겠습니까?")) return
    setTabDirty(false)
    setTab(next)
  }

  function goBack() {
    if (tabDirty && !window.confirm("변경사항이 있습니다. 저장하지 않고 나가시겠습니까?")) return
    navigate(-1)
  }

  return (
    <div className="settings-page">
      <div className="settings-header">
        <button className="settings-back-btn" onClick={goBack}>← 돌아가기</button>
        <span className="settings-header-title">Profile and Domain Knowledge</span>
      </div>

      <div className="pm-tabs">
        <button className={`pm-tab-btn ${tab === "profiles" ? "active" : ""}`} onClick={() => switchTab("profiles")}>
          분석 프로파일
        </button>
        <button className={`pm-tab-btn ${tab === "knowledge" ? "active" : ""}`} onClick={() => switchTab("knowledge")}>
          도메인 지식
        </button>
      </div>

      <div className="settings-body">
        {tab === "profiles"
          ? <ProfilesTab knowledgeItems={knowledgeItems} onDirtyChange={setTabDirty} />
          : <KnowledgeTab onChanged={refreshKnowledgeItems} onDirtyChange={setTabDirty} />}
      </div>
    </div>
  )
}
