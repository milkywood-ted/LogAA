import { useState } from "react"
import Sidebar from "./components/Sidebar"
import InfoPanel from "./components/InfoPanel"
import AnalyzeHeader from "./components/AnalyzeHeader"
import ProgressPanel from "./components/ProgressPanel"
import ResultPanel from "./components/ResultPanel"
import ErrorPanel from "./components/ErrorPanel"
import "./App.css"

function App() {
  const [selectedCase, setSelectedCase] = useState(null)
  const [pullerError, setPullerError] = useState(null)
  const [analysisState, setAnalysisState] = useState({
    status: "idle",
    stages: {},
    report: null,
    error: null,
  })

  function handleSelectCase(c) {
    setSelectedCase(c)
    setPullerError(null)
    setAnalysisState({ status: "idle", stages: {}, report: null, error: null })
  }

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
        {pullerError ? (
          <div className="content-area">
            <ErrorPanel error={pullerError} />
          </div>
        ) : (
          <>
            <InfoPanel selectedCase={selectedCase} />
            <AnalyzeHeader
              selectedCase={selectedCase}
              analysisState={analysisState}
              onAnalyze={() => setAnalysisState(s => ({ ...s, status: "running" }))}
            />
            <div className="content-area">
              <div className="bottom-panels">
                <ProgressPanel analysisState={analysisState} />
                <ResultPanel analysisState={analysisState} />
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

export default App
