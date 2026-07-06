import { useState, useEffect } from "react"
import { getAnalysisProfiles } from "../api"

export default function ProfileSelector({ selectedProfiles, onChange }) {
  const [profiles, setProfiles] = useState([])

  useEffect(() => {
    getAnalysisProfiles().then(setProfiles).catch(() => {})
  }, [])

  if (profiles.length === 0) return null

  function toggle(name) {
    onChange(
      selectedProfiles.includes(name)
        ? selectedProfiles.filter(n => n !== name)
        : [...selectedProfiles, name]
    )
  }

  const selectedKeywords = [
    ...new Set(
      profiles
        .filter(p => selectedProfiles.includes(p.name))
        .flatMap(p => p.prefilter_keywords)
    ),
  ]

  return (
    <div className="profile-selector">
      <div className="profile-tags">
        {profiles.map(p => (
          <button
            key={p.name}
            className={`profile-tag ${selectedProfiles.includes(p.name) ? "selected" : ""}`}
            onClick={() => toggle(p.name)}
            title={p.description || p.name}
          >
            {p.name}
          </button>
        ))}
      </div>
      {selectedKeywords.length > 0 && (
        <div className="profile-keywords">
          <span className="profile-keywords-label">사전정제 키워드</span>
          {selectedKeywords.map(kw => (
            <span key={kw} className="keyword-tag">{kw}</span>
          ))}
        </div>
      )}
    </div>
  )
}
