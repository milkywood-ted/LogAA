import { useState } from "react"

export default function SelectedFilesPanel({ selectedFiles, defectId }) {
  const [open, setOpen] = useState(false)

  if (!selectedFiles) return null

  function toRelative(path) {
    if (!defectId) return path
    const idx = path.indexOf(defectId)
    return idx !== -1 ? path.slice(idx) : path
  }

  return (
    <div className="selected-files-panel">
      <div className="selected-files-header" onClick={() => setOpen(v => !v)}>
        <span className="selected-files-toggle">{open ? "▼" : "▶"}</span>
        <span className="selected-files-title">분석 대상 파일</span>
        <span className="selected-files-count">{selectedFiles.length}개 선택됨</span>
      </div>
      {open && (
        <div className="selected-files-body">
          {selectedFiles.map((path, i) => (
            <div key={i} className="selected-files-item">
              <span className="selected-files-name">{path.split("/").pop()}</span>
              <span className="selected-files-path">{toRelative(path)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
