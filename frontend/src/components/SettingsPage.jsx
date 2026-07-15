import { useState, useEffect } from "react"
import { useNavigate } from "react-router-dom"
import {
  getActiveProfiles,
  getLLMProfiles, getLLMModels, checkLLMConnection, getLLMConfig, saveLLMConfig,
  getRerankerConfig, saveRerankerConfig,
  getEmbeddingProfiles, getEmbeddingModels, checkEmbeddingConnection, getEmbeddingConfig, saveEmbeddingConfig,
  getPipelineConfig, savePipelineConfig, queryNumCtx,
  getServerConfig, saveServerConfig,
  getGuidelines, saveGuidelines,
} from "../api"

function useAsyncOperation() {
  const [error, setError] = useState(null)
  const [saveMsg, setSaveMsg] = useState(null)

  function showSaveMsg(msg, duration = 2000) {
    setSaveMsg(msg)
    setTimeout(() => setSaveMsg(null), duration)
  }

  return { error, setError, saveMsg, showSaveMsg }
}

function useModelSection({ getProfiles, getModels, checkConnection, getConfig, saveConfig, activeKey }) {
  const [profiles, setProfiles] = useState([])
  const [selectedProfile, setSelectedProfile] = useState("")
  const [models, setModels] = useState([])
  const [selectedModel, setSelectedModel] = useState("")
  const [savedModel, setSavedModel] = useState("")
  const [connectionStatus, setConnectionStatus] = useState(null)
  const [config, setConfig] = useState({ provider: "", max_tokens: "", timeout: "", report_temperature: "" })
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [loading, setLoading] = useState({ profiles: false, models: false, connection: false, config: false, save: false })
  const { error, setError, saveMsg, showSaveMsg } = useAsyncOperation()

  useEffect(() => {
    setLoading(l => ({ ...l, profiles: true }))
    Promise.all([getProfiles(), getActiveProfiles().catch(() => ({}))])
      .then(([data, active]) => {
        const list = data.profiles || []
        setProfiles(list)
        if (list.length > 0) {
          const activeName = active?.[activeKey]
          const match = list.find(p => p.name === activeName)
          setSelectedProfile(match ? activeName : list[0].name)
        }
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(l => ({ ...l, profiles: false })))
  }, [])

  useEffect(() => {
    if (!selectedProfile) return
    setModels([])
    setSelectedModel("")
    setConnectionStatus(null)
    setLoading(l => ({ ...l, config: true }))
    getConfig(selectedProfile)
      .then(data => {
        setConfig({
          provider: data.provider || "",
          max_tokens: data.max_tokens || "",
          timeout: data.timeout || "",
          report_temperature: data.report_temperature || "",
        })
        if (data.model) {
          setSelectedModel(data.model)
          setSavedModel(data.model)
        }
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(l => ({ ...l, config: false })))
  }, [selectedProfile])

  async function handleGetModels() {
    if (!selectedProfile) return
    setLoading(l => ({ ...l, models: true }))
    setError(null)
    try {
      const data = await getModels(selectedProfile)
      setModels(data.models || [])
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(l => ({ ...l, models: false }))
    }
  }

  async function handleCheckConnection() {
    if (!selectedProfile || !selectedModel) return
    setLoading(l => ({ ...l, connection: true }))
    setConnectionStatus(null)
    try {
      const data = await checkConnection(selectedProfile, selectedModel)
      setConnectionStatus(data.connected ? "ok" : "error")
    } catch {
      setConnectionStatus("error")
    } finally {
      setLoading(l => ({ ...l, connection: false }))
    }
  }

  async function handleSave() {
    if (!selectedProfile) return
    setLoading(l => ({ ...l, save: true }))
    try {
      await saveConfig(selectedProfile, { ...config, model: selectedModel })
      setSavedModel(selectedModel)
      showSaveMsg("저장되었습니다")
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(l => ({ ...l, save: false }))
    }
  }

  return {
    profiles, selectedProfile, setSelectedProfile,
    models, selectedModel, setSelectedModel, savedModel,
    connectionStatus,
    config, setConfig,
    showAdvanced, setShowAdvanced,
    loading,
    error, saveMsg,
    handleGetModels, handleCheckConnection, handleSave,
  }
}

function ModelSection({ title, getProfiles, getModels, checkConnection, getConfig, saveConfig, activeKey, hideAdvanced = false }) {
  const {
    profiles, selectedProfile, setSelectedProfile,
    models, selectedModel, setSelectedModel, savedModel,
    connectionStatus,
    config, setConfig,
    showAdvanced, setShowAdvanced,
    loading,
    error, saveMsg,
    handleGetModels, handleCheckConnection, handleSave,
  } = useModelSection({ getProfiles, getModels, checkConnection, getConfig, saveConfig, activeKey })

  return (
    <div className="settings-section">
      <div className="settings-section-title">{title}</div>

      {error && <div className="settings-error">{error}</div>}

      <div className="settings-row">
        <label className="settings-label">프로필</label>
        <select
          className="settings-input"
          value={selectedProfile}
          onChange={e => setSelectedProfile(e.target.value)}
        >
          {profiles.length === 0 && <option value="">프로필 없음</option>}
          {profiles.map(p => (
            <option key={p.name} value={p.name}>{p.name}</option>
          ))}
        </select>
      </div>

      <div className="settings-row">
        <label className="settings-label">모델</label>
        <div className="settings-model-row">
          <select
            className="settings-input"
            value={selectedModel}
            onChange={e => setSelectedModel(e.target.value)}
          >
            {models.length === 0 && <option value={selectedModel}>{selectedModel ? `${selectedModel}${selectedModel === savedModel ? " ✅" : ""}` : "모델 목록을 조회하세요"}</option>}
            {models.map(m => (
              <option key={m} value={m}>{m === savedModel ? `${m} ✅` : m}</option>
            ))}
          </select>
          <button
            className="settings-btn-sm"
            onClick={handleGetModels}
            disabled={loading.models || !selectedProfile}
          >
            {loading.models ? "조회 중..." : "목록 조회"}
          </button>
          <button
            className="settings-btn-sm"
            onClick={handleCheckConnection}
            disabled={loading.connection || !selectedModel}
          >
            {loading.connection ? "확인 중..." : "연결 확인"}
          </button>
          {connectionStatus === "ok" && <span className="conn-ok">✅ 연결됨</span>}
          {connectionStatus === "error" && <span className="conn-error">❌ 실패</span>}
        </div>
      </div>

      {!hideAdvanced && (
        <>
          <div className="settings-advanced-toggle" onClick={() => setShowAdvanced(v => !v)}>
            {showAdvanced ? "▼" : "▶"} 고급 옵션
          </div>

          {showAdvanced && (
            <div className="settings-advanced">
              <div className="settings-row">
                <label className="settings-label">Provider</label>
                <select
                  className="settings-input"
                  value={config.provider}
                  onChange={e => setConfig(c => ({ ...c, provider: e.target.value }))}
                >
                  <option value="openai">openai</option>
                  <option value="anthropic">anthropic</option>
                  <option value="anthropic-bedrock">anthropic-bedrock</option>
                </select>
              </div>
              <div className="settings-row">
                <label className="settings-label">Max Tokens</label>
                <input
                  className="settings-input"
                  type="number"
                  value={config.max_tokens}
                  onChange={e => setConfig(c => ({ ...c, max_tokens: e.target.value }))}
                />
              </div>
              <div className="settings-row">
                <label className="settings-label">Timeout (초)</label>
                <input
                  className="settings-input"
                  type="number"
                  value={config.timeout}
                  onChange={e => setConfig(c => ({ ...c, timeout: e.target.value }))}
                />
              </div>
              <div className="settings-row">
                <label className="settings-label">Temperature</label>
                <input
                  className="settings-input"
                  type="range"
                  step="0.05"
                  min="0"
                  max="1"
                  value={config.report_temperature || 0}
                  onChange={e => setConfig(c => ({ ...c, report_temperature: e.target.value }))}
                />
                <span style={{ minWidth: "2.5rem", textAlign: "right" }}>
                  {Number(config.report_temperature || 0).toFixed(2)}
                </span>
              </div>
            </div>
          )}
        </>
      )}

      <div className="settings-actions">
        {saveMsg && <span className="settings-save-msg">{saveMsg}</span>}
        <button
          className="settings-save-btn"
          onClick={handleSave}
          disabled={loading.save || !selectedProfile}
        >
          {loading.save ? "저장 중..." : "저장"}
        </button>
      </div>
    </div>
  )
}

function RerankerProfileRow({ label, value, onChange, profiles, emptyLabel }) {
  const [model, setModel] = useState("")
  const [connectionStatus, setConnectionStatus] = useState(null)
  const [checking, setChecking] = useState(false)

  useEffect(() => {
    setConnectionStatus(null)
    if (!value) { setModel(""); return }
    getLLMConfig(value)
      .then(data => setModel(data.model || ""))
      .catch(() => setModel(""))
  }, [value])

  async function handleCheck() {
    if (!value) return
    setChecking(true)
    setConnectionStatus(null)
    try {
      const data = await checkLLMConnection(value, model || undefined)
      setConnectionStatus(data.connected ? "ok" : "error")
    } catch {
      setConnectionStatus("error")
    } finally {
      setChecking(false)
    }
  }

  return (
    <div className="settings-row">
      <label className="settings-label">{label}</label>
      <div className="settings-model-row">
        <select
          className="settings-input"
          value={value}
          onChange={e => onChange(e.target.value)}
        >
          <option value="">{emptyLabel}</option>
          {profiles.map(p => (
            <option key={p.name} value={p.name}>{p.name}</option>
          ))}
        </select>
        {value && model && (
          <span style={{ fontSize: "0.85em", color: "#666", whiteSpace: "nowrap" }}>모델: {model}</span>
        )}
        <button
          className="settings-btn-sm"
          onClick={handleCheck}
          disabled={checking || !value}
        >
          {checking ? "확인 중..." : "연결 확인"}
        </button>
        {connectionStatus === "ok" && <span className="conn-ok">✅ 연결됨</span>}
        {connectionStatus === "error" && <span className="conn-error">❌ 실패</span>}
      </div>
    </div>
  )
}

function RerankerSection() {
  const [profiles, setProfiles] = useState([])
  const [rerankerLLM, setRerankerLLM] = useState("")
  const [fallbackLLM, setFallbackLLM] = useState("")
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const { error, setError, saveMsg, showSaveMsg } = useAsyncOperation()

  useEffect(() => {
    Promise.all([getLLMProfiles(), getRerankerConfig()])
      .then(([data, cfg]) => {
        setProfiles(data.profiles || [])
        setRerankerLLM(cfg.reranker_llm || "")
        setFallbackLLM(cfg.reranker_fallback_llm || "")
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  async function handleSave() {
    setSaving(true)
    try {
      await saveRerankerConfig({ reranker_llm: rerankerLLM, reranker_fallback_llm: fallbackLLM })
      showSaveMsg("저장되었습니다")
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div className="settings-section">Reranker 설정 불러오는 중...</div>

  return (
    <div className="settings-section">
      <div className="settings-section-title">Reranker LLM 설정</div>
      <div style={{ fontSize: "0.85em", color: "#666", marginBottom: "0.5rem" }}>
        KB 검색 재순위(Stage 2B Reranker)에 사용할 LLM 프로필입니다. 비우면 LLM 설정의 활성 프로필을 사용합니다.
      </div>
      {error && <div className="settings-error">{error}</div>}

      <RerankerProfileRow
        label="Reranker LLM"
        value={rerankerLLM}
        onChange={setRerankerLLM}
        profiles={profiles}
        emptyLabel="(활성 LLM 사용 — 기본)"
      />
      <RerankerProfileRow
        label="Fallback LLM"
        value={fallbackLLM}
        onChange={setFallbackLLM}
        profiles={profiles}
        emptyLabel="(사용 안 함)"
      />

      <div className="settings-actions">
        {saveMsg && <span className="settings-save-msg">{saveMsg}</span>}
        <button className="settings-save-btn" onClick={handleSave} disabled={saving}>
          {saving ? "저장 중..." : "저장"}
        </button>
      </div>
    </div>
  )
}

const CONTEXT_STRATEGY_LABELS = {
  truncation:      "우선순위 Truncation",
  split:           "분할 전송",
  hybrid:          "혼합 (Hybrid)",
  summarize_split: "요약 후 단일 전송 (Summarize Split)",
}

const UNKNOWN_REFINE_LABELS = {
  current:      "현행 (선택 경로 그대로)",
  all_profiles: "전체 프로파일 필터 합집합",
}

function PipelineSection() {
  const [config, setConfig] = useState(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [querying, setQuerying] = useState(false)
  const [numCtxResult, setNumCtxResult] = useState(null)
  const { error, setError, saveMsg, showSaveMsg } = useAsyncOperation()

  useEffect(() => {
    setLoading(true)
    getPipelineConfig()
      .then(setConfig)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  async function handleSave() {
    setSaving(true)
    try {
      await savePipelineConfig(config)
      showSaveMsg("저장되었습니다")
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  async function handleQueryNumCtx() {
    setQuerying(true)
    setNumCtxResult(null)
    setError(null)
    try {
      const data = await queryNumCtx()
      setNumCtxResult(data)
      if (data.num_ctx) {
        set("num_ctx", data.num_ctx)
        if (data.suggested_max_log_lines) set("max_log_lines", data.suggested_max_log_lines)
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setQuerying(false)
    }
  }

  if (loading) return <div className="settings-section">파이프라인 설정 불러오는 중...</div>
  if (!config) return null

  const set = (key, val) => setConfig(c => ({ ...c, [key]: val }))

  return (
    <div className="settings-section">
      <div className="settings-section-title">파이프라인 임계값</div>
      {error && <div className="settings-error">{error}</div>}

      <div className="settings-row">
        <label className="settings-label">KB 관련성 임계값</label>
        <input type="range" className="settings-input" min="0" max="1" step="0.05"
          value={config.kb_threshold}
          onChange={e => set("kb_threshold", parseFloat(e.target.value))} />
        <span style={{ minWidth: "2.5rem", textAlign: "right" }}>{config.kb_threshold.toFixed(2)}</span>
      </div>

      <div className="settings-row">
        <label className="settings-label">확정 진단 임계값</label>
        <input type="range" className="settings-input" min="0" max="1" step="0.05"
          value={config.definite_threshold}
          onChange={e => set("definite_threshold", parseFloat(e.target.value))} />
        <span style={{ minWidth: "2.5rem", textAlign: "right" }}>{config.definite_threshold.toFixed(2)}</span>
      </div>

      <div className="settings-row">
        <label className="settings-label">최대 로그 라인 수</label>
        <input type="number" className="settings-input" min="10" max="2000"
          value={config.max_log_lines}
          onChange={e => set("max_log_lines", parseInt(e.target.value))} />
      </div>

      <div className="settings-row">
        <label className="settings-label">신규 문제 로그 정제</label>
        <select className="settings-input"
          value={config.unknown_refine_mode ?? "current"}
          onChange={e => set("unknown_refine_mode", e.target.value)}>
          {Object.entries(UNKNOWN_REFINE_LABELS).map(([val, label]) => (
            <option key={val} value={val}>{label}</option>
          ))}
        </select>
      </div>

      <div className="settings-row">
        <label className="settings-label">컨텍스트 전략</label>
        <select className="settings-input"
          value={config.context_strategy}
          onChange={e => set("context_strategy", e.target.value)}>
          {Object.entries(CONTEXT_STRATEGY_LABELS).map(([val, label]) => (
            <option key={val} value={val}>{label}</option>
          ))}
        </select>
      </div>

      {(config.context_strategy === "hybrid") && (
        <div className="settings-row">
          <label className="settings-label">혼합 전환 임계값</label>
          <input type="range" className="settings-input" min="0" max="1" step="0.05"
            value={config.hybrid_overflow_ratio}
            onChange={e => set("hybrid_overflow_ratio", parseFloat(e.target.value))} />
          <span style={{ minWidth: "2.5rem", textAlign: "right" }}>{config.hybrid_overflow_ratio.toFixed(2)}</span>
        </div>
      )}

      <div className="settings-row">
        <label className="settings-label">num_ctx (토큰)</label>
        <input type="number" className="settings-input" min="0" max="1000000" step="1024"
          value={config.num_ctx ?? 0}
          onChange={e => set("num_ctx", parseInt(e.target.value) || null)} />
        <button className="settings-btn-sm" onClick={handleQueryNumCtx} disabled={querying}>
          {querying ? "조회 중..." : "num_ctx 조회"}
        </button>
      </div>
      {numCtxResult && (
        <div className="settings-row" style={{ fontSize: "0.85em", color: "#666" }}>
          {numCtxResult.num_ctx
            ? <>
                <span><b>{numCtxResult.model}</b> — {numCtxResult.num_ctx.toLocaleString()} 토큰 ({numCtxResult.source})</span>
                {numCtxResult.suggested_max_log_lines && <span style={{ marginLeft: "1rem" }}>권장 최대 로그 라인: <b>{numCtxResult.suggested_max_log_lines}</b>줄 (자동 반영됨)</span>}
              </>
            : <span style={{ color: "#c00" }}>num_ctx를 확인할 수 없습니다. 직접 입력해 주세요.</span>
          }
        </div>
      )}

      <div className="settings-row">
        <label className="settings-label">Stage 6 Reflection</label>
        <input type="checkbox"
          checked={config.stage6_reflection_enabled}
          onChange={e => set("stage6_reflection_enabled", e.target.checked)} />
      </div>

      <div className="settings-row">
        <label className="settings-label">Observability</label>
        <input type="checkbox"
          checked={config.observability_enabled}
          onChange={e => set("observability_enabled", e.target.checked)} />
      </div>

      <div className="settings-actions">
        {saveMsg && <span className="settings-save-msg">{saveMsg}</span>}
        <button className="settings-save-btn" onClick={handleSave} disabled={saving}>
          {saving ? "저장 중..." : "저장"}
        </button>
      </div>
    </div>
  )
}

function ServerSection() {
  const [config, setConfig] = useState(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const { error, setError, saveMsg, showSaveMsg } = useAsyncOperation()

  useEffect(() => {
    setLoading(true)
    getServerConfig()
      .then(setConfig)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  async function handleSave() {
    setSaving(true)
    try {
      await saveServerConfig(config)
      showSaveMsg("저장되었습니다 (다음 서버 재시작 시 적용)", 3000)
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div className="settings-section">서버 설정 불러오는 중...</div>
  if (!config) return null

  const set = (key, val) => setConfig(c => ({ ...c, [key]: val }))

  return (
    <div className="settings-section">
      <div className="settings-section-title">서버 구동 설정</div>
      <div style={{ fontSize: "0.85em", color: "#666", marginBottom: "0.5rem" }}>
        서버 재시작 후 적용됩니다.
      </div>
      {error && <div className="settings-error">{error}</div>}

      <div className="settings-row">
        <label className="settings-label">동시 실행 Worker 수</label>
        <input type="number" className="settings-input" min="1" max="50"
          value={config.max_workers ?? 10}
          onChange={e => set("max_workers", parseInt(e.target.value) || 1)} />
      </div>

      <div className="settings-actions">
        {saveMsg && <span className="settings-save-msg">{saveMsg}</span>}
        <button className="settings-save-btn" onClick={handleSave} disabled={saving}>
          {saving ? "저장 중..." : "저장"}
        </button>
      </div>
    </div>
  )
}

function SystemGuidelinesSection() {
  const [value, setValue] = useState("")
  const [defaultValue, setDefaultValue] = useState("")
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const { error, setError, saveMsg, showSaveMsg } = useAsyncOperation()

  useEffect(() => {
    setLoading(true)
    getGuidelines()
      .then(data => { setValue(data.value); setDefaultValue(data.default) })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  async function handleSave(text) {
    setSaving(true)
    try {
      await saveGuidelines(text)
      setValue(text)
      showSaveMsg("저장되었습니다")
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div className="settings-section">시스템 분석 지침 불러오는 중...</div>

  return (
    <div className="settings-section">
      <div className="settings-section-title">시스템 분석 지침</div>
      <div style={{ fontSize: "0.85em", color: "#666", marginBottom: "0.5rem" }}>
        모든 LLM 프롬프트 최상단에 주입되는 전역 분석 제약 조건입니다. (Stage 2B Reranker · Stage 5 리포트 생성 적용)
      </div>
      {error && <div className="settings-error">{error}</div>}
      <textarea
        style={{ width: "100%", minHeight: "120px", fontFamily: "monospace", fontSize: "0.9em", boxSizing: "border-box" }}
        value={value}
        onChange={e => setValue(e.target.value)}
      />
      <div className="settings-actions">
        {saveMsg && <span className="settings-save-msg">{saveMsg}</span>}
        <button className="settings-btn-sm" onClick={() => handleSave(defaultValue)} disabled={saving}>
          기본값으로 초기화
        </button>
        <button className="settings-save-btn" onClick={() => handleSave(value)} disabled={saving}>
          {saving ? "저장 중..." : "저장"}
        </button>
      </div>
    </div>
  )
}

export default function SettingsPage() {
  const navigate = useNavigate()
  return (
    <div className="settings-page">
      <div className="settings-header">
        <button className="settings-back-btn" onClick={() => navigate(-1)}>← 돌아가기</button>
        <span className="settings-header-title">Analyzing Assistant 설정</span>
      </div>
      <div className="settings-body">
        <ModelSection
          title="LLM 설정"
          getProfiles={getLLMProfiles}
          getModels={getLLMModels}
          checkConnection={checkLLMConnection}
          getConfig={getLLMConfig}
          saveConfig={saveLLMConfig}
          activeKey="active_llm"
        />
        <RerankerSection />
        <ModelSection
          title="Embedding 설정"
          getProfiles={getEmbeddingProfiles}
          getModels={getEmbeddingModels}
          checkConnection={checkEmbeddingConnection}
          getConfig={getEmbeddingConfig}
          saveConfig={saveEmbeddingConfig}
          activeKey="active_embed"
          hideAdvanced
        />
        <PipelineSection />
        <ServerSection />
        <SystemGuidelinesSection />
      </div>
    </div>
  )
}
