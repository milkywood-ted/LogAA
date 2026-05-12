"""
URL 파라미터 조합 모듈
config.yaml의 parameters 배열과 UI 입력값을 병합하여 최종 URL을 생성합니다.

parameters 형식:
  - "ui이름:param"         → UI 입력값 사용 (값 없음)
  - "ui이름:param=value"   → 고정값 사용
  - "param"               → UI 입력값 사용 (하위 호환)
  - "param=value"         → 고정값 사용 (하위 호환)
"""

from urllib.parse import urlparse, urlencode, urlunparse


def _parse_param(param) -> tuple[str, str, str]:
    """
    파라미터를 파싱합니다.

    지원 형식:
      - {"ui이름": "param"}          → UI 입력값
      - {"ui이름": "param=value"}    → 고정값
      - "ui이름:param"               → UI 입력값
      - "ui이름:param=value"         → 고정값
      - "param"                      → UI 입력값 (하위 호환)
      - "param=value"                → 고정값 (하위 호환)

    Returns:
        (ui_name, param_key, param_value)
    """
    # 딕셔너리 형식: {"ui이름": "param"} 또는 {"ui이름": "param=value"}
    if isinstance(param, dict):
        ui_name  = list(param.keys())[0].strip()
        rest     = list(param.values())[0].strip()
        if "=" in rest:
            param_key, param_value = rest.split("=", 1)
        else:
            param_key, param_value = rest, ""
        return ui_name, param_key.strip(), param_value.strip()

    # 문자열 형식
    param = str(param)

    # "ui이름:param" 또는 "ui이름:param=value" 형식
    if ":" in param:
        ui_name, rest = param.split(":", 1)
        ui_name = ui_name.strip()
        if "=" in rest:
            param_key, param_value = rest.split("=", 1)
        else:
            param_key, param_value = rest, ""
        return ui_name.strip(), param_key.strip(), param_value.strip()

    # 하위 호환: "param" 또는 "param=value" 형식
    if "=" in param:
        param_key, param_value = param.split("=", 1)
        return param_key.strip(), param_key.strip(), param_value.strip()
    else:
        return param.strip(), param.strip(), ""


def build_url(base_url: str, parameters: list, param_values: dict = None) -> str:
    """
    base_url에 parameters를 병합하여 최종 URL을 생성합니다.

    Args:
        base_url: 기본 URL
        parameters: 파라미터 배열
        param_values: UI에서 입력받은 값 (키는 ui_name)

    Returns:
        str: 최종 URL
    """
    param_values = param_values or {}
    parsed       = urlparse(base_url)
    params       = {}

    for param in (parameters or []):
        ui_name, param_key, param_value = _parse_param(param)

        if param_value:
            # 고정값
            params[param_key] = param_value
        elif ui_name in param_values and param_values[ui_name]:
            # UI 입력값 (ui_name으로 조회)
            params[param_key] = param_values[ui_name]

    if not params:
        return base_url

    query_string = urlencode(params)
    return urlunparse(parsed._replace(query=query_string))


def get_input_params(parameters: list) -> list[tuple[str, str]]:
    """
    UI에서 입력받아야 할 파라미터 목록을 반환합니다.
    고정값이 없는 파라미터만 반환합니다.

    Returns:
        list of (ui_name, param_key)
    """
    input_params = []
    for param in (parameters or []):
        ui_name, param_key, param_value = _parse_param(param)
        if not param_value:
            input_params.append((ui_name, param_key))
    return input_params