const BASE_URL = "http://localhost:8000"

export async function submitAnalysis(defectId, options = {}) {
  const res = await fetch(`${BASE_URL}/api/defect/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ defect_id: defectId, ...options }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || "분석 요청 실패")
  }
  return res.json()
}

export async function pollAnalysis(jobId) {
  const res = await fetch(`${BASE_URL}/api/defect/analyze/${jobId}`)
  if (!res.ok) throw new Error("분석 상태 조회 실패")
  return res.json()
}

export async function getAnalysisProfiles() {
  const res = await fetch(`${BASE_URL}/api/profiles`)
  if (!res.ok) throw new Error("분석 프로파일 목록 조회 실패")
  return res.json()
}

export async function getPullers() {
  const res = await fetch(`${BASE_URL}/api/pullers`)
  if (!res.ok) throw new Error("Puller 목록 조회 실패")
  return res.json()
}

export async function getCases() {
  const res = await fetch(`${BASE_URL}/api/cases`)
  if (!res.ok) throw new Error("케이스 목록 조회 실패")
  return res.json()
}

export async function fetchDefect(pullerName, defectId) {
  const res = await fetch(`${BASE_URL}/api/defect/fetch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ puller_name: pullerName, defect_id: defectId }),
  })
  if (!res.ok) {
    const err = await res.json()
    throw new Error(err.detail || "서버 오류")
  }
  return res.json()
}

// Settings - Guidelines
export async function getGuidelines() {
  const res = await fetch(`${BASE_URL}/api/settings/guidelines`)
  if (!res.ok) throw new Error("시스템 분석 지침 조회 실패")
  return res.json()
}

export async function saveGuidelines(value) {
  const res = await fetch(`${BASE_URL}/api/settings/guidelines`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value }),
  })
  if (!res.ok) throw new Error("시스템 분석 지침 저장 실패")
  return res.json()
}

// Settings - Pipeline
export async function queryNumCtx() {
  const res = await fetch(`${BASE_URL}/api/settings/pipeline/num_ctx`)
  if (!res.ok) throw new Error("num_ctx 조회 실패")
  return res.json()
}

export async function getPipelineConfig() {
  const res = await fetch(`${BASE_URL}/api/settings/pipeline/config`)
  if (!res.ok) throw new Error("파이프라인 설정 조회 실패")
  return res.json()
}

export async function savePipelineConfig(data) {
  const res = await fetch(`${BASE_URL}/api/settings/pipeline/config`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error("파이프라인 설정 저장 실패")
  return res.json()
}

// Settings - Active profiles
export async function getActiveProfiles() {
  const res = await fetch(`${BASE_URL}/api/settings/active`)
  if (!res.ok) throw new Error("활성 프로파일 조회 실패")
  return res.json()
}

// Settings - LLM
export async function getLLMProfiles() {
  const res = await fetch(`${BASE_URL}/api/settings/llm/profiles`)
  if (!res.ok) throw new Error("LLM 프로필 목록 조회 실패")
  return res.json()
}

export async function getLLMModels(profileName) {
  const res = await fetch(`${BASE_URL}/api/settings/llm/models?profile=${encodeURIComponent(profileName)}`)
  if (!res.ok) throw new Error("LLM 모델 목록 조회 실패")
  return res.json()
}

export async function checkLLMConnection(profileName, model) {
  const res = await fetch(`${BASE_URL}/api/settings/llm/check`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ profile: profileName, model }),
  })
  if (!res.ok) throw new Error("LLM 연결 확인 실패")
  return res.json()
}

export async function getLLMConfig(profileName) {
  const res = await fetch(`${BASE_URL}/api/settings/llm/config?profile=${encodeURIComponent(profileName)}`)
  if (!res.ok) throw new Error("LLM 설정 조회 실패")
  return res.json()
}

export async function saveLLMConfig(profileName, config) {
  const res = await fetch(`${BASE_URL}/api/settings/llm/config`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ profile: profileName, ...config }),
  })
  if (!res.ok) throw new Error("LLM 설정 저장 실패")
  return res.json()
}

// Settings - Embedding
export async function getEmbeddingProfiles() {
  const res = await fetch(`${BASE_URL}/api/settings/embedding/profiles`)
  if (!res.ok) throw new Error("Embedding 프로필 목록 조회 실패")
  return res.json()
}

export async function getEmbeddingModels(profileName) {
  const res = await fetch(`${BASE_URL}/api/settings/embedding/models?profile=${encodeURIComponent(profileName)}`)
  if (!res.ok) throw new Error("Embedding 모델 목록 조회 실패")
  return res.json()
}

export async function checkEmbeddingConnection(profileName, model) {
  const res = await fetch(`${BASE_URL}/api/settings/embedding/check`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ profile: profileName, model }),
  })
  if (!res.ok) throw new Error("Embedding 연결 확인 실패")
  return res.json()
}

export async function getEmbeddingConfig(profileName) {
  const res = await fetch(`${BASE_URL}/api/settings/embedding/config?profile=${encodeURIComponent(profileName)}`)
  if (!res.ok) throw new Error("Embedding 설정 조회 실패")
  return res.json()
}

export async function saveEmbeddingConfig(profileName, config) {
  const res = await fetch(`${BASE_URL}/api/settings/embedding/config`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ profile: profileName, ...config }),
  })
  if (!res.ok) throw new Error("Embedding 설정 저장 실패")
  return res.json()
}