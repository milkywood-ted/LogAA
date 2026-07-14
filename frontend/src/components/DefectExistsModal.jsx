import { useEffect } from "react"
import { createPortal } from "react-dom"

export default function DefectExistsModal({ defect, onUseExisting, onRefetch, onClose }) {
  useEffect(() => {
    function onKey(e) { if (e.key === "Escape") onClose() }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose])

  const fetchedAt = defect.fetchedAt ? new Date(defect.fetchedAt).toLocaleString() : null

  return createPortal(
    <div className="as-modal-overlay" onClick={onClose}>
      <div className="as-modal defect-exists-modal" onClick={e => e.stopPropagation()}>
        <div className="as-modal-header">
          <span className="as-modal-title">이미 가져온 문제</span>
          <button className="as-modal-close" onClick={onClose}>✕</button>
        </div>

        <div className="as-modal-body">
          <div className="defect-exists-message">
            <span className="defect-exists-icon">📁</span>
            <div className="defect-exists-text">
              <div className="defect-exists-title">{defect.id}{defect.title ? ` — ${defect.title}` : ""}</div>
              <div className="defect-exists-desc">
                이 문제는 workspace에 이미 있습니다.
                {fetchedAt && <> (마지막 가져오기: {fetchedAt})</>}
                <br />
                기존 데이터를 그대로 사용할까요, 다시 가져올까요?
              </div>
            </div>
          </div>
        </div>

        <div className="as-modal-footer">
          <button className="as-cancel-btn" onClick={onUseExisting}>기존 데이터 사용</button>
          <button className="as-confirm-btn" onClick={onRefetch}>다시 가져오기</button>
        </div>
      </div>
    </div>,
    document.body
  )
}
