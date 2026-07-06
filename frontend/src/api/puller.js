import { _request } from "./_http"

export async function getPullers() {
  return _request("GET", "/api/pullers")
}

export async function getDefects() {
  return _request("GET", "/api/defects")
}

export async function fetchDefect(pullerName, defectId, credentials) {
  return _request("POST", "/api/defect/fetch", {
    puller_name: pullerName,
    defect_id: defectId,
    credentials,
  })
}
