export default function AnalyzeHeader({ selectedCase, analysisState, onAnalyze, onSettings }) {
  const canAnalyze = selectedCase && analysisState.status === "idle"
  const isRunning = analysisState.status === "running"

  return (
    <div className="analyze-header">
      <span className="analyze-header-title">
        {selectedCase ? `[${selectedCase.id}] ${selectedCase.title || ""}` : "케이스를 선택하세요"}
      </span>
      <div className="analyze-header-actions">
        <button
          className={`analyze-btn ${isRunning ? "running" : ""}`}
          onClick={onAnalyze}
          disabled={!canAnalyze}
        >
          {isRunning ? "분석 중..." : "분석 시작"}
        </button>
        <button className="settings-btn" onClick={onSettings}>
          ⚙ 설정
        </button>
      </div>
    </div>
  )
}