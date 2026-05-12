export default function AnalyzeHeader({ selectedCase, analysisState, onAnalyze }) {
  const canAnalyze = selectedCase && analysisState.status === "idle"
  const isRunning = analysisState.status === "running"

  return (
    <div className="analyze-header">
      <span className="analyze-header-title">
        {selectedCase ? `[${selectedCase.id}] ${selectedCase.title || ""}` : "케이스를 선택하세요"}
      </span>
      <button
        className={`analyze-btn ${isRunning ? "running" : ""}`}
        onClick={onAnalyze}
        disabled={!canAnalyze}
      >
        {isRunning ? "분석 중..." : "분석 시작"}
      </button>
    </div>
  )
}
