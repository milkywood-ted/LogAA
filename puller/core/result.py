"""
Result 클래스 모듈

Core의 실행 결과를 담는 객체들입니다.
UI는 이 객체를 받아서 표시만 합니다.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from abc import ABC


# =============================================================================
# Base
# =============================================================================

class Result(ABC):
    """모든 Result의 추상 기반 클래스"""
    pass


# =============================================================================
# Download
# =============================================================================

@dataclass
class FileResult:
    """개별 파일 다운로드 결과"""
    filename: str
    path: str | None = None
    status: str = "failed"   # "success" | "failed"
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.status == "success"


@dataclass
class DownloadResult(Result):
    """전체 다운로드 실행 결과"""
    success: bool = False
    files: list[FileResult] = field(default_factory=list)
    comment_attachment_items: list[dict] = field(default_factory=list)
    error: str | None = None

    @property
    def total(self) -> int:
        return len(self.files)

    @property
    def success_count(self) -> int:
        return sum(1 for f in self.files if f.success)

    @property
    def failed_count(self) -> int:
        return self.total - self.success_count


# =============================================================================
# Inspect
# =============================================================================

@dataclass
class InspectResult(Result):
    """페이지 탐색 결과"""
    success: bool = False
    title: str = ""
    current_url: str = ""
    selectors: list[str] = field(default_factory=list)
    error: str | None = None


# =============================================================================
# Scan
# =============================================================================

@dataclass
class ScanResult(Result):
    """셀렉터 스캔 결과"""
    success: bool = False
    title: str = ""
    current_url: str = ""
    inputs: list[dict] = field(default_factory=list)
    buttons: list[dict] = field(default_factory=list)
    links: list[dict] = field(default_factory=list)
    tables: list[dict] = field(default_factory=list)
    clickables: list[dict] = field(default_factory=list)
    error: str | None = None


# =============================================================================
# Read (TableData / ReadTextResult)
# =============================================================================

@dataclass
class TableData:
    """테이블 데이터"""
    selector: str
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        return len(self.rows)


@dataclass
class ReadTextResult(Result):
    """텍스트 읽기 결과"""
    success: bool = False
    title: str = ""
    current_url: str = ""
    texts: dict[str, str] = field(default_factory=dict)
    error: str | None = None


# =============================================================================
# Final (통합 결과)
# =============================================================================

@dataclass
class FinalResult(Result):
    """
    전체 interaction 실행 후 final step의 모든 결과를 수집합니다.
    download, read_text, read_table 결과를 모두 포함합니다.
    """
    success: bool = False
    title: str = ""
    current_url: str = ""
    files: list[FileResult] = field(default_factory=list)
    texts: dict[str, str] = field(default_factory=dict)
    tables: list[TableData] = field(default_factory=list)
    comment_attachment_items: list[dict] = field(default_factory=list)
    error: str | None = None

    @property
    def total_files(self) -> int:
        return len(self.files)

    @property
    def success_files(self) -> int:
        return sum(1 for f in self.files if f.success)


# =============================================================================
# ReadTable
# =============================================================================

@dataclass
class ReadTableResult(Result):
    """테이블 읽기 결과"""
    success: bool = False
    title: str = ""
    current_url: str = ""
    tables: list[TableData] = field(default_factory=list)
    error: str | None = None
