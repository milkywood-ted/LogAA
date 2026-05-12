const BASE_URL = "http://localhost:8000"

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
