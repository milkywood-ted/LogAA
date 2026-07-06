/**
 * 사이트 선택 컴포넌트
 */

export default function SiteSelector({ sites, selected, onChange }) {
    return (
        <div className="form-group">
            <label>사이트 선택</label>
            <select value={selected} onChange={e => onChange(e.target.value)}>
                {sites.map(s => (
                    <option key={s.name} value={s.name}>{s.name}</option>
                ))}
            </select>
        </div>
    );
}
