export default function ErrorPanel({ error }) {
  if (!error) return null

  return (
    <div className="error-panel">
      <div className="error-panel-title">⚠️ Puller 오류</div>
      <div className="error-panel-defect">Defect ID: {error.defect_id}</div>
      <div className="error-panel-message">{error.message}</div>
    </div>
  )
}
