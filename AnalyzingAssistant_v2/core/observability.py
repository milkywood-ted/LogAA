"""
core/observability.py

Stage별 파이프라인 내부 상태를 기록하는 AnalysisLogger.

- 활성화 여부는 AppConfig.observability_enabled 로 제어된다.
- 비활성 시 모든 메서드는 no-op (성능 영향 없음).
- 활성 시 메모리 버퍼에 Stage 별 payload 를 누적하고,
  파이프라인 완료 시점에 history_id 를 받아 SQLite analysis_logs 에 일괄 flush.

사용 예시:
    logger = AnalysisLogger(enabled=cfg.observability_enabled)
    logger.log("stage1", {"raw_lines": 1000, "refined_lines": 200})
    ...
    logger.flush(history_id=42)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

from core.db import DB_PATH, get_conn


class _Entry(NamedTuple):
    stage: str
    payload: dict[str, Any]


@dataclass
class AnalysisLogger:
    """파이프라인 내부 상태 수집기. 비활성화 시 no-op."""

    enabled: bool = False
    db_path: Path = DB_PATH
    _entries: list[_Entry] = field(default_factory=list)

    def log(self, stage: str, payload: dict[str, Any]) -> None:
        """Stage payload 를 메모리 버퍼에 추가한다."""
        if not self.enabled:
            return
        self._entries.append(_Entry(stage, payload))

    def flush(self, history_id: int | None) -> None:
        """버퍼를 DB 에 기록하고 비운다. history_id 가 None 이어도 저장한다."""
        if not self.enabled or not self._entries:
            self._entries.clear()
            return

        try:
            with get_conn(self.db_path) as conn:
                for e in self._entries:
                    conn.execute(
                        "INSERT INTO analysis_logs (history_id, stage, payload) "
                        "VALUES (?, ?, ?)",
                        (
                            history_id,
                            e.stage,
                            json.dumps(e.payload, ensure_ascii=False, default=str),
                        ),
                    )
        except Exception:
            pass
        finally:
            self._entries.clear()


# ── 조회 API ─────────────────────────────────────────────────────────────────

def load_logs(history_id: int, db_path: Path = DB_PATH) -> list[dict]:
    """특정 history_id 의 Stage 별 로그를 조회한다. id 오름차순."""
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT id, stage, payload, created_at FROM analysis_logs "
            "WHERE history_id = ? ORDER BY id",
            (history_id,),
        ).fetchall()

    result: list[dict] = []
    for r in rows:
        d = dict(r)
        try:
            d["payload_parsed"] = json.loads(d["payload"])
        except Exception:
            d["payload_parsed"] = {}
        result.append(d)
    return result


def clear_logs(history_id: int | None = None, db_path: Path = DB_PATH) -> int:
    """history_id 의 로그(또는 전체)를 삭제한다. 반환: 삭제 건수."""
    with get_conn(db_path) as conn:
        if history_id is None:
            cur = conn.execute("DELETE FROM analysis_logs")
        else:
            cur = conn.execute(
                "DELETE FROM analysis_logs WHERE history_id = ?",
                (history_id,),
            )
        return cur.rowcount
