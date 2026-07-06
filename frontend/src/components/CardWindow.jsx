import { useState, useEffect } from "react"
import { createPortal } from "react-dom"

export default function CardWindow({
  title,
  titleRight,
  children,
  className = "",
  expanded: externalExpanded,
  onCollapse,
}) {
  const [minimized, setMinimized] = useState(false)
  const [expanded, setExpanded] = useState(false)

  // 외부에서 expanded가 true로 바뀌면(예: 자동 크게 보기) 모달 오픈
  useEffect(() => {
    if (externalExpanded === true) setExpanded(true)
    if (externalExpanded === false) setExpanded(false)
  }, [externalExpanded])

  useEffect(() => {
    if (!expanded) return
    function onKey(e) {
      if (e.key === "Escape") handleCollapse()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [expanded])

  function handleCollapse() {
    setExpanded(false)
    onCollapse?.()
  }

  function handleTitleClick() {
    setMinimized(false)
    if (expanded) handleCollapse()
  }

  return (
    <>
      <div className={`main-card${className ? ` ${className}` : ""}${minimized ? " card-minimized" : ""}`}>
        <div className="card-titlebar" onClick={handleTitleClick}>
          <div className="card-traffic-lights">
            <button
              className="traffic-light traffic-light-min"
              onClick={e => { e.stopPropagation(); setMinimized(v => !v) }}
              title={minimized ? "복원" : "최소화"}
            >
              <span className="traffic-light-icon">–</span>
            </button>
            <button
              className="traffic-light traffic-light-max"
              onClick={e => { e.stopPropagation(); setExpanded(true) }}
              title="크게 보기"
            >
              <span className="traffic-light-icon">+</span>
            </button>
          </div>
          <span className="card-titlebar-text">{title}</span>
          {titleRight && <div className="card-titlebar-right">{titleRight}</div>}
        </div>

        {!minimized && !expanded && (
          <div className="card-body">{children}</div>
        )}
      </div>

      {expanded && createPortal(
        <div className="card-modal-overlay" onClick={handleCollapse}>
          <div className="card-modal" onClick={e => e.stopPropagation()}>
            <div className="card-titlebar card-titlebar-modal">
              <div className="card-traffic-lights">
                <button
                  className="traffic-light traffic-light-min"
                  onClick={e => { e.stopPropagation(); handleCollapse() }}
                  title="닫기"
                >
                  <span className="traffic-light-icon">–</span>
                </button>
              </div>
              <span className="card-titlebar-text">{title}</span>
            </div>
            <div className="card-modal-body">{children}</div>
          </div>
        </div>,
        document.body
      )}
    </>
  )
}
