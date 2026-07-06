import { useState, useEffect, useCallback } from 'react';
import { api, LogSocket } from './api/client.js';
import SiteSelector from './components/SiteSelector.jsx';
import ParamInput from './components/ParamInput.jsx';
import StepSelector from './components/StepSelector.jsx';
import CredentialInput from './components/CredentialInput.jsx';
import LogViewer from './components/LogViewer.jsx';
import ResultViewer from './components/ResultViewer.jsx';
import './App.css';

export default function App() {
    // 설정
    const [sites, setSites]               = useState([]);
    const [selectedSite, setSelectedSite] = useState('');
    const [siteInfo, setSiteInfo]         = useState(null);
    const [paramValues, setParamValues]   = useState({});
    const [untilStep, setUntilStep]       = useState('');
    const [credentials, setCredentials]   = useState({ id: '', pw: '' });

    // 실행 상태
    const [loading, setLoading]   = useState(false);
    const [activeTab, setActiveTab] = useState('scan');
    const [result, setResult]     = useState(null);
    const [logs, setLogs]         = useState([]);

    // WebSocket
    const [logSocket] = useState(() => new LogSocket((msg) => {
        setLogs(prev => [...prev, msg]);
    }));

    // 초기 로드
    useEffect(() => {
        api.getConfig().then(data => {
            setSites(data.sites);
            if (data.sites.length > 0) {
                setSelectedSite(data.sites[0].name);
            }
        });
        logSocket.connect();
        return () => logSocket.disconnect();
    }, []);

    // 사이트 변경 시
    useEffect(() => {
        const site = sites.find(s => s.name === selectedSite);
        setSiteInfo(site || null);
        setParamValues({});
        setUntilStep('');
        setResult(null);
    }, [selectedSite, sites]);

    // 파라미터 유효성 체크
    const paramsOk = !siteInfo?.input_params?.length ||
        siteInfo.input_params.every(([uiName]) => paramValues[uiName]);

    // 요청 객체 생성
    const makeRequest = () => ({
        site_name:       selectedSite,
        until_step_name: untilStep || null,
        param_values:    paramValues,
        credentials:     (credentials.id && credentials.pw) ? credentials : null,
    });

    // 실행 함수
    const run = useCallback(async (type, apiFn) => {
        setLoading(true);
        setActiveTab(type);
        setResult(null);
        setLogs([]);
        try {
            if (type === 'final') {
                const { job_id } = await api.startFinal(makeRequest());
                while (true) {
                    await new Promise(r => setTimeout(r, 3000));
                    const job = await api.pollJob(job_id);
                    if (job.status === 'done' || job.status === 'error') {
                        setResult(job);
                        break;
                    }
                }
            } else {
                const res = await apiFn(makeRequest());
                setResult(res);
            }
        } finally {
            setLoading(false);
        }
    }, [selectedSite, untilStep, paramValues]);

    const tabs = [
        { key: 'scan',       label: '① 셀렉터 스캔',   fn: api.scan },
        { key: 'inspect',    label: '② 페이지 탐색',   fn: api.inspect },
        { key: 'read_text',  label: '③ 텍스트 읽기',   fn: api.readText },
        { key: 'read_table', label: '④ 테이블 읽기',   fn: api.readTable },
        { key: 'download',   label: '⑤ 다운로드',      fn: api.download },
        ...(siteInfo?.has_individual_download ? [
            { key: 'final', label: '⑥ 통합 실행', fn: api.final },
        ] : []),
    ];

    return (
        <div className="app">
            {/* 사이드바 */}
            <aside className="sidebar">
                <h1 className="logo">🗂️ Puller</h1>

                <SiteSelector
                    sites={sites}
                    selected={selectedSite}
                    onChange={setSelectedSite}
                />

                <ParamInput
                    inputParams={siteInfo?.input_params}
                    paramValues={paramValues}
                    onChange={(uiName, value) =>
                        setParamValues(prev => ({ ...prev, [uiName]: value }))
                    }
                />

                <StepSelector
                    steps={siteInfo?.steps}
                    selected={untilStep}
                    onChange={setUntilStep}
                />

                <CredentialInput
                    credentials={credentials}
                    onChange={(key, value) =>
                        setCredentials(prev => ({ ...prev, [key]: value }))
                    }
                />

                {!paramsOk && (
                    <div className="warn-box">
                        ⚠️ 필수 파라미터를 입력해 주세요.
                    </div>
                )}
            </aside>

            {/* 메인 */}
            <main className="main">
                {/* 탭 버튼 */}
                <div className="tabs">
                    {tabs.map(tab => (
                        <button
                            key={tab.key}
                            className={`tab-btn ${activeTab === tab.key ? 'active' : ''}`}
                            disabled={loading || !paramsOk}
                            onClick={() => run(tab.key, tab.fn)}
                        >
                            {tab.label}
                        </button>
                    ))}
                </div>

                {/* 로딩 */}
                {loading && (
                    <div className="loading-bar">
                        <div className="loading-bar-inner" />
                    </div>
                )}

                {/* 결과 */}
                <div className="content">
                    <div className="result-panel">
                        <ResultViewer type={activeTab} result={result} />
                    </div>
                    <div className="log-panel">
                        <LogViewer logs={logs} />
                    </div>
                </div>
            </main>
        </div>
    );
}
