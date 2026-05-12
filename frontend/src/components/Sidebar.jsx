import { useState, useEffect } from "react"
import { getPullers, fetchDefect, getCases } from "../api"

export default function Sidebar({ selectedCase, onSelectCase, onPullerError }) {
  const [pullers, setPullers] = useState([])
  const [selectedPuller, setSelectedPuller] = useState("")
  const [defectId, setDefectId] = useState("")
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

    getCases()
      .then(data => setCases(data.cases))
      .catch(() => setCases([]))
  }, [])

  async function handleFetch() {
    if (!selectedPuller || !defectId) return
    setLoading(true)
    setError(null)
    onPullerError(null)
    try {
      const result = await fetchDefect(selectedPuller, defectId)
      const newCase = {
        id: result.id,
        puller: result.puller,
        title: result.title,
        description: Object.values(result.description || {}).join("\n"),
        files: result.files,
        fetchedAt: result.fetchedAt,
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
      <div className="sidebar-header">문제 목록</div>
      <div className="sidebar-input-area">
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
              description: typeof c.description === "object"
                ? Object.values(c.description).join("\n")
                : c.description
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
