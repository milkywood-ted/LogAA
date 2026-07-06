import { useState, useEffect, useRef } from "react"
import { Routes, Route, useNavigate } from "react-router-dom"
import { submitAnalysis, subscribeAnalysis, cancelAnalysis, getDefectFiles } from "./api"
import Sidebar from "./components/Sidebar"
import CardWindow from "./components/CardWindow"
import AnalyzeSettingsModal from "./components/AnalyzeSettingsModal"
import SelectedFilesPanel from "./components/SelectedFilesPanel"
import InfoPanel from "./components/InfoPanel"
import ProfileSelector from "./components/ProfileSelector"
import AnalyzeHeader from "./components/AnalyzeHeader"
import ProgressPanel from "./components/ProgressPanel"
import ResultPanel from "./components/ResultPanel"
import ErrorPanel from "./components/ErrorPanel"
import SettingsPage from "./components/SettingsPage"
import ProfileManagePage from "./components/ProfileManagePage"
import CaseManagePage from "./components/CaseManagePage"
import HistoryPage from "./components/HistoryPage"
import NoLogsModal from "./components/NoLogsModal"
import "./App.css"

const INITIAL_ANALYSIS_STATE = { status: "idle", stage: "", progress: 0, report: null, error: null }

const SESSION_KEY = "logaa_selectedCase"

function loadSelectedCase() {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY)
    return raw ? JSON.parse(raw) : null
  } catch { return null }
}

