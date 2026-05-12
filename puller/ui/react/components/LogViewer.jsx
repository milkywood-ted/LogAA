/**
 * 실시간 로그 뷰어 컴포넌트
 */

import { useEffect, useRef } from 'react';

export default function LogViewer({ logs }) {
    const bottomRef = useRef(null);

    // 새 로그 추가 시 자동 스크롤
    useEffect(() => {
        bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [logs]);

    return (
        <div className="log-viewer">
            <div className="log-header">
                <span>실행 로그</span>
                <span className="log-count">{logs.length}줄</span>
            </div>
            <div className="log-body">
                {logs.length === 0
                    ? <span className="log-empty">로그가 없습니다.</span>
                    : logs.map((log, i) => (
                        <div key={i} className={`log-line ${getLogClass(log)}`}>
                            {log}
                        </div>
                    ))
                }
                <div ref={bottomRef} />
            </div>
        </div>
    );
}

function getLogClass(log) {
    if (log.includes('✅')) return 'log-success';
    if (log.includes('❌')) return 'log-error';
    if (log.includes('⚠️')) return 'log-warn';
    if (log.includes('🎯')) return 'log-highlight';
    return 'log-default';
}