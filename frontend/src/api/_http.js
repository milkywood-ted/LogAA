// 프로덕션 빌드: VITE_API_URL 미설정 → 상대 경로("") — backend(:8800)가 dist를
// 서빙하므로 같은 출처로 호출한다 (환경별 재빌드 불필요, §9-8 해소).
// 개발(npm run dev): .env.development의 VITE_API_URL로 backend를 직접 호출.
const BASE_URL = import.meta.env.VITE_API_URL ?? ""

export async function _request(method, path, body) {
  const options = { method, headers: {} }
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json"
    options.body = JSON.stringify(body)
  }
  const res = await fetch(`${BASE_URL}${path}`, options)
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || `${method} ${path} 실패`)
  }
  return res.json()
}

export function _sseUrl(path) {
  return `${BASE_URL}${path}`
}
