const VERDICT_ICON = { "문제": "🔴", "불확실": "🟡", "알 수 없음": "⚪" }
const VERDICT_CLASS = { "문제": "verdict-problem", "불확실": "verdict-uncertain", "알 수 없음": "verdict-unknown" }

export default function ResultPanel({ analysisState }) {
  const { status, report, error } = analysisState

  if (status === "idle") {
    return <div className="result-panel"><div className="panel-empty">분석 시작 버튼을 눌러주세요</div></div>
  }

  if (status === "running") {
    return <div className="result-panel"><div className="panel-empty">분석 진행 중입니다...</div></div>
  }

  if (status === "error") {
    return <div className="result-panel"><div className="result-error">분석 실패: {error}</div></div>
  }

  if (status === "done" && report) {
    const verdict = report.verdict ?? "—"
    const score = report.match_result?.score
    const matchedCase = report.matched_case?.name
    const patterns = report.match_result?.matched ?? []

    return (
      <div className="result-panel">
        <div className={`result-verdict ${VERDICT_CLASS[verdict] ?? ""}`}>
          <span className="result-verdict-icon">{VERDICT_ICON[verdict] ?? "❓"}</span>
          <span className="result-verdict-text">{verdict}</span>
          {score != null && (
            <span className="result-score">{(score * 100).toFixed(0)}점</span>
          )}
        </div>

        <div className="result-meta">
          {matchedCase && (
            <div className="result-meta-row">
              <span className="result-meta-label">매칭 케이스</span>
              <span className="result-meta-value">{matchedCase}</span>
            </div>
          )}
          {patterns.length > 0 && (
            <div className="result-meta-row">
              <span className="result-meta-label">매칭 패턴</span>
              <span className="result-meta-value">
                {patterns.map(p => p.name).join(", ")}
              </span>
            </div>
          )}
        </div>

        {report.report_md && (
          <div className="result-report">
            <div className="result-report-title">분석 리포트</div>
            <pre className="result-report-body">{report.report_md}</pre>
          </div>
        )}
      </div>
    )
  }

  return <div className="result-panel"><div className="panel-empty">결과가 없습니다</div></div>
}
