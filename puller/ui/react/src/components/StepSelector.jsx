/**
 * interaction 단계 선택 컴포넌트
 */

export default function StepSelector({ steps, selected, onChange }) {
    if (!steps || steps.length === 0) return null;

    return (
        <div className="form-group">
            <label>중단할 단계</label>
            <select value={selected} onChange={e => onChange(e.target.value)}>
                <option value="">전체 실행</option>
                {steps.map((s, i) => (
                    <option key={s.name} value={s.name}>
                        {i + 1}. {s.name}{s.final ? ' (final)' : ''}
                    </option>
                ))}
            </select>
            <div className="step-list">
                {steps.map((s, i) => {
                    const selectedIdx = steps.findIndex(st => st.name === selected);
                    const active = selectedIdx === -1 || i <= selectedIdx;
                    return (
                        <span key={s.name} className={`step-badge ${active ? 'active' : 'inactive'}`}>
                            {i + 1}. {s.name}
                        </span>
                    );
                })}
            </div>
        </div>
    );
}
