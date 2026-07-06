import { useEffect } from "react"
import { createPortal } from "react-dom"

export default function NoLogsModal({ onAddLog, onClose }) {
  useEffect(() => {
    function onKey(e) { if (e.key === "Escape") onClose() }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose])

  return createPortal(
    <div className="as-modal-overlay" onClick={onClose}>
      <div className="as-modal no-logs-modal" onClick={e => e.stopPropagation()}>
        <div className="as-modal-header">
          <span className="as-modal-title">로그 파일 없음</span>
          <button className="as-modal-close" onClick={onClose}>✕</button>
        </div>

        <div className="as-modal-body">
          <div className="no-logs-message">
            <span className="no-logs-icon">⚠️</span>
            <div className="no-logs-text">
              <div className="no-logs-title">로그 파일이 없습니다.</div>
              <div className="no-logs-desc">분석할 로그가 없어 분석을 시작할 수 없습니다. 로그를 추가해주세요.</div>
            </div>
          </div>
        </div>

        <div className="as-modal-footer">
          <button className="as-cancel-btn" onClick={onClose}>닫기</button>
          {onAddLog && (
            <button className="as-confirm-btn" onClick={onAddLog}>로그 추가</button>
          )}
        </div>
      </div>
    </div>,
    document.body
  )
}
