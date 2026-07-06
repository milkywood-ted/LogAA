import { useState, useEffect } from "react"
import { getPullers, fetchDefect, getDefects } from "../api"

function formatDescription(description) {
  if (!description) return ""
  return typeof description === "object" ? Object.values(description).join("\n") : description
}

export default function Sidebar({ selectedCase, onSelectCase, onPullerError, collapsed, onToggle }) {
  const [pullers, setPullers] = useState([])
  const [selectedPuller, setSelectedPuller] = useState("")
  const [defectId, setDefectId] = useState("")
  const [credId, setCredId] = useState("")
  const [credPw, setCredPw] = useState("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [cases, setCases] = useState([])

  useEffect(() => {
    getPullers()
      .then(data => {
        setPullers(data.pullers)
        if (data.pullers.length > 0) setSelectedPuller(data.pullers[0].name)
      })
      .catch(() => setPullers([]))

    getDefects()
      .then(data => setCases(data.defects))
      .catch(() => setCases([]))
  }, [])

  async function handleFetch() {
    if (!selectedPuller || !defectId) return
    setLoading(true)
    setError(null)
    onPullerError(null)
    try {
      const credentials = credId || credPw ? { id: credId, pw: credPw } : undefined
      const result = await fetchDefect(selectedPuller, defectId, credentials)
      const newCase = {
        id: result.id,
        puller: result.puller,
        title: result.title,
        description: formatDescription(result.description),
        comment_attachment_items: result.comment_attachment_items || [],
        files: result.files,
        fetchedAt: result.fetchedAt,
        sw_version: result.sw_version,
        chip: result.chip,
      }
      setCases(prev => {
        const exists = prev.find(c => c.id === newCase.id)
        const updated = exists
          ? prev.map(c => c.id === newCase.id ? newCase : c)
          : [newCase, ...prev]
        return updated.slice(0, 10)
      })
      onSelectCase(newCase)
    } catch (e) {
      onPullerError({ message: e.message, defect_id: defectId })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="sidebar">
      <div className="sidebar-brand">
        <span className="sidebar-brand-text">{collapsed ? "LogAA" : "Log Analyzing Assistant"}</span>
        <button className="sidebar-toggle" onClick={onToggle} title={collapsed ? "펼치기" : "접기"}>
          {collapsed ? "»" : "«"}
        </button>
      </div>
      <div className="sidebar-header">문제 목록</div>
      <div className="sidebar-input-area">
        <div className="sidebar-section">
          <input
            className="sidebar-input"
            placeholder="ID"
            value={credId}
            onChange={e => setCredId(e.target.value)}
          />
          <input
            className="sidebar-input"
            type="password"
            placeholder="Password"
            value={credPw}
            onChange={e => setCredPw(e.target.value)}
          />
        </div>
        <div className="sidebar-divider" />
        <div className="sidebar-section">
          <select
            className="sidebar-input"
            value={selectedPuller}
            onChange={e => setSelectedPuller(e.target.value)}
          >
            {pullers.length === 0 && <option value="">Puller 없음</option>}
            {pullers.map(p => (
              <option key={p.name} value={p.name}>{p.name}</option>
            ))}
          </select>
          <input
            className="sidebar-input"
            placeholder="Defect ID"
            value={defectId}
            onChange={e => setDefectId(e.target.value)}
            onKeyDown={e => e.key === "Enter" && handleFetch()}
          />
          <button
            className="sidebar-fetch-btn"
            onClick={handleFetch}
            disabled={loading || !selectedPuller || !defectId}
          >
            {loading ? "가져오는 중..." : "가져오기"}
          </button>
          {error && <div className="sidebar-error">{error}</div>}
        </div>
      </div>
      <div className="sidebar-list">
        {cases.length === 0 && (
          <div className="sidebar-empty">가져온 문제가 없습니다</div>
        )}
        {cases.map(c => (
          <div
            key={c.id}
            className={`sidebar-item ${selectedCase?.id === c.id ? "active" : ""}`}
            onClick={() => onSelectCase({
              ...c,
              description: formatDescription(c.description),
              comment_attachment_items: c.comment_attachment_items || [],
            })}
          >
            <div className="sidebar-item-id">{c.id}</div>
            <div className="sidebar-item-title">{c.title || "제목 없음"}</div>
          </div>
        ))}
      </div>
    </div>
  )
}
