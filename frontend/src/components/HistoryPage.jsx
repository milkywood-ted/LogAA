import { useState, useEffect, useCallback, useMemo } from "react"
import { useNavigate } from "react-router-dom"
import ReactMarkdown from "react-markdown"
import { getHistoryList, getHistoryItem, deleteHistoryItem, clearHistory } from "../api"

const VERDICT_ICON = {
  "문제": "🔴", "문제 아님": "🟢", "판정 불가": "🔵", "불확실": "🟡", "알 수 없음": "⚪",
}

function VerdictBadge({ verdict }) {
  const icon = VERDICT_ICON[verdict] || "❓"
  return <span className="hist-verdict-badge">{icon} {verdict || "—"}</span>
}

function ScoreBadge({ score }) {
  if (score == null) return null
  const pct = Math.round(score * 100)
  return <span className="hist-score-badge">{pct}%</span>
}

function formatDate(dt) {
  if (!dt) return ""
  return dt.replace("T", " ").slice(0, 19)
}

// ─── 단건 상세 모달 ───────────────────────────────────────────────────────────

function HistoryDetailModal({ id, onClose }) {
  const [item, setItem] = useState(null)
  const [loading, setLoading] = useState(true)
  const [reportOpen, setReportOpen] = useState(false)

  useEffect(() => {
    setLoading(true)
    getHistoryItem(id)
      .then(setItem)
      .catch(() => setItem(null))
      .finally(() => setLoading(false))
  }, [id])

  function handleBackdrop(e) {
    if (e.target === e.currentTarget) onClose()
  }

  return (
    <div className="hist-modal-backdrop" onClick={handleBackdrop}>
      <div className="hist-modal">
        <div className="hist-modal-header">
          <span className="hist-modal-title">분석 이력 상세 #{id}</span>
          <button className="hist-modal-close" onClick={onClose}>✕</button>
        </div>
        <div className="hist-modal-body">
          {loading && <div className="pm-loading">불러오는 중...</div>}
          {!loading && !item && <div className="pm-empty">데이터를 불러올 수 없습니다.</div>}
          {!loading && item && <HistoryDetail item={item} reportOpen={reportOpen} onToggleReport={() => setReportOpen(v => !v)} />}
        </div>
      </div>
    </div>
  )
}

