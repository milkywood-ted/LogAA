const BASE_URL = import.meta.env.VITE_API_URL

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
