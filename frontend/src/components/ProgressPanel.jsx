const STAGES = [
  "로그 정제",
  "키워드 필터링",
  "벡터 검색",
  "재랭킹",
  "프롬프트 조립",
  "리포트 생성",
]

function stageIcon(status) {
  if (status === "done") return "✅"
  if (status === "running") return "⏳"
  if (status === "error") return "❌"
  return null
}

export default function ProgressPanel({ analysisState }) {
  const { stages } = analysisState

  return (
    <div className="progress-panel">
      <div className="panel-title">분석 진행</div>
      {STAGES.map((label, i) => {
        const num = i + 1
        const status = stages[num] || "wait"
        return (
          <div key={num} className={`stage-row ${status}`}>
            <div className={`stage-index ${status}`}>
              {stageIcon(status) || num}
            </div>
            <span className={`stage-label ${status}`}>
              Stage {num} — {label}
            </span>
          </div>
        )
      })}
    </div>
  )
}