function MainPage() {
  const navigate = useNavigate()
  const [selectedCase, setSelectedCase] = useState(loadSelectedCase)
  const [pullerError, setPullerError] = useState(null)
  const [selectedProfiles, setSelectedProfiles] = useState([])
  const [analysisState, setAnalysisState] = useState(INITIAL_ANALYSIS_STATE)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [resultExpanded, setResultExpanded] = useState(false)
  const [analyzeSettingsOpen, setAnalyzeSettingsOpen] = useState(false)
  const [selectedFiles, setSelectedFiles] = useState(null)
  const [userLogsRefreshKey, setUserLogsRefreshKey] = useState(0) // null = 전체, string[] = 선택
  const [hasLogs, setHasLogs] = useState(true)
  const [noLogsModalOpen, setNoLogsModalOpen] = useState(false)
  const [autoExpand, setAutoExpand] = useState(
    () => localStorage.getItem("result-auto-expand") !== "false"
  )
  const sseUnsubRef = useRef(null)
  const jobIdRef = useRef(null)

  useEffect(() => {
    if (selectedCase) {
      sessionStorage.setItem(SESSION_KEY, JSON.stringify(selectedCase))
    } else {
      sessionStorage.removeItem(SESSION_KEY)
    }
  }, [selectedCase])

  // 선택된 문제에 분석 가능한 로그 파일이 하나라도 있는지 확인한다
  // (기본 첨부 + 댓글 첨부 + 사용자 추가 로그 전부 고려)
  useEffect(() => {
    if (!selectedCase) { setHasLogs(true); return }
    let cancelled = false
    getDefectFiles(selectedCase.id)
      .then(data => {
        if (cancelled) return
        const total =
          (data.default?.length || 0) +
          (data.comment_attachment?.length || 0) +
          (data.user_added?.length || 0)
        setHasLogs(total > 0)
      })
      .catch(() => { if (!cancelled) setHasLogs(true) })
    return () => { cancelled = true }
  }, [selectedCase?.id, userLogsRefreshKey])

  function handleSelectCase(c) {
    setSelectedCase(c)
    setPullerError(null)
    stopSSE()
    setAnalysisState(INITIAL_ANALYSIS_STATE)
    setSelectedFiles(null)
  }

  function stopSSE() {
    if (sseUnsubRef.current) {
      sseUnsubRef.current()
      sseUnsubRef.current = null
    }
    jobIdRef.current = null
  }

  async function handleCancel() {
    const jobId = jobIdRef.current
    if (!jobId) return
    stopSSE()
    try {
      await cancelAnalysis(jobId)
    } catch (_) {}
    setAnalysisState(INITIAL_ANALYSIS_STATE)
  }

  async function handleAnalyze() {
    if (!selectedCase || analysisState.status === "running") return
    if (!hasLogs) { setNoLogsModalOpen(true); return }
    stopSSE()
    setAnalysisState({ status: "running", stage: "분석 요청 중...", progress: 0, report: null, error: null })
    try {
      const { job_id } = await submitAnalysis(selectedCase.id, {
        profile_names: selectedProfiles,
        ...(selectedFiles && { selected_files: selectedFiles }),
      })
      jobIdRef.current = job_id
      sseUnsubRef.current = subscribeAnalysis(job_id, {
        onProgress: (data) => {
          setAnalysisState(s => ({ ...s, stage: data.stage || "", progress: data.progress || 0 }))
        },
        onDone: (data) => {
          sseUnsubRef.current = null
          jobIdRef.current = null
          setAnalysisState({ status: "done", stage: "", progress: 100, report: data.result, error: null })
        },
        onError: (data) => {
          sseUnsubRef.current = null
          jobIdRef.current = null
          setAnalysisState({ status: "error", stage: "", progress: 0, report: null, error: data.error || "분석 오류" })
        },
        onCancelled: () => {
          sseUnsubRef.current = null
          jobIdRef.current = null
          setAnalysisState(INITIAL_ANALYSIS_STATE)
        },
      })
    } catch (e) {
      setAnalysisState({ status: "error", stage: "", progress: 0, report: null, error: e.message })
    }
  }

  useEffect(() => () => stopSSE(), [])

  function handlePullerError(err) {
    setPullerError(err)
    setSelectedCase(null)
  }

  function handleReportUpdate(updatedRefs) {
    setAnalysisState(prev => {
      if (!prev.report || !prev.report.matched_case) return prev
      return {
        ...prev,
        report: {
          ...prev.report,
          matched_case: {
            ...prev.report.matched_case,
            references: updatedRefs,
          },
        },
      }
    })
  }

  return (
    <div className="app-layout">
      <div className={`sidebar-wrapper ${sidebarCollapsed ? "collapsed" : ""}`}>
        <Sidebar
          selectedCase={selectedCase}
          onSelectCase={handleSelectCase}
          onPullerError={handlePullerError}
          collapsed={sidebarCollapsed}
          onToggle={() => setSidebarCollapsed(v => !v)}
        />
      </div>
      <div className="main-area">
        {pullerError ? (
          <div className="content-area">
            <ErrorPanel error={pullerError} />
          </div>
        ) : (
          <div className="main-content">
            <CardWindow title="문제 정보">
              <InfoPanel selectedCase={selectedCase} refreshKey={userLogsRefreshKey} />
            </CardWindow>
            <CardWindow title="분석">
              <div className="analysis-info-wrap">
                <div className="analysis-info-card">
                  <span className="analysis-info-label">분석 대상</span>
                  <span className="analysis-info-value">
                    {selectedCase ? `${selectedCase.id} : ${selectedCase.title || "제목 없음"}` : "케이스를 선택하세요"}
                  </span>
                </div>
                <div className="analysis-info-card">
                  <span className="analysis-info-label">분석 프로파일</span>
                  <span className="analysis-info-value analysis-info-value-profile">
                    <ProfileSelector
                      selectedProfiles={selectedProfiles}
                      onChange={setSelectedProfiles}
                    />
                    <button
                      className="profile-manage-btn"
                      onClick={() => navigate("/profiles")}
                      title="분석 프로파일과 도메인 지식을 관리합니다"
                    >
                      Profile and Domain Knowledge
                    </button>
                  </span>
                </div>
              </div>
              <AnalyzeHeader
                selectedCase={selectedCase}
                analysisState={analysisState}
                hasLogs={hasLogs}
                onAnalyze={handleAnalyze}
                onCancel={handleCancel}
                onSettings={() => navigate("/settings")}
                onAnalyzeSettings={() => setAnalyzeSettingsOpen(true)}
                onCaseManage={() => navigate("/cases")}
                onHistory={() => navigate("/history")}
              />
              <SelectedFilesPanel selectedFiles={selectedFiles} defectId={selectedCase?.id} />
              <ProgressPanel analysisState={analysisState} />
            </CardWindow>
            <CardWindow
              title="분석 결과"
              className="main-card-result"
              expanded={resultExpanded}
              onCollapse={() => setResultExpanded(false)}
              titleRight={
                <label className="result-auto-expand-label" onClick={e => e.stopPropagation()}>
                  <input
                    type="checkbox"
                    checked={autoExpand}
                    onChange={e => {
                      const val = e.target.checked
                      setAutoExpand(val)
                      localStorage.setItem("result-auto-expand", String(val))
                    }}
                  />
                  완료 시 자동으로 크게 보기
                </label>
              }
            >
              <ResultPanel
                analysisState={analysisState}
                caseId={selectedCase?.id}
                defectId={selectedCase?.id}
                autoExpand={autoExpand}
                onAutoExpand={() => setResultExpanded(true)}
                onReportUpdate={handleReportUpdate}
              />
            </CardWindow>
          </div>
        )}
      </div>
      {analyzeSettingsOpen && selectedCase && (
        <AnalyzeSettingsModal
          defectId={selectedCase.id}
          selectedFiles={selectedFiles}
          onChangeSelectedFiles={setSelectedFiles}
          onClose={() => { setAnalyzeSettingsOpen(false); setUserLogsRefreshKey(k => k + 1) }}
        />
      )}
      {noLogsModalOpen && (
        <NoLogsModal
          onAddLog={() => { setNoLogsModalOpen(false); setAnalyzeSettingsOpen(true) }}
          onClose={() => setNoLogsModalOpen(false)}
        />
      )}
    </div>
  )
}

function App() {
  return (
    <Routes>
      <Route path="/" element={<MainPage />} />
      <Route path="/settings" element={<SettingsPage />} />
      <Route path="/profiles" element={<ProfileManagePage />} />
      <Route path="/cases" element={<CaseManagePage />} />
      <Route path="/history" element={<HistoryPage />} />
    </Routes>
  )
}

export default App
