import { useEffect, useRef, useState } from "react"

// stage 문자열 = "{name} — {detail}", name 자체가 "Stage 2 — KB 검색" 형태라
// " — " 구분자가 두 번 등장한다. 앞 두 조각이 단계 라벨, 나머지가 상세다.
function parseStageLabel(stage) {
  if (!stage) return null
  const parts = stage.split(" — ")
  return parts.length >= 2 ? `${parts[0]} — ${parts[1]}` : parts[0]
}

function stageIcon(s) {
  if (s === "done") return "✅"
  if (s === "running") return "⏳"
  if (s === "error") return "❌"
  return null
}

export default function ProgressPanel({ analysisState }) {
  const { status, stage, progress } = analysisState
  const [expanded, setExpanded] = useState(false)

  // 도착한 notify 단계를 순서대로 누적한다 — 하드코딩 단계 목록 없이 실제
  // 실행된 단계를 그대로 표시(§9-1 해소). 조건부 단계(Fallback·Reflection)도
  // 실행된 경우에만 나타나며, 파이프라인 단계가 바뀌어도 자동 추종한다.
  const [stages, setStages] = useState([])
  const prevStatus = useRef(status)

  useEffect(() => {
    const label = parseStageLabel(stage)
    if (status === "running" && prevStatus.current !== "running") {
      // 새 분석 시작 — 이전 실행의 누적 기록 초기화
      setStages(label ? [label] : [])
    } else if (status === "running" && label) {
      setStages(prev => (prev[prev.length - 1] === label ? prev : [...prev, label]))
    }
    prevStatus.current = status
  }, [status, stage])

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
            {stage.split(" — ").slice(1).join(" — ") || stage}
          </div>
        )}

        <button className="progress-detail-toggle" onClick={() => setExpanded(v => !v)}>
          상세 프로그레스 {expanded ? "▲" : "▼"}
        </button>
      </div>

      {expanded && (
        <div className="progress-stages">
          {stages.length === 0 && (
            <div className="stage-row wait">
              <div className="stage-index wait">1</div>
              <span className="stage-label wait">대기 중...</span>
            </div>
          )}
          {stages.map((label, i) => {
            const isLast = i === stages.length - 1
            const s = status === "done" ? "done"
              : status === "error" ? (isLast ? "error" : "done")
              : isLast ? "running" : "done"
            return (
              <div key={i} className={`stage-row ${s === "error" ? "wait" : s}`}>
                <div className={`stage-index ${s === "error" ? "wait" : s}`}>
                  {stageIcon(s) ?? i + 1}
                </div>
                <span className={`stage-label ${s === "error" ? "wait" : s}`}>{label}</span>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
