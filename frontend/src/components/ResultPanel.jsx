export default function ResultPanel({ analysisState }) {
  const { status, report, error } = analysisState

  if (status === "idle") {
    return (
      <div className="result-panel">
        <div className="panel-empty">분석 시작 버튼을 눌러주세요</div>
      </div>
    )
  }

  if (status === "running") {
    return (
      <div className="result-panel">
        <div className="panel-empty">분석 진행 중입니다...</div>
      </div>
    )
  }

  if (status === "error") {
    return (
      <div className="result-panel">
        <div className="result-error">분석 실패: {error}</div>
      </div>
    )
  }

  if (status === "done" && report) {
    return (
      <div className="result-panel">
        <div className="result-actions">
          <button className="result-save-btn">파일로 저장</button>
        </div>
        <div className="result-content">{report}</div>
      </div>
    )
  }

  return (
    <div className="result-panel">
      <div className="panel-empty">결과가 없습니다</div>
    </div>
  )
}
