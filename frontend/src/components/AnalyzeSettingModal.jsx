import { useState, useEffect } from "react"
import { createPortal } from "react-dom"
import { getDefectFiles, addUserLog, deleteUserLog } from "../api"

const CATEGORY_LABEL = {
  default: "기본 로그",
  comment_attachment: "댓글 첨부",
  user_added: "사용자 추가 로그",
}

export default function AnalyzeSettingsModal({ defectId, selectedFiles, onChangeSelectedFiles, onClose }) {
  const [fileGroups, setFileGroups] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [srcPath, setSrcPath] = useState("")
  const [addStatus, setAddStatus] = useState(null) // null | "loading" | "ok" | "error"
  const [addError, setAddError] = useState(null)

  useEffect(() => {
    if (!defectId) return
    loadFiles()
  }, [defectId])

  useEffect(() => {
    function onKey(e) { if (e.key === "Escape") onClose() }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose])

  async function loadFiles() {
    setLoading(true)
    setError(null)
    try {
      const data = await getDefectFiles(defectId)
      setFileGroups(data)
    } catch (e) {
      setError("파일 목록을 불러오지 못했습니다.")
    } finally {
      setLoading(false)
    }
  }

  function allFiles() {
    if (!fileGroups) return []
    return [
      ...fileGroups.default,
      ...fileGroups.comment_attachment,
      ...fileGroups.user_added,
    ]
  }

  function isSelected(path) {
    if (!selectedFiles) return true  // 전체 선택 상태
    return selectedFiles.includes(path)
  }

  function toggleFile(path) {
    const all = allFiles().map(f => f.path)
    const current = selectedFiles ?? all
    const next = current.includes(path)
      ? current.filter(p => p !== path)
      : [...current, path]
    // 전체가 선택된 경우 null(전체)로 되돌림
    onChangeSelectedFiles(next.length === all.length ? null : next)
  }

  function toggleAll() {
    if (!selectedFiles) {
      // 전체 → 전체 해제
      onChangeSelectedFiles([])
    } else {
      // 일부 또는 없음 → 전체 선택
      onChangeSelectedFiles(null)
    }
  }

  async function handleAddLog() {
    if (!srcPath.trim()) return
    setAddStatus("loading")
    setAddError(null)
    try {
      await addUserLog(defectId, srcPath.trim())
      setSrcPath("")
      setAddStatus("ok")
      await loadFiles()
      setTimeout(() => setAddStatus(null), 2000)
    } catch (e) {
      setAddStatus("error")
      setAddError(e.message || "추가 실패")
    }
  }

  async function handleDeleteLog(filename) {
    try {
      await deleteUserLog(defectId, filename)
      // 삭제된 파일이 선택 목록에 있으면 제거
      if (selectedFiles) {
        const removedPath = fileGroups.user_added.find(f => f.filename === filename)?.path
        if (removedPath) {
          onChangeSelectedFiles(selectedFiles.filter(p => p !== removedPath))
        }
      }
      await loadFiles()
    } catch (e) {
      alert("삭제 실패: " + (e.message || ""))
    }
  }

  const total = allFiles().length
  const selectedCount = selectedFiles ? selectedFiles.length : total
  const isPartial = selectedFiles !== null

  return createPortal(
    <div className="as-modal-overlay" onClick={onClose}>
      <div className="as-modal" onClick={e => e.stopPropagation()}>

        <div className="as-modal-header">
          <span className="as-modal-title">분석 고급 설정</span>
          <button className="as-modal-close" onClick={onClose}>✕</button>
        </div>

        <div className="as-modal-body">
          {/* 사용자 로그 추가 섹션 */}
          <div className="as-section">
            <div className="as-section-title">사용자 로그 추가</div>
            <div className="as-add-row">
              <input
                className="as-path-input"
                type="text"
                placeholder="서버 내 파일 경로 입력 (예: /data/logs/my.log)"
                value={srcPath}
                onChange={e => setSrcPath(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter") handleAddLog() }}
              />
              <button
                className="as-add-btn"
                onClick={handleAddLog}
                disabled={!srcPath.trim() || addStatus === "loading"}
              >
                {addStatus === "loading" ? "복사 중..." : "추가"}
              </button>
            </div>
            {addStatus === "ok" && <div className="as-add-ok">추가되었습니다.</div>}
            {addStatus === "error" && <div className="as-add-error">{addError}</div>}
          </div>

          {/* 파일 선택 섹션 */}
          <div className="as-section">
            <div className="as-section-header">
              <div className="as-section-title">분석 대상 파일 선택</div>
              <div className="as-selection-info">
                <span className={isPartial ? "as-partial" : ""}>{selectedCount} / {total} 선택</span>
                <button className="as-toggle-all-btn" onClick={toggleAll}>
                  {!selectedFiles ? "전체 해제" : "전체 선택"}
                </button>
              </div>
            </div>

            {loading && <div className="as-loading">불러오는 중...</div>}
            {error && <div className="as-error">{error}</div>}

            {!loading && !error && fileGroups && (
              <div className="as-file-list">
                {(["default", "comment_attachment", "user_added"]).map(category => {
                  const files = fileGroups[category]
                  if (files.length === 0) return null
                  return (
                    <div key={category} className="as-file-group">
                      <div className="as-file-group-label">{CATEGORY_LABEL[category]}</div>
                      {files.map(f => (
                        <div key={f.path} className="as-file-row">
                          <label className="as-file-label">
                            <input
                              type="checkbox"
                              checked={isSelected(f.path)}
                              onChange={() => toggleFile(f.path)}
                            />
                            <span className="as-file-name">{f.relative_path}</span>
                            <span className="as-file-size">{formatSize(f.size)}</span>
                          </label>
                          {category === "user_added" && (
                            <button
                              className="as-file-delete"
                              onClick={() => handleDeleteLog(f.filename)}
                              title="삭제"
                            >✕</button>
                          )}
                        </div>
                      ))}
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        </div>

        <div className="as-modal-footer">
          <button className="as-confirm-btn" onClick={onClose}>확인</button>
        </div>
      </div>
    </div>,
    document.body
  )
}

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
