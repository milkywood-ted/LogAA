import { useState, useEffect } from "react"
import { _request } from "../api/_http"

export default function InfoPanel({ selectedCase, refreshKey }) {
  const [expanded, setExpanded] = useState(true)
  const [userLogs, setUserLogs] = useState([])

  useEffect(() => {
    if (!selectedCase) { setUserLogs([]); return }
    _request("GET", `/api/defect/${selectedCase.id}/user-logs`)
      .then(data => setUserLogs(data.files ?? []))
      .catch(() => setUserLogs([]))
  }, [selectedCase?.id, refreshKey])

  if (!selectedCase) {
    return (
      <div className="info-panel">
        <div className="panel-empty">케이스를 선택하세요</div>
      </div>
    )
  }

  return (
    <div className="info-panel">
      <div className="info-panel-header" onClick={() => setExpanded(v => !v)}>
        <span className="info-panel-toggle">{expanded ? "▼" : "▶"}</span>
        <span className="info-panel-id">{selectedCase.id}</span>
        <span className="info-panel-meta">가져옴 · {new Date(selectedCase.fetchedAt).toLocaleString("ko-KR")}</span>
        {!expanded && <span className="info-panel-summary">{selectedCase.title || "제목 없음"}</span>}
      </div>

      {expanded && (
        <div className="info-panel-body">

          {selectedCase.title && (
            <div className="info-description">
              <div className="info-label" style={{ marginBottom: 6 }}>문제 제목</div>
              <div className="info-description-text">{selectedCase.title}</div>
            </div>
          )}

          {(selectedCase.sw_version || selectedCase.chip?.length > 0) && (
            <div className="info-chip-section">
              {selectedCase.sw_version && (
                <div className="info-sw-version">
                  <span className="info-label">SW Version</span>
                  <span className="info-sw-version-text">{selectedCase.sw_version}</span>
                </div>
              )}
              {selectedCase.chip?.length > 0 && (
                <div className="info-chips">
                  <span className="info-label">칩</span>
                  <span className="info-chip-badges">
                    {selectedCase.chip.map((c, i) => (
                      <span key={i} className="info-chip-badge">{c}</span>
                    ))}
                  </span>
                </div>
              )}
            </div>
          )}

          {selectedCase.description && (
            <div className="info-description">
              <div className="info-label" style={{ marginBottom: 6 }}>문제 설명</div>
              <div className="info-description-text">{selectedCase.description}</div>
            </div>
          )}

          {selectedCase.files?.length > 0 && (
            <div className="info-files">
              <div className="info-label" style={{ marginBottom: 6 }}>첨부파일</div>
              {selectedCase.files.map((f, i) => (
                <div key={i} className="info-file-item">
                  <span className="info-file-status">✅</span>
                  <span className="info-file-name">{typeof f === "string" ? f : f.filename}</span>
                </div>
              ))}
            </div>
          )}

          {selectedCase.comment_attachment_items?.some(item => item.files?.length > 0) && (
            <div className="info-comments">
              <div className="info-label" style={{ marginBottom: 6 }}>댓글</div>
              {selectedCase.comment_attachment_items.filter(item => item.files?.length > 0).map((item, i) => {
                const lines = (item.text || "").split("\n").filter(l => l.trim())
                const dateLine = lines[0] || ""
                const authorLine = lines[1] || ""
                const bodyLines = lines.slice(2)
                const attachPaths = (item.files || []).map(f => f.filename)

                return (
                  <div key={i} className="info-comment-item">
                    <div className="info-comment-meta">{dateLine}{authorLine ? ` · ${authorLine}` : ""}</div>
                    {bodyLines.length > 0 && (
                      <div className="info-comment-body">{bodyLines.join("\n")}</div>
                    )}
                    {attachPaths.length > 0 && (
                      <div className="info-comment-attachments">
                        {attachPaths.map((name, j) => (
                          <div key={j} className="info-file-item">
                            <span className="info-file-status">📎</span>
                            <span className="info-file-name">{name}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}

          {userLogs.length > 0 && (
            <div className="info-user-logs">
              <div className="info-label" style={{ marginBottom: 6 }}>사용자 추가 로그</div>
              {userLogs.map((f, i) => (
                <div key={i} className="info-file-item">
                  <span className="info-file-status">📄</span>
                  <span className="info-file-name">{f.filename}</span>
                </div>
              ))}
            </div>
          )}

        </div>
      )}
    </div>
  )
}
