/**
 * 파라미터 입력 컴포넌트
 * input_params가 있는 경우에만 렌더링됩니다.
 */

export default function ParamInput({ inputParams, paramValues, onChange }) {
    if (!inputParams || inputParams.length === 0) return null;

    return (
        <div className="form-group">
            <label>파라미터 입력</label>
            {inputParams.map(([uiName, paramKey]) => (
                <div key={paramKey} className="param-row">
                    <label>{uiName}</label>
                    <input
                        type="text"
                        value={paramValues[uiName] || ''}
                        onChange={e => onChange(uiName, e.target.value)}
                        placeholder={uiName}
                    />
                </div>
            ))}
        </div>
    );
}
