import { useState } from "react"

const STAGES = [
  "로그 정제",
  "키워드 필터링",
  "벡터 검색",
  "재랭킹",
  "프롬프트 조립",
  "리포트 생성",
]

function getStageStatus(index, currentStage, status) {
  if (status === "idle") return "wait"
  if (status === "done") return "done"
  if (status === "error") return "wait"
  const name = currentStage?.split(" — ")[0]?.trim() ?? ""
  const current = STAGES.findIndex(s => name.includes(s))
  if (current === -1) return index === 0 ? "running" : "wait"
  if (index < current) return "done"
  if (index === current) return "running"
  return "wait"
}

function stageIcon(s) {
  if (s === "done") return "✅"
  if (s === "running") return "⏳"
  return null
}

export default function ProgressPanel({ analysisState }) {
  const { status, stage, progress } = analysisState
  const [expanded, setExpanded] = useState(false)

  const isActive = status === "running" || status === "done" || status === "error"

  if (!isActive) return null

  return (
    <div className="progress-panel">
      <div className="progress-summary">
        <div className="progress-bar-wrap">
          <div className="progress-bar-track">
            <div
              className="progress-bar-fill"
              style={{ width: `${status === "done" ? 100 : progress}%` }}
            />
          </div>
          <span className="progress-pct">
            {status === "done" ? "완료" : status === "error" ? "오류" : `${progress}%`}
          </span>
        </div>

        {status === "running" && stage && (
          <div className="progress-stage-text">
            {stage.split(" — ")[1] ?? stage}
          </div>
        )}

        <button className="progress-detail-toggle" onClick={() => setExpanded(v => !v)}>
          상세 프로그레스 {expanded ? "▲" : "▼"}
        </button>
      </div>

      {expanded && (
        <div className="progress-stages">
          {STAGES.map((label, i) => {
            const s = getStageStatus(i, stage, status)
            return (
              <div key={i} className={`stage-row ${s}`}>
                <div className={`stage-index ${s}`}>{stageIcon(s) ?? i + 1}</div>
                <span className={`stage-label ${s}`}>Stage {i + 1} — {label}</span>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
