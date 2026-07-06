export default function AnalyzeHeader({ selectedCase, analysisState, hasLogs = true, onAnalyze, onCancel, onSettings, onAnalyzeSettings, onCaseManage, onHistory }) {
  const isRunning = analysisState.status === "running"
  // 문제가 선택되고 idle 상태일 때만 분석 가능
  const idle = selectedCase && analysisState.status === "idle"
  // 로그가 없으면 버튼을 비활성화처럼 보이게 하되, 클릭은 가능하게 두어 팝업을 띄운다
  const noLogs = idle && !hasLogs
  // 실제 disabled: 케이스 미선택이거나 분석 진행 중일 때만
  const disabled = !idle

  return (
    <div className="analyze-header">
      <div className="analyze-header-actions">
        <button
          className={`analyze-btn ${isRunning ? "running" : ""} ${noLogs ? "no-logs" : ""}`}
          onClick={onAnalyze}
          disabled={disabled}
        >
          {isRunning ? "분석 중..." : "▶ 분석 시작"}
        </button>
        {isRunning && (
          <button className="cancel-btn" onClick={onCancel}>
            취소
          </button>
        )}
        <div className="analyze-header-right">
          <button className="settings-btn" onClick={onAnalyzeSettings} disabled={!selectedCase}>
            📋 분석 고급 설정
          </button>
          <button className="settings-btn" onClick={onSettings}>
            ⚙ Assistant 고급 설정
          </button>
          <button className="manage-btn" onClick={onCaseManage}>
            Case관리
          </button>
          <button className="manage-btn" onClick={onHistory}>
            분석 이력
          </button>
        </div>
      </div>
    </div>
  )
}
