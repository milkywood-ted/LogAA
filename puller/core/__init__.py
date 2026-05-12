from .downloader import WebDownloader
from .config import load_config
from .url_builder import build_url, get_input_params
from .result import (
    DownloadResult, FileResult,
    InspectResult,
    ScanResult,
    ReadTableResult, TableData,
    ReadTextResult,
    FinalResult,
)

__all__ = [
    "WebDownloader",
    "load_config",
    "build_url", "get_input_params",
    "DownloadResult", "FileResult",
    "InspectResult",
    "ScanResult",
    "ReadTableResult", "TableData",
    "ReadTextResult",
    "FinalResult",
]