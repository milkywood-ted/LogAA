export default function CredentialInput({ credentials, onChange }) {
    return (
        <div className="credential-input">
            <label className="input-label">ID</label>
            <input
                className="text-input"
                type="text"
                placeholder="로그인 ID"
                value={credentials.id}
                onChange={e => onChange('id', e.target.value)}
            />
            <label className="input-label">PW</label>
            <input
                className="text-input"
                type="password"
                placeholder="로그인 PW"
                value={credentials.pw}
                onChange={e => onChange('pw', e.target.value)}
            />
        </div>
    );
}
