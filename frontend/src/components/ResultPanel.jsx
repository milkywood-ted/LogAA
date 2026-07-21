import { useState, useEffect, useRef } from "react"
import ReactMarkdown from "react-markdown"
import { addKBCaseReference, deleteKBCaseReference, getKBCaseReferences } from "../api/assistant"

const VERDICT_ICON = {
  "문제": "🔴", "문제 아님": "🟢", "판정 불가": "🔵", "불확실": "🟡", "알 수 없음": "⚪",
}
const VERDICT_CLASS = {
  "문제": "verdict-problem",
  "문제 아님": "verdict-no-defect",
  "판정 불가": "verdict-case-undetermined",
  "불확실": "verdict-uncertain",
  "알 수 없음": "verdict-unknown",
}

// 케이스가 원 분석에서 받은 판정 — 로그와의 일치도와는 독립된 축이다.
const CASE_VERDICT_LABEL = { defect: "결함", no_defect: "결함 아님", undetermined: "판정 불가" }

const DEFECT_SYSTEM = "Kona"

export default function ResultPanel({ analysisState, caseId, defectId, autoExpand, onAutoExpand, onReportUpdate }) {
  const { status, report, error } = analysisState
  const [copied, setCopied] = useState(false)
  const prevStatusRef = useRef(status)

  useEffect(() => {
    if (prevStatusRef.current !== "done" && status === "done" && autoExpand) {
      onAutoExpand?.()
    }
    prevStatusRef.current = status
  }, [status, autoExpand, onAutoExpand])

  function handleCopy() {
    if (!report?.report_md) return
    navigator.clipboard.writeText(report.report_md).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  function handleDownload() {
    if (!report?.report_md) return
    const filename = caseId ? `${caseId}_report.md` : "analysis_report.md"
    const blob = new Blob([report.report_md], { type: "text/markdown;charset=utf-8" })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = filename
    a.click()
    URL.revokeObjectURL(url)
  }

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
    const matchedCase = report.matched_case
    const patterns = report.match_result?.matched ?? []
    const warnings = report.warnings ?? []
    const winnerProfiles = report.winner_profile_names ?? []

    return (
      <div className="result-panel">
        {warnings.length > 0 && (
          <div className="result-warnings">
            {warnings.map((w, i) => (
              <div key={i} className="result-warning-item">⚠ {w}</div>
            ))}
          </div>
        )}

        <div className={`result-verdict ${VERDICT_CLASS[verdict] ?? ""}`}>
          <span className="result-verdict-icon">{VERDICT_ICON[verdict] ?? "❓"}</span>
          <span className="result-verdict-text">{verdict}</span>
          {score != null && (
            <span className="result-score" title="매칭 케이스의 패턴 시그니처가 로그에 재현된 비율">
              일치도 {(score * 100).toFixed(0)}%
            </span>
          )}
        </div>

        <div className="result-meta">
          {matchedCase && (
            <>
              <div className="result-meta-row">
                <span className="result-meta-label">매칭 케이스</span>
                <span className="result-meta-value">
                  {matchedCase.name}
                  {(matchedCase.chip_tags ?? []).length > 0 && (
                    <span className="result-chip-badges">
                      {matchedCase.chip_tags.map((c, i) => (
                        <span key={i} className="result-chip-badge">{c}</span>
                      ))}
                    </span>
                  )}
                </span>
              </div>
              {matchedCase.case_verdict && (
                <div className="result-meta-row">
                  <span className="result-meta-label">원 분석 판정</span>
                  <span className="result-meta-value">
                    {CASE_VERDICT_LABEL[matchedCase.case_verdict] ?? matchedCase.case_verdict}
                    {matchedCase.verdict_rationale && (
                      <span className="result-meta-note">{matchedCase.verdict_rationale}</span>
                    )}
                  </span>
                </div>
              )}
              {matchedCase.action_summary && (
                <div className="result-meta-row">
                  <span className="result-meta-label">원 분석 조치</span>
                  <span className="result-meta-value">
                    {matchedCase.action_summary}
                    {(matchedCase.action_details ?? []).map((d, i) => (
                      <span key={i} className="result-meta-note">{d}</span>
                    ))}
                  </span>
                </div>
              )}
              {defectId && (
                <DefectReferenceControl
                  kbCaseId={matchedCase.case_id}
                  defectId={defectId}
                  initialRefs={matchedCase.references ?? []}
                  onReportUpdate={onReportUpdate}
                />
              )}
            </>
          )}
          {winnerProfiles.length > 0 && (
            <div className="result-meta-row">
              <span className="result-meta-label">선정 프로파일</span>
              <span className="result-meta-value">{winnerProfiles.join(", ")}</span>
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
            <div className="result-report-header">
              <span className="result-report-title">분석 리포트</span>
              <div className="result-report-actions">
                <button className="result-save-btn" onClick={handleCopy}>
                  {copied ? "복사됨" : "클립보드 복사"}
                </button>
                <button className="result-save-btn" onClick={handleDownload}>
                  .md 다운로드
                </button>
              </div>
            </div>
            <div className="result-report-body markdown-body">
              <ReactMarkdown>{report.report_md}</ReactMarkdown>
            </div>
          </div>
        )}

        <MinorityReportSection
          minorityReports={report.minority_reports ?? []}
          mainScore={score}
          traversalMode={report.traversal_mode ?? "single"}
        />
      </div>
    )
  }

  return <div className="result-panel"><div className="panel-empty">결과가 없습니다</div></div>
}

