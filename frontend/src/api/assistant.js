import { _request, _sseUrl } from "./_http"

// ── 분석 실행 ─────────────────────────────────────────────────────────────────

export async function submitAnalysis(defectId, options = {}) {
  return _request("POST", "/api/defect/analyze", { defect_id: defectId, ...options })
}

export async function cancelAnalysis(jobId) {
  return _request("DELETE", `/api/defect/analyze/${jobId}`)
}

export async function pollAnalysis(jobId) {
  return _request("GET", `/api/defect/analyze/${jobId}`)
}

export function subscribeAnalysis(jobId, { onProgress, onDone, onError, onCancelled }) {
  const es = new EventSource(_sseUrl(`/api/defect/analyze/${jobId}/stream`))

  es.addEventListener("progress", (e) => {
    try { onProgress?.(JSON.parse(e.data)) } catch {}
  })
  es.addEventListener("done", (e) => {
    try { onDone?.(JSON.parse(e.data)) } catch {}
    es.close()
  })
  es.addEventListener("cancelled", (e) => {
    try { onCancelled?.(JSON.parse(e.data)) } catch {}
    es.close()
  })
  es.addEventListener("error", (e) => {
    if (e.data) {
      try { onError?.(JSON.parse(e.data)) } catch {}
      es.close()
    } else {
      onError?.({ status: "error", error: "SSE 연결 오류" })
      es.close()
    }
  })

  return () => es.close()
}

// ── 파일 목록 ────────────────────────────────────────────────────────────────

export async function getDefectFiles(defectId) {
  return _request("GET", `/api/defect/${defectId}/files`)
}

export async function addUserLog(defectId, srcPath) {
  return _request("POST", `/api/defect/${defectId}/user-logs`, { src_path: srcPath })
}

export async function deleteUserLog(defectId, filename) {
  return _request("DELETE", `/api/defect/${defectId}/user-logs/${encodeURIComponent(filename)}`)
}

// ── 분석 프로파일 ─────────────────────────────────────────────────────────────

export async function getAnalysisProfiles() {
  return _request("GET", "/api/profiles")
}

export async function getAnalysisProfile(name) {
  return _request("GET", `/api/profiles/${encodeURIComponent(name)}`)
}

export async function createAnalysisProfile(profile) {
  return _request("POST", "/api/profiles", profile)
}

export async function updateAnalysisProfile(name, profile) {
  return _request("PUT", `/api/profiles/${encodeURIComponent(name)}`, profile)
}

export async function deleteAnalysisProfile(name) {
  return _request("DELETE", `/api/profiles/${encodeURIComponent(name)}`)
}

// ── 도메인 지식 ───────────────────────────────────────────────────────────────

export async function getKnowledgeList() {
  return _request("GET", "/api/knowledge")
}

export async function getKnowledge(id) {
  return _request("GET", `/api/knowledge/${id}`)
}

export async function createKnowledge(knowledge) {
  return _request("POST", "/api/knowledge", knowledge)
}

export async function updateKnowledge(id, knowledge) {
  return _request("PUT", `/api/knowledge/${id}`, knowledge)
}

export async function deleteKnowledge(id) {
  return _request("DELETE", `/api/knowledge/${id}`)
}

// ── Settings / Guidelines ─────────────────────────────────────────────────────

export async function getGuidelines() {
  return _request("GET", "/api/settings/guidelines")
}

export async function saveGuidelines(value) {
  return _request("POST", "/api/settings/guidelines", { value })
}

// ── Settings / Pipeline ───────────────────────────────────────────────────────

export async function queryNumCtx() {
  return _request("GET", "/api/settings/pipeline/num_ctx")
}

export async function getPipelineConfig() {
  return _request("GET", "/api/settings/pipeline/config")
}

export async function savePipelineConfig(data) {
  return _request("POST", "/api/settings/pipeline/config", data)
}

// ── Settings / Server ─────────────────────────────────────────────────────────

export async function getServerConfig() {
  return _request("GET", "/api/settings/server/config")
}

export async function saveServerConfig(data) {
  return _request("POST", "/api/settings/server/config", data)
}

// ── Settings / Active ─────────────────────────────────────────────────────────

export async function getActiveProfiles() {
  return _request("GET", "/api/settings/active")
}

// ── Settings / LLM ───────────────────────────────────────────────────────────

