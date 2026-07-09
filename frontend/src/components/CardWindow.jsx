import { useState, useEffect } from "react"
import { createPortal } from "react-dom"

// Updated to match the indigo light-admin theme:
// - macOS traffic-light chrome removed from markup/CSS (see App.css .traffic-light*)
// - collapse is now toggled by clicking the titlebar itself (no separate button)
// - "크게 보기" (expand) remains a dedicated icon button, function unchanged
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

  // 타이틀 클릭 = 접기/펼치기 토글. 확대된 상태에서 클릭하면 확대만 닫는다.
  function handleTitleClick() {
    if (expanded) {
      handleCollapse()
      return
    }
    setMinimized(v => !v)
  }

  return (
    <>
      <div className={`main-card${className ? ` ${className}` : ""}${minimized ? " card-minimized" : ""}`}>
        <div className="card-titlebar" onClick={handleTitleClick}>
          <span className="card-titlebar-text">{title}</span>
          {titleRight && <div className="card-titlebar-right" onClick={e => e.stopPropagation()}>{titleRight}</div>}
          <div className="card-traffic-lights" onClick={e => e.stopPropagation()}>
            <button
              className="traffic-light traffic-light-max"
              onClick={() => setExpanded(true)}
              title="크게 보기"
            >
              <span className="traffic-light-icon">⤢</span>
            </button>
          </div>
        </div>

        {!minimized && !expanded && (
          <div className="card-body">{children}</div>
        )}
      </div>

      {expanded && createPortal(
        <div className="card-modal-overlay" onClick={handleCollapse}>
          <div className="card-modal" onClick={e => e.stopPropagation()}>
            <div className="card-titlebar card-titlebar-modal">
              <span className="card-titlebar-text">{title}</span>
              <div className="card-traffic-lights">
                <button
                  className="traffic-light traffic-light-min"
                  onClick={handleCollapse}
                  title="닫기"
                >
                  <span className="traffic-light-icon">⤡</span>
                </button>
              </div>
            </div>
            <div className="card-modal-body">{children}</div>
          </div>
        </div>,
        document.body
      )}
    </>
  )
}
