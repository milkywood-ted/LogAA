"""
core/log_loader.py

파일 목록 또는 폴더에서 로그 파일을 로드한다.

  - 파일 확장자 제약 없음
  - 인코딩 자동 감지 (chardet) — 낮은 신뢰도면 UTF-8 fallback
  - 폴더 탐색 깊이: recursive 옵션으로 선택
  - 파일/폴더 혼합 목록 지원

사용 예:
    files = load_inputs(["/var/log", "/tmp/kern.log"], recursive=True)
    # → {"/var/log/syslog": "...", "/tmp/kern.log": "...", ...}
"""

import os
from pathlib import Path
import chardet

# 바이너리 판별용 샘플 크기
_BINARY_SNIFF_BYTES = 8192

# 인코딩 감지 신뢰도 기본값
_DEFAULT_CONFIDENCE = 0.70

# 스트리밍 전환 임계값 (bytes)
# 환경 변수 LOG_LOADER_STREAM_THRESHOLD_MB 로 재설정 가능
_DEFAULT_STREAM_THRESHOLD_MB = 50
_STREAM_THRESHOLD: int = (
    int(os.environ.get("LOG_LOADER_STREAM_THRESHOLD_MB", str(_DEFAULT_STREAM_THRESHOLD_MB)))
    * 1024 * 1024
)

# 스트리밍 시 인코딩 감지용 샘플 크기 (64 KB)
_ENCODING_SAMPLE_BYTES = 65536


# ── public API ────────────────────────────────────────────────────────────────

def load_inputs(
    inputs: list[str | Path],
    recursive: bool = True,
    encoding_confidence: float = _DEFAULT_CONFIDENCE,
) -> dict[str, str]:
    """
    파일 경로와 폴더 경로가 섞인 목록을 받아 로그 내용을 반환한다.

    Parameters
    ----------
    inputs              : 파일 또는 폴더 경로 목록
    recursive           : 폴더 탐색 시 하위 폴더까지 재귀 탐색 여부
    encoding_confidence : chardet 신뢰도 임계값 (미달 시 UTF-8 fallback)

    Returns
    -------
    {절대경로 문자열: 파일 내용} 딕셔너리
    읽기 실패 / 바이너리 파일은 조용히 건너뜀
    """
    result: dict[str, str] = {}
    for inp in inputs:
        path = Path(inp).resolve()
        if path.is_file():
            content = _read_file(path, encoding_confidence)
            if content is not None:
                result[str(path)] = content
        elif path.is_dir():
            result.update(
                load_folder(path, recursive=recursive,
                            encoding_confidence=encoding_confidence)
            )
    return result


def load_files(
    paths: list[str | Path],
    encoding_confidence: float = _DEFAULT_CONFIDENCE,
) -> dict[str, str]:
    """
    파일 경로 목록만 받아 로드한다. (폴더 무시)

    Parameters
    ----------
    paths               : 파일 경로 목록
    encoding_confidence : chardet 신뢰도 임계값

    Returns
    -------
    {절대경로 문자열: 파일 내용} 딕셔너리
    """
    result: dict[str, str] = {}
    for p in paths:
        path = Path(p).resolve()
        if not path.is_file():
            continue
        content = _read_file(path, encoding_confidence)
        if content is not None:
            result[str(path)] = content
    return result


def load_folder(
    folder: str | Path,
    recursive: bool = True,
    encoding_confidence: float = _DEFAULT_CONFIDENCE,
) -> dict[str, str]:
    """
    폴더에서 파일을 읽어 반환한다.

    Parameters
    ----------
    folder              : 탐색할 폴더 경로
    recursive           : True 이면 모든 하위 폴더 포함, False 이면 직속 파일만
    encoding_confidence : chardet 신뢰도 임계값

    Returns
    -------
    {절대경로 문자열: 파일 내용} 딕셔너리

    Raises
    ------
    NotADirectoryError : folder 가 디렉토리가 아닐 때
    """
    folder = Path(folder).resolve()
    if not folder.is_dir():
        raise NotADirectoryError(f"폴더가 아닙니다: {folder}")

    pattern = "**/*" if recursive else "*"
    result: dict[str, str] = {}
    for path in sorted(folder.glob(pattern)):
        if not path.is_file():
            continue
        content = _read_file(path, encoding_confidence)
        if content is not None:
            result[str(path)] = content
    return result


# ── 내부 유틸리티 ──────────────────────────────────────────────────────────────

def _read_file(path: Path, confidence: float) -> str | None:
    """
    파일 크기에 따라 읽기 방식을 자동 선택한다.

      ≤ _STREAM_THRESHOLD : 전체 읽기 (bulk)   — read_bytes() 후 decode
      > _STREAM_THRESHOLD : 라인 단위 읽기 (streaming) — 앞부분 샘플로 인코딩 감지 후
                            open() 으로 순회

    반환 None 조건:
      - 읽기 권한 없음 / OS 오류
      - 빈 파일
      - 바이너리 파일 (null byte 감지)
    """
    try:
        size = path.stat().st_size
    except OSError:
        return None

    if size == 0:
        return None

    if size <= _STREAM_THRESHOLD:
        return _read_bulk(path, confidence)
    else:
        return _read_streaming(path, confidence)


def _read_bulk(path: Path, confidence: float) -> str | None:
    """파일 전체를 한 번에 읽어 디코딩한다."""
    try:
        raw: bytes = path.read_bytes()
    except (PermissionError, OSError):
        return None

    if b"\x00" in raw[:_BINARY_SNIFF_BYTES]:
        return None

    return _decode_bytes(raw, confidence)


def _read_streaming(path: Path, confidence: float) -> str | None:
    """
    파일 앞부분(_ENCODING_SAMPLE_BYTES)만 읽어 인코딩을 감지한 뒤
    라인 단위로 순회하여 전체 내용을 반환한다.

    peak 메모리 = 결과 문자열 크기 (raw bytes 버퍼 없음).
    """
    try:
        with open(path, "rb") as f:
            sample = f.read(_ENCODING_SAMPLE_BYTES)
    except (PermissionError, OSError):
        return None

    if not sample:
        return None

    if b"\x00" in sample[:_BINARY_SNIFF_BYTES]:
        return None

    enc = _detect_encoding(sample, confidence)

    try:
        with open(path, encoding=enc, errors="replace") as f:
            text = f.read()
        return text.encode("utf-8", errors="ignore").decode("utf-8")
    except (PermissionError, OSError, LookupError):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read()
        except (PermissionError, OSError):
            return None


def _detect_encoding(sample: bytes, confidence: float) -> str:
    """샘플 바이트로 인코딩을 감지한다. 신뢰도 미달 시 UTF-8 반환."""
    detected = chardet.detect(sample)
    enc: str = detected.get("encoding") or "utf-8"
    conf: float = detected.get("confidence") or 0.0
    return enc if conf >= confidence else "utf-8"


def _decode_bytes(raw: bytes, confidence: float) -> str:
    """raw bytes 전체를 감지된 인코딩으로 디코딩한다."""
    enc = _detect_encoding(raw, confidence)
    try:
        return raw.decode(enc, errors="replace").encode("utf-8", errors="ignore").decode("utf-8")
    except (LookupError, UnicodeDecodeError):
        return raw.decode("utf-8", errors="replace")
