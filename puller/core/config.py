import yaml
from pathlib import Path


def load_config(path: str = None) -> dict:
    """
    config.yaml을 로드합니다.
    path를 지정하지 않으면 패키지 내 기본 config.yaml을 사용합니다.
    """
    config_path = Path(path) if path else Path(__file__).parent.parent / "config" / "config.yaml"

    if not config_path.exists():
        raise FileNotFoundError(f"설정 파일을 찾을 수 없습니다: {config_path}")

    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)