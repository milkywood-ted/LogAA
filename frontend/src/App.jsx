import { useState, useEffect, useRef } from "react"
import { submitAnalysis, pollAnalysis } from "./api"
import Sidebar from "./components/Sidebar"
import InfoPanel from "./components/InfoPanel"
import ProfileSelector from "./components/ProfileSelector"
import AnalyzeHeader from "./components/AnalyzeHeader"
import ProgressPanel from "./components/ProgressPanel"
import ResultPanel from "./components/ResultPanel"
import ErrorPanel from "./components/ErrorPanel"
import SettingsPage from "./components/SettingsPage"
import "./App.css"

function App() {
  const [selectedCase, setSelectedCase] = useState(null)
  const [pullerError, setPullerError] = useState(null)
  const [selectedProfiles, setSelectedProfiles] = useState([])
  const [analysisState, setAnalysisState] = useState({
    status: "idle", stage: "", progress: 0, report: null, error: null,
  })
  const [page, setPage] = useState("main") // "main" | "settings"
  const pollRef = useRef(null)

  function handleSelectCase(c) {
    setSelectedCase(c)
    setPullerError(null)
    stopPolling()
    setAnalysisState({ status: "idle", stage: "", progress: 0, report: null, error: null })
  }

  function stopPolling() {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  async function handleAnalyze() {
    if (!selectedCase || analysisState.status === "running") return
    stopPolling()
    setAnalysisState({ status: "running", stage: "분석 요청 중...", progress: 0, report: null, error: null })
    try {
      const { job_id } = await submitAnalysis(selectedCase.id, {
        profile_names: selectedProfiles,
      })
      pollRef.current = setInterval(async () => {
        try {
          const data = await pollAnalysis(job_id)
          if (data.status === "done") {
            stopPolling()
            setAnalysisState({ status: "done", stage: "", progress: 100, report: data.result, error: null })
          } else if (data.status === "error") {
            stopPolling()
            setAnalysisState({ status: "error", stage: "", progress: 0, report: null, error: data.error })
          } else {
            setAnalysisState(s => ({ ...s, stage: data.stage || "", progress: data.progress || 0 }))
          }
        } catch (_) { /* 네트워크 오류 시 다음 폴링 시도 */ }
      }, 2000)
    } catch (e) {
      setAnalysisState({ status: "error", stage: "", progress: 0, report: null, error: e.message })
    }
  }

  useEffect(() => () => stopPolling(), [])

  function handlePullerError(err) {
    setPullerError(err)
    setSelectedCase(null)
  }

  return (
    <div className="app-layout">
      <Sidebar
        selectedCase={selectedCase}
        onSelectCase={handleSelectCase}
        onPullerError={handlePullerError}
      />
      <div className="main-area">
        {page === "settings" ? (
          <SettingsPage onBack={() => setPage("main")} />
        ) : (
          <>
            {pullerError ? (
              <div className="content-area">
                <ErrorPanel error={pullerError} />
              </div>
            ) : (
              <>
                <InfoPanel selectedCase={selectedCase} />
                <ProfileSelector
                  selectedProfiles={selectedProfiles}
                  onChange={setSelectedProfiles}
                />
                <AnalyzeHeader
                  selectedCase={selectedCase}
                  analysisState={analysisState}
                  onAnalyze={handleAnalyze}
                  onSettings={() => setPage("settings")}
                />
                <div className="content-area">
                    <ProgressPanel analysisState={analysisState} />
                    <ResultPanel analysisState={analysisState} />
                </div>
              </>
            )}
          </>
        )}
      </div>
    </div>
  )
}

export default App