export async function getLLMProfiles() {
  return _request("GET", "/api/settings/llm/profiles")
}

export async function getLLMModels(profileName) {
  return _request("GET", `/api/settings/llm/models?profile=${encodeURIComponent(profileName)}`)
}

export async function checkLLMConnection(profileName, model) {
  return _request("POST", "/api/settings/llm/check", { profile: profileName, model })
}

export async function getLLMConfig(profileName) {
  return _request("GET", `/api/settings/llm/config?profile=${encodeURIComponent(profileName)}`)
}

export async function saveLLMConfig(profileName, config) {
  return _request("POST", "/api/settings/llm/config", { profile: profileName, ...config })
}

// ── Settings / Reranker ───────────────────────────────────────────────────────

export async function getRerankerConfig() {
  return _request("GET", "/api/settings/reranker/config")
}

export async function saveRerankerConfig(data) {
  return _request("POST", "/api/settings/reranker/config", data)
}

// ── Settings / Embedding ──────────────────────────────────────────────────────

export async function getEmbeddingProfiles() {
  return _request("GET", "/api/settings/embedding/profiles")
}

export async function getEmbeddingModels(profileName) {
  return _request("GET", `/api/settings/embedding/models?profile=${encodeURIComponent(profileName)}`)
}

export async function checkEmbeddingConnection(profileName, model) {
  return _request("POST", "/api/settings/embedding/check", { profile: profileName, model })
}

export async function getEmbeddingConfig(profileName) {
  return _request("GET", `/api/settings/embedding/config?profile=${encodeURIComponent(profileName)}`)
}

export async function saveEmbeddingConfig(profileName, config) {
  return _request("POST", "/api/settings/embedding/config", { profile: profileName, ...config })
}

export async function getChips() {
  return _request("GET", "/api/settings/chips")
}

// ── KB 케이스 ─────────────────────────────────────────────────────────────────

export async function getKBCases() {
  return _request("GET", "/api/cases")
}

export async function getKBCase(id) {
  return _request("GET", `/api/cases/${id}`)
}

export async function createKBCase(data) {
  return _request("POST", "/api/cases", data)
}

export async function updateKBCase(id, data) {
  return _request("PUT", `/api/cases/${id}`, data)
}

export async function deleteKBCase(id) {
  return _request("DELETE", `/api/cases/${id}`)
}

export async function syncKBCases() {
  return _request("POST", "/api/cases/sync")
}

export async function getKBCasePatterns(caseId) {
  return _request("GET", `/api/cases/${caseId}/patterns`)
}

export async function linkKBCasePattern(caseId, patternId) {
  return _request("POST", `/api/cases/${caseId}/patterns/${patternId}`)
}

export async function unlinkKBCasePattern(caseId, patternId) {
  return _request("DELETE", `/api/cases/${caseId}/patterns/${patternId}`)
}

export async function getKBCaseReferences(caseId) {
  return _request("GET", `/api/cases/${caseId}/references`)
}

export async function addKBCaseReference(caseId, data) {
  return _request("POST", `/api/cases/${caseId}/references`, data)
}

export async function deleteKBCaseReference(caseId, refId) {
  return _request("DELETE", `/api/cases/${caseId}/references/${refId}`)
}

// ── 패턴 ─────────────────────────────────────────────────────────────────────

export async function getPatterns(type) {
  const query = type ? `?type=${encodeURIComponent(type)}` : ""
  return _request("GET", `/api/patterns${query}`)
}

export async function getPattern(id) {
  return _request("GET", `/api/patterns/${id}`)
}

export async function createPattern(data) {
  return _request("POST", "/api/patterns", data)
}

export async function updatePattern(id, data) {
  return _request("PUT", `/api/patterns/${id}`, data)
}

export async function deletePattern(id) {
  return _request("DELETE", `/api/patterns/${id}`)
}

// ── 분석 이력 ─────────────────────────────────────────────────────────────────

export async function getHistoryList({ limit = 50, defectId } = {}) {
  const params = new URLSearchParams({ limit })
  if (defectId != null) params.set("defect_id", defectId)
  return _request("GET", `/api/history?${params}`)
}

export async function getHistoryItem(id) {
  return _request("GET", `/api/history/${id}`)
}

export async function deleteHistoryItem(id) {
  return _request("DELETE", `/api/history/${id}`)
}

export async function clearHistory() {
  return _request("DELETE", "/api/history")
}
