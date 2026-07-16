import yaml
from pathlib import Path


class Config:
    _PATH = Path(__file__).parent / "config.yaml"

    def __init__(self):
        self._config: dict = self._load()
        self._workspace: Path = self._resolve_workspace()
        self._user_log_roots: list[Path] = self._resolve_user_log_roots()

    def _load(self) -> dict:
        if not self._PATH.exists():
            return {}
        with open(self._PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _resolve_workspace(self) -> Path:
        raw = self._config.get("workspace")
        if not raw:
            return (self._PATH.parent / "workspace").resolve()
        p = Path(raw)
        return p if p.is_absolute() else (self._PATH.parent / p).resolve()

    def _resolve_user_log_roots(self) -> list[Path]:
        """user_log_roots 항목을 절대 경로 목록으로 해석한다.

        '~'는 홈 디렉토리로 확장하고, 상대 경로는 backend 디렉토리 기준으로
        해석한다. 미설정/빈 목록이면 [] — user-logs 추가는 전부 차단된다.
        """
        raw = self._config.get("user_log_roots") or []
        roots: list[Path] = []
        for entry in raw:
            p = Path(str(entry)).expanduser()
            if not p.is_absolute():
                p = self._PATH.parent / p
            roots.append(p.resolve())
        return roots

    def workspace(self) -> Path:
        return self._workspace

    def user_log_roots(self) -> list[Path]:
        return self._user_log_roots

    def allowed_client_ips(self) -> list[str]:
        """접근을 허용할 클라이언트 IP/CIDR 목록. 비어 있으면 전체 허용(opt-in)."""
        raw = self._config.get("allowed_client_ips") or []
        return [str(entry) for entry in raw]

    def pullers(self) -> list:
        return self._config.get("pullers", [])

    def get_puller(self, name: str) -> dict:
        for p in self.pullers():
            if p["name"] == name:
                return p
        raise ValueError(f"Puller '{name}' 를 찾을 수 없습니다")

    def puller_client(self) -> dict:
        return self._config.get("puller_client", {})

    def analyzing_assistants(self) -> list:
        section = self._config.get("analyzing_assistants", {})
        return section.get("items", [])

    def get_active_analyzing_assistant(self) -> dict:
        section = self._config.get("analyzing_assistants", {})
        if not section:
            raise ValueError("config.yaml에 'analyzing_assistants' 설정이 없습니다")
        active_name = section.get("active")
        for item in section.get("items", []):
            if item["name"] == active_name:
                return item
        raise ValueError(f"Active analyzing assistant '{active_name}'를 찾을 수 없습니다")


config = Config()