// ─── Defect 참조 등록/제거 컨트롤 ───────────────────────────────────────────

function DefectReferenceControl({ kbCaseId, defectId, initialRefs, onReportUpdate }) {
  // initialRefs: [{id, system, reference_id}, ...] — 분석 시점의 케이스 참조 목록
  const existing = initialRefs.find(
    r => r.system === DEFECT_SYSTEM && r.reference_id === String(defectId)
  )
  const [refId, setRefId] = useState(existing?.id ?? null)  // DB row id, null이면 미등록
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function refreshRefs() {
    try {
      const fresh = await getKBCaseReferences(kbCaseId)
      onReportUpdate?.(fresh)
    } catch {
      // report update 실패는 무시 — UI는 local refId로 정상 동작
    }
  }

  async function handleAdd() {
    setBusy(true)
    setError(null)
    try {
      const result = await addKBCaseReference(kbCaseId, {
        system: DEFECT_SYSTEM,
        reference_id: String(defectId),
      })
      setRefId(result.id)
      await refreshRefs()
    } catch (e) {
      setError(e.message || "등록에 실패했습니다.")
    } finally {
      setBusy(false)
    }
  }

  async function handleRemove() {
    if (!window.confirm(`케이스에서 이 Defect(${defectId}) 등록을 제거할까요?`)) return
    setBusy(true)
    setError(null)
    try {
      await deleteKBCaseReference(kbCaseId, refId)
      setRefId(null)
      await refreshRefs()
    } catch (e) {
      setError(e.message || "제거에 실패했습니다.")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="result-defect-ref">
      {refId !== null ? (
        <button className="result-defect-ref-btn registered" onClick={handleRemove} disabled={busy}>
          {busy ? "처리 중..." : `✓ Defect 등록됨 — 제거`}
        </button>
      ) : (
        <button className="result-defect-ref-btn" onClick={handleAdd} disabled={busy}>
          {busy ? "처리 중..." : "이 케이스에 Defect 등록"}
        </button>
      )}
      {error && <span className="result-defect-ref-error">{error}</span>}
    </div>
  )
}

function MinorityReportSection({ minorityReports, mainScore, traversalMode }) {
  const visible = minorityReports.filter(mr => mr.match_result.score > 0)
  const isEnsemble = traversalMode === "ensemble" || traversalMode === "first_hit"

  if (visible.length === 0) {
    if (!isEnsemble) return null
    return (
      <div className="minority-report">
        <div className="minority-report-title">기타 후보 케이스</div>
        <div className="minority-report-empty">이 분석에서 추가 후보 케이스가 없습니다</div>
      </div>
    )
  }

  return (
    <div className="minority-report">
      <div className="minority-report-title">기타 후보 케이스</div>
      <table className="minority-report-table">
        <thead>
          <tr>
            <th>케이스</th>
            <th>칩</th>
            <th>관련성</th>
            <th>패턴 매칭</th>
            <th>매칭 패턴</th>
            <th>발견 전문가</th>
          </tr>
        </thead>
        <tbody>
          {visible.map((mr, i) => {
            const matchedPatterns = mr.match_result.matched.map(p => p.name).join(", ") || "(없음)"
            const chipTags = mr.matched_case.chip_tags ?? []
            const sourceProfiles = mr.source_profile_names ?? []
            return (
              <tr key={i}>
                <td>{mr.matched_case.name}</td>
                <td>
                  {chipTags.length > 0 ? (
                    <span className="result-chip-badges">
                      {chipTags.map((c, j) => (
                        <span key={j} className="result-chip-badge">{c}</span>
                      ))}
                    </span>
                  ) : "—"}
                </td>
                <td>{(mr.matched_case.relevance_score * 100).toFixed(0)}%</td>
                <td>{(mr.match_result.score * 100).toFixed(0)}%</td>
                <td>{matchedPatterns}</td>
                <td>{sourceProfiles.length > 0 ? sourceProfiles.join(", ") : "—"}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
