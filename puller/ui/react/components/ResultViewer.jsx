/**
 * 결과 뷰어 컴포넌트
 * scan, inspect, read_table, read_text, download, final 결과를 표시합니다.
 */

export default function ResultViewer({ type, result }) {
    if (!result) return null;

    if (!result.success) {
        return (
            <div className="result-box error">
                <span>❌ 실패: {result.error}</span>
            </div>
        );
    }

    return (
        <div className="result-box success">
            <div className="result-meta">
                <span>✅ 성공</span>
                {result.data?.title && <span>📄 {result.data.title}</span>}
                {result.data?.current_url && (
                    <span className="url">{result.data.current_url}</span>
                )}
            </div>
            {renderData(type, result.data)}
        </div>
    );
}

function renderData(type, data) {
    if (!data) return null;

    switch (type) {
        case 'scan':    return <ScanResult data={data} />;
        case 'inspect': return <InspectResult data={data} />;
        case 'read_table': return <TableResult tables={data.tables} />;
        case 'read_text':  return <TextResult texts={data.texts} />;
        case 'download':   return <DownloadResult data={data} />;
        case 'final':      return <FinalResultView data={data} />;
        default: return null;
    }
}

// =============================================================================
// 개별 결과 컴포넌트
// =============================================================================

function ScanResult({ data }) {
    return (
        <div className="scan-result">
            {data.inputs?.length > 0 && (
                <Section title="📝 Input 요소">
                    <Table
                        headers={['셀렉터', 'type', 'name', 'label', 'frame']}
                        rows={data.inputs.map(i => [i.selector, i.type, i.name, i.label || '', i.frame_name || 'main'])}
                    />
                </Section>
            )}
            {data.buttons?.length > 0 && (
                <Section title="🔘 버튼">
                    <Table
                        headers={['셀렉터', 'type', '텍스트', 'frame']}
                        rows={data.buttons.map(b => [b.selector, b.type, b.text, b.frame_name || 'main'])}
                    />
                </Section>
            )}
            {data.links?.length > 0 && (
                <Section title="🔗 다운로드 링크">
                    <Table
                        headers={['셀렉터', '텍스트', 'href', 'frame']}
                        rows={data.links.map(l => [l.selector, l.text, l.href, l.frame_name || 'main'])}
                    />
                </Section>
            )}
            {data.tables?.length > 0 && (
                <Section title="📊 data-table">
                    <Table
                        headers={['셀렉터', 'data-table', '행 수', 'frame']}
                        rows={data.tables.map(t => [t.selector, t.data_table, t.row_count, t.frame_name || 'main'])}
                    />
                </Section>
            )}
            {data.clickables?.length > 0 && (
                <Section title="👆 클릭 가능">
                    <Table
                        headers={['셀렉터', 'tag', '텍스트', 'frame']}
                        rows={data.clickables.map(c => [c.selector, c.tag, c.text, c.frame_name || 'main'])}
                    />
                </Section>
            )}
        </div>
    );
}

function InspectResult({ data }) {
    return <div className="inspect-result">탐색 완료</div>;
}

function TableResult({ tables }) {
    if (!tables || tables.length === 0) return <div>테이블 결과 없음</div>;
    return (
        <div>
            {tables.map((t, i) => (
                <Section key={i} title={`📊 ${t.selector}`}>
                    <Table headers={t.headers} rows={t.rows} />
                </Section>
            ))}
        </div>
    );
}

function TextResult({ texts }) {
    if (!texts || Object.keys(texts).length === 0) return <div>텍스트 결과 없음</div>;
    return (
        <div>
            {Object.entries(texts).map(([selector, text]) => (
                <Section key={selector} title={`📝 ${selector}`}>
                    <pre className="text-result">{text}</pre>
                </Section>
            ))}
        </div>
    );
}

function DownloadResult({ data }) {
    return (
        <div>
            <div className="download-summary">
                총 {data.total}개 / 성공 {data.success_count}개 / 실패 {data.failed_count}개
            </div>
            {data.files?.length > 0 && (
                <Table
                    headers={['번호', '파일명', '상태', '경로']}
                    rows={data.files.map((f, i) => [
                        i + 1,
                        f.filename,
                        f.status === 'success' ? '✅ 성공' : '❌ 실패',
                        f.path || '-'
                    ])}
                />
            )}
        </div>
    );
}

function FinalResultView({ data }) {
    return (
        <div>
            {data.files?.length > 0 && (
                <Section title="📁 다운로드 결과">
                    <DownloadResult data={{ ...data, total: data.files.length, success_count: data.files.filter(f => f.status === 'success').length, failed_count: data.files.filter(f => f.status !== 'success').length }} />
                </Section>
            )}
            {data.texts && Object.keys(data.texts).length > 0 && (
                <Section title="📝 텍스트 결과">
                    <TextResult texts={data.texts} />
                </Section>
            )}
            {data.tables?.length > 0 && (
                <Section title="📊 테이블 결과">
                    <TableResult tables={data.tables} />
                </Section>
            )}
        </div>
    );
}

// =============================================================================
// 공통 컴포넌트
// =============================================================================

function Section({ title, children }) {
    return (
        <div className="section">
            <div className="section-title">{title}</div>
            {children}
        </div>
    );
}

function Table({ headers, rows }) {
    if (!rows || rows.length === 0) return <div className="empty">데이터 없음</div>;
    return (
        <div className="table-wrapper">
            <table>
                {headers?.length > 0 && (
                    <thead>
                        <tr>{headers.map((h, i) => <th key={i}>{h}</th>)}</tr>
                    </thead>
                )}
                <tbody>
                    {rows.map((row, i) => (
                        <tr key={i}>
                            {row.map((cell, j) => <td key={j}>{cell}</td>)}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}