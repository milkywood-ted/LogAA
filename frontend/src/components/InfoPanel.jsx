export default function InfoPanel({ selectedCase }) {
  if (!selectedCase) {
    return (
      <div className="info-panel">
        <div className="panel-title">문제 정보</div>
        <div className="panel-empty">케이스를 선택하세요</div>
      </div>
    )
  }

  return (
    <div className="info-panel">
      <div className="panel-title">문제 정보</div>

      <div className="info-row">
        <span className="info-label">{selectedCase.id}</span>
        <span className="info-value">가져옴 · {new Date(selectedCase.fetchedAt).toLocaleString("ko-KR")}</span>
      </div>

      {selectedCase.description && (
        <div className="info-description">
          <div className="info-label" style={{ marginBottom: 6 }}>문제 설명</div>
          <div className="info-description-text">{selectedCase.description}</div>
        </div>
      )}

      {selectedCase.files?.length > 0 && (
        <div className="info-files">
          <div className="info-label" style={{ marginBottom: 6 }}>첨부파일</div>
          {selectedCase.files.map((f, i) => (
            <div key={i} className="info-file-item">
              <span className="info-file-status">✅</span>
              <span className="info-file-name">{typeof f === "string" ? f : f.filename}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
