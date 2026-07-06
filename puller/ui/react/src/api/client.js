/**
 * API 통신 모듈
 * FastAPI 백엔드와 통신합니다.
 */

const BASE_URL = 'http://localhost:8000';
const WS_URL   = 'ws://localhost:8000/ws/logs';

// =============================================================================
// HTTP 요청
// =============================================================================

async function request(method, path, body = null) {
    const options = {
        method,
        headers: { 'Content-Type': 'application/json' },
    };
    if (body) {
        options.body = JSON.stringify(body);
    }

    const res  = await fetch(`${BASE_URL}${path}`, options);
    const data = await res.json();
    return data;
}

const get  = (path)        => request('GET',  path);
const post = (path, body)  => request('POST', path, body);

// =============================================================================
// API 함수
// =============================================================================

export const api = {
    /** 사이트 목록 및 설정 */
    getConfig: () => get('/api/config'),

    /** 셀렉터 스캔 */
    scan: (siteRequest) => post('/api/scan', siteRequest),

    /** 페이지 탐색 */
    inspect: (siteRequest) => post('/api/inspect', siteRequest),

    /** 테이블 읽기 */
    readTable: (siteRequest) => post('/api/read_table', siteRequest),

    /** 텍스트 읽기 */
    readText: (siteRequest) => post('/api/read_text', siteRequest),

    /** 다운로드 */
    download: (siteRequest) => post('/api/download', siteRequest),

    /** 통합 실행 */
    final: (siteRequest) => post('/api/final', siteRequest),

    /** 비동기 통합 실행 — job_id 반환 */
    startFinal: (siteRequest) => post('/api/final/start', siteRequest),

    /** Job 상태 폴링 */
    pollJob: (jobId) => get(`/api/job/${jobId}`),
};

// =============================================================================
// WebSocket - 실시간 로그
// =============================================================================

export class LogSocket {
    constructor(onMessage) {
        this.onMessage = onMessage;
        this.ws        = null;
    }

    connect() {
        this.ws            = new WebSocket(WS_URL);
        this.ws.onmessage  = (e) => this.onMessage(e.data);
        this.ws.onerror    = (e) => console.error('WebSocket error:', e);
        this.ws.onclose    = ()  => console.log('WebSocket closed');
    }

    disconnect() {
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
    }
}