function HistoryDetail({ item, reportOpen, onToggleReport }) {
  const r = item.result || {}
  const logs = item.analysis_logs || []

  return (
    <div className="hist-detail">
      <div className="hist-detail-meta">
        <div className="hist-detail-row">
          <span className="hist-detail-label">일시</span>
          <span>{formatDate(item.created_at)}</span>
        </div>
        <div className="hist-detail-row">
          <span className="hist-detail-label">Defect</span>
          <span>{item.defect_id || "—"}</span>
        </div>
        <div className="hist-detail-row">
          <span className="hist-detail-label">판정</span>
          <VerdictBadge verdict={r.verdict} />
        </div>
        <div className="hist-detail-row">
          <span className="hist-detail-label">점수</span>
          <ScoreBadge score={r.score} />
        </div>
        <div className="hist-detail-row">
          <span className="hist-detail-label">매칭 케이스</span>
          <span>{r.matched_case || "—"}</span>
        </div>
        <div className="hist-detail-row">
          <span className="hist-detail-label">입력 해시</span>
          <span className="hist-detail-hash">{item.input_hash?.slice(0, 16)}…</span>
        </div>
      </div>

      {r.problem_text && (
        <div className="hist-detail-section">
          <div className="hist-detail-section-title">문제 설명</div>
          <div className="hist-detail-text">{r.problem_text}</div>
        </div>
      )}

      {(r.matched_patterns?.length > 0) && (
        <div className="hist-detail-section">
          <div className="hist-detail-section-title">매칭 패턴 ({r.matched_patterns.length})</div>
          <div className="hist-detail-pattern-list">
            {r.matched_patterns.map((p, i) => (
              <span key={i} className="hist-pattern-tag">{p.name} <span className="hist-pattern-type">{p.type}</span></span>
            ))}
          </div>
        </div>
      )}

      {r.report_md && (
        <div className="hist-detail-section">
          <button className="hist-toggle-btn" onClick={onToggleReport}>
            {reportOpen ? "▼ 리포트 접기" : "▶ 리포트 전문 보기"}
          </button>
          {reportOpen && (
            <div className="hist-report-md result-report-body markdown-body">
              <ReactMarkdown>{r.report_md}</ReactMarkdown>
            </div>
          )}
        </div>
      )}

      {logs.length > 0 && (
        <div className="hist-detail-section">
          <div className="hist-detail-section-title">Stage 로그 ({logs.length})</div>
          <div className="hist-logs">
            {logs.map(lg => (
              <div key={lg.id} className="hist-log-item">
                <span className="hist-log-stage">{lg.stage}</span>
                <span className="hist-log-time">{formatDate(lg.created_at)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ─── 이력 목록 행 ─────────────────────────────────────────────────────────────

function HistoryRow({ item, onDetail, onDelete }) {
  const [deleting, setDeleting] = useState(false)

  async function handleDelete(e) {
    e.stopPropagation()
    if (!confirm(`이력 #${item.id}을 삭제하시겠습니까?`)) return
    setDeleting(true)
    try {
      await onDelete(item.id)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="hist-row" onClick={() => onDetail(item.id)}>
      <div className="hist-row-main">
        <div className="hist-row-top">
          <VerdictBadge verdict={item.verdict} />
          <ScoreBadge score={item.score} />
          <span className="hist-row-case">{item.matched_case || "—"}</span>
          {item.defect_id && (
            <span className="hist-defect-badge">{item.defect_id}</span>
          )}
          <span className="hist-row-date">{formatDate(item.created_at)}</span>
        </div>
        {item.problem_text && (
          <div className="hist-row-problem">{item.problem_text}</div>
        )}
        {item.matched_patterns?.length > 0 && (
          <div className="hist-row-patterns">
            {item.matched_patterns.slice(0, 4).map((p, i) => (
              <span key={i} className="hist-pattern-tag sm">{p.name}</span>
            ))}
            {item.matched_patterns.length > 4 && (
              <span className="hist-more">+{item.matched_patterns.length - 4}</span>
            )}
          </div>
        )}
      </div>
      <div className="hist-row-actions">
        <button
          className="hist-delete-btn pm-danger"
          disabled={deleting}
          onClick={handleDelete}
          title="이력 삭제"
        >
          {deleting ? "삭제 중..." : "삭제"}
        </button>
      </div>
    </div>
  )
}

// ─── 메인 페이지 ──────────────────────────────────────────────────────────────

export default function HistoryPage() {
  const navigate = useNavigate()
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [detailId, setDetailId] = useState(null)
  const [limit, setLimit] = useState(50)
  const [clearing, setClearing] = useState(false)
  const [filterDefectId, setFilterDefectId] = useState("")  // "" = 전체

  const load = useCallback(() => {
    setLoading(true)
    getHistoryList({ limit })
      .then(data => { setItems(data); setFilterDefectId("") })
      .catch(() => setItems([]))
      .finally(() => setLoading(false))
  }, [limit])

  useEffect(() => { load() }, [load])

  // 목록에서 유니크 defect_id 추출 (null/undefined 제외)
  const defectOptions = useMemo(() => {
    const ids = [...new Set(items.map(h => h.defect_id).filter(Boolean))]
    return ids.sort()
  }, [items])

  // 클라이언트 사이드 필터링
  const displayed = useMemo(() => {
    if (!filterDefectId) return items
    return items.filter(h => h.defect_id === filterDefectId)
  }, [items, filterDefectId])

  async function handleDelete(id) {
    await deleteHistoryItem(id)
    setItems(prev => prev.filter(h => h.id !== id))
  }

  async function handleClearAll() {
    const target = filterDefectId
      ? `Defect ${filterDefectId} 이력 ${displayed.length}건`
      : `전체 이력 ${items.length}건`
    if (!confirm(`${target}을 모두 삭제하시겠습니까?`)) return
    setClearing(true)
    try {
      if (filterDefectId) {
        // 필터된 건만 순차 삭제
        await Promise.all(displayed.map(h => deleteHistoryItem(h.id)))
        setItems(prev => prev.filter(h => h.defect_id !== filterDefectId))
        setFilterDefectId("")
      } else {
        await clearHistory()
        setItems([])
      }
    } finally {
      setClearing(false)
    }
  }

  return (
    <div className="settings-page">
      <div className="settings-header">
        <button className="settings-back-btn" onClick={() => navigate(-1)}>← 돌아가기</button>
        <span className="settings-header-title">분석 이력</span>
      </div>

      <div className="settings-body">
        <div className="hist-toolbar">
          <div className="hist-toolbar-left">
            <span className="pm-count">
              {loading
                ? "불러오는 중..."
                : filterDefectId
                  ? `${displayed.length} / 전체 ${items.length}건`
                  : `${items.length}건`
              }
            </span>

            {/* Defect 필터 드롭다운 */}
            {defectOptions.length > 0 && (
              <select
                className="hist-limit-select"
                value={filterDefectId}
                onChange={e => setFilterDefectId(e.target.value)}
              >
                <option value="">전체 Defect</option>
                {defectOptions.map(id => (
                  <option key={id} value={id}>{id}</option>
                ))}
              </select>
            )}

            <select
              className="hist-limit-select"
              value={limit}
              onChange={e => setLimit(Number(e.target.value))}
            >
              <option value={20}>최근 20건</option>
              <option value={50}>최근 50건</option>
              <option value={100}>최근 100건</option>
              <option value={500}>최근 500건</option>
            </select>
          </div>

          {displayed.length > 0 && (
            <button
              className="hist-clear-btn pm-danger"
              disabled={clearing}
              onClick={handleClearAll}
            >
              {clearing ? "삭제 중..." : filterDefectId ? "이 Defect 이력 삭제" : "전체 삭제"}
            </button>
          )}
        </div>

        {loading && <div className="pm-loading">분석 이력을 불러오는 중...</div>}
        {!loading && displayed.length === 0 && (
          <div className="pm-empty">
            {filterDefectId ? `Defect ${filterDefectId}의 이력이 없습니다.` : "분석 이력이 없습니다."}
          </div>
        )}
        {!loading && displayed.length > 0 && (
          <div className="hist-list">
            {displayed.map(item => (
              <HistoryRow
                key={item.id}
                item={item}
                onDetail={setDetailId}
                onDelete={handleDelete}
              />
            ))}
          </div>
        )}
      </div>

      {detailId != null && (
        <HistoryDetailModal id={detailId} onClose={() => setDetailId(null)} />
      )}
    </div>
  )
}
