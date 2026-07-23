import { useState, useEffect, useRef } from "react"
import ReactMarkdown from "react-markdown"
import { addKBCaseReference, deleteKBCaseReference, getKBCaseReferences } from "../api/assistant"

// verdict 는 유사도 판정만을 뜻한다(2026-07-23 정정) — "문제"류 단어는 결함
// 확정으로 오독되므로 쓰지 않는다. 케이스 원 판정("결함"/"비결함"/…)은 완전히
// 별개 축(CASE_VERDICT_LABEL, 아래)이며 이 두 맵과 섞이지 않는다.
const VERDICT_ICON = {
  "유사도 높음": "🔴", "유사도 중간": "🟡", "유사도 낮음": "⚪",
}
const VERDICT_CLASS = {
  "유사도 높음": "verdict-problem", "유사도 중간": "verdict-uncertain", "유사도 낮음": "verdict-unknown",
}

// 케이스의 원 분석 판정 어휘 — 분석 실행 판정(VERDICT_ICON 등)과는 다른 축이다(C5).
// CaseManagePage.jsx 의 VERDICT_LABEL 과 동일하게 유지 — 백엔드는 원본 토큰만
// 내려주므로(case_report.py 는 LLM 프롬프트용, API 응답은 raw token) 프론트가
// 표시용 라벨로 바꾼다.
const CASE_VERDICT_LABEL = { defect: "결함", no_defect: "비결함", undetermined: "판정불가" }

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
            <span className="result-score">{(score * 100).toFixed(0)}점</span>
          )}
        </div>

        {report.pool_conflict_warning && (
          <div className="result-pool-conflict-warning">{report.pool_conflict_warning}</div>
        )}

        {verdict === "유사도 중간" && <UncertainChoiceControl />}

        <div className="result-meta">
          {matchedCase && (
            <>
              <div className="result-meta-row">
                <span className="result-meta-label">매칭 케이스</span>
                <span className="result-meta-value">
                  {matchedCase.name}
                  {matchedCase.case_verdict && (
                    <span className="result-case-verdict-badge">
                      원 판정: {CASE_VERDICT_LABEL[matchedCase.case_verdict] ?? matchedCase.case_verdict}
                    </span>
                  )}
                  {(matchedCase.chip_tags ?? []).length > 0 && (
                    <span className="result-chip-badges">
                      {matchedCase.chip_tags.map((c, i) => (
                        <span key={i} className="result-chip-badge">{c}</span>
                      ))}
                    </span>
                  )}
                </span>
              </div>
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

// ─── 불확실 그레이존 선택 (§1.2-A) ──────────────────────────────────────────
// 일치도가 낮거나 fallback 로 케이스 귀속이 깨져(P1') 시스템이 판정을 확정하지
// 않은 상태 — 사용자가 직접 판단하거나 신규 문제로 넘길지 고른다. 신규 문제
// 파이프라인은 별도 트랙(프론트 노출·승인 흐름 미완성)이라 "이관"은 지금은
// 아무 동작도 하지 않는 no-op 이다 — 이 화면에 실제로 얹었을 때 사용성이
// 어떤지 조기에 보기 위한 것으로, 신규 파이프라인 완성 후 연결한다.
function UncertainChoiceControl() {
  const [choice, setChoice] = useState(null)   // null | "manual" | "transfer"

  return (
    <div className="result-uncertain-choice">
      <span className="result-uncertain-choice-label">그레이존 — 어떻게 진행할까요?</span>
      <div className="result-uncertain-choice-buttons">
        <button
          className={`result-uncertain-choice-btn ${choice === "manual" ? "selected" : ""}`}
          onClick={() => setChoice("manual")}
        >
          수동 분석
        </button>
        <button
          className={`result-uncertain-choice-btn ${choice === "transfer" ? "selected" : ""}`}
          onClick={() => setChoice("transfer")}
        >
          신규 문제로 이관
        </button>
      </div>
    </div>
  )
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
