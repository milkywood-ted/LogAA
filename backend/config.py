import yaml
from pathlib import Path


class Config:
    _PATH = Path(__file__).parent / "config.yaml"

    def __init__(self):
        self._config: dict = self._load()
        self._workspace: Path = self._resolve_workspace()

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

    def workspace(self) -> Path:
        return self._workspace

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
