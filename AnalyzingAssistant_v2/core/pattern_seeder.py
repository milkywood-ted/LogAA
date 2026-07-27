"""
core/pattern_seeder.py

default_patterns.yaml → SQLite seed 로직.

공개 API:
  seed(yaml_path, db_path)   — 테이블이 비어 있을 때만 삽입 (최초 1회)
  reset(yaml_path, db_path)  — 기존 데이터를 모두 지우고 재삽입 (초기화)
  load_yaml(yaml_path)       — YAML 파싱 + pydantic 검증만 수행 (DB 건드리지 않음)
"""

import json
import sqlite3
from pathlib import Path
from typing import Literal, Union

from pydantic import BaseModel, field_validator, model_validator

from core.config import patterns_config
from core.db import DB_PATH, get_conn, init_db
from core.pattern_lint import ERROR, format_issues, lint_pattern_fields


# ── Pydantic 모델 ─────────────────────────────────────────────────────────────

class _Base(BaseModel):
    name: str
    type: str
    description: str = ""
    keywords: list[str]
    weight: float = 1.0

    @field_validator("keywords")
    @classmethod
    def keywords_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("keywords 는 하나 이상이어야 합니다")
        return v

    @model_validator(mode="after")
    def regex_fields_must_compile(self) -> "_Base":
        """
        정규식 필드가 컴파일 가능한지 검증한다.

        컴파일 실패는 확정적 오류이므로 여기서 차단한다. 경고 수준의 문제는
        판단이 필요하므로 통과시키고, 경로별로 다르게 처리한다
        (YAML seed → load_yaml 에서 오류로 승격 / LLM 생성 → 검토 화면에 노출).
        """
        broken = [i for i in lint_pattern_fields(self.model_dump()) if i.issue.severity == ERROR]
        if broken:
            raise ValueError(
                f"패턴 '{self.name}' 의 정규식을 해석할 수 없습니다:\n{format_issues(broken)}"
            )
        return self


class PresencePattern(_Base):
    type: Literal["PRESENCE"]
    pattern: str
    event_dedup_window_sec: float | None = None


class SequencePattern(_Base):
    type: Literal["SEQUENCE"]
    steps: list[str]
    step_dedup: bool = False
    non_overlapping: bool = False

    @field_validator("steps")
    @classmethod
    def steps_not_empty(cls, v: list[str]) -> list[str]:
        if len(v) < 2:
            raise ValueError("SEQUENCE 는 steps 가 2개 이상이어야 합니다")
        return v


class WindowPattern(_Base):
    type: Literal["WINDOW"]
    pattern: str
    window_sec: float
    count_threshold: int
    count_unique_only: bool = False


class AbsencePattern(_Base):
    type: Literal["ABSENCE"]
    trigger_pattern: str
    absent_pattern: str
    window_sec: float


class CompositePattern(_Base):
    type: Literal["COMPOSITE"]
    operator: Literal["AND", "OR", "NOT"]
    components: list[str]

    @model_validator(mode="after")
    def not_requires_single(self) -> "CompositePattern":
        if self.operator == "NOT" and len(self.components) != 1:
            raise ValueError("COMPOSITE NOT 은 components 가 정확히 1개여야 합니다")
        if self.operator in ("AND", "OR") and len(self.components) < 2:
            raise ValueError(f"COMPOSITE {self.operator} 는 components 가 2개 이상이어야 합니다")
        return self


AnyPattern = Union[
    PresencePattern, SequencePattern, WindowPattern, AbsencePattern, CompositePattern
]

_TYPE_MAP: dict[str, type] = {
    "PRESENCE":  PresencePattern,
    "SEQUENCE":  SequencePattern,
    "WINDOW":    WindowPattern,
    "ABSENCE":   AbsencePattern,
    "COMPOSITE": CompositePattern,
}


def _parse_pattern(data: dict) -> AnyPattern:
    t = str(data.get("type", "")).upper()
    cls = _TYPE_MAP.get(t)
    if cls is None:
        raise ValueError(f"알 수 없는 패턴 타입: {t!r}  (허용: {list(_TYPE_MAP)})")
    return cls(**{**data, "type": t})


# ── 공개 API ──────────────────────────────────────────────────────────────────

def load_yaml(yaml_path: Path | None = None) -> list[AnyPattern]:
    """
    YAML 파일을 읽어 pydantic 모델 목록으로 반환한다.
    DB 는 건드리지 않는다.

    Parameters
    ----------
    yaml_path : None 이면 기본 경로(config/patterns/default_patterns.yaml) 사용.

    Raises
    ------
    FileNotFoundError : yaml_path 가 없을 때
    ValueError        : 스키마 검증 실패, 순환 참조, 미정의 참조,
                        정규식 경고 (아래 _reject_regex_warnings 참조)
    """
    raw = patterns_config.load(yaml_path)
    if not raw:
        return []
    patterns = [_parse_pattern(p) for p in raw.get("patterns", [])]
    _validate_references(patterns)
    _reject_regex_warnings(patterns)
    return patterns


def seed(
    yaml_path: Path | None = None,
    db_path: Path = DB_PATH,
) -> int:
    """
    patterns 테이블이 비어 있을 때만 YAML 을 읽어 삽입한다 (최초 1회 seed).

    Returns
    -------
    삽입된 패턴 수  (이미 데이터가 있으면 0)
    """
    init_db(db_path)

    with get_conn(db_path) as conn:
        if conn.execute("SELECT COUNT(*) FROM patterns").fetchone()[0] > 0:
            return 0

    return _insert_all(load_yaml(yaml_path), db_path)


def reset(
    yaml_path: Path | None = None,
    db_path: Path = DB_PATH,
) -> int:
    """
    기존 패턴 데이터를 모두 삭제하고 YAML 에서 재삽입한다.
    pattern_steps / pattern_components 는 CASCADE 로 자동 삭제된다.

    Returns
    -------
    삽입된 패턴 수
    """
    patterns = load_yaml(yaml_path)  # 검증 먼저 — 실패 시 DB 건드리지 않음

    with get_conn(db_path) as conn:
        conn.execute("DELETE FROM patterns")  # CASCADE → steps, components 자동 삭제

    return _insert_all(patterns, db_path)


# ── 내부 ──────────────────────────────────────────────────────────────────────

def _reject_regex_warnings(patterns: list[AnyPattern]) -> None:
    """
    정규식 경고를 오류로 승격한다 — YAML seed 전용.

    API 경로는 사용자가 경고를 확인하고 저장을 이어갈 수 있지만, seed 는 확인을
    받을 사람이 없다. 의도한 정규식이라면 YAML 에서, 리터럴이라면 백슬래시를
    붙여 명시해야 한다.
    """
    issues = [i for p in patterns for i in lint_pattern_fields(p.model_dump())]
    if issues:
        raise ValueError(
            "정규식 메타문자가 의도와 다르게 해석될 수 있습니다. "
            "리터럴이라면 백슬래시로 이스케이프하세요:\n" + format_issues(issues)
        )


def _validate_references(patterns: list[AnyPattern]) -> None:
    """
    COMPOSITE 의 components 참조가 유효한지, 순환 참조가 없는지 검사한다.
    """
    name_set = {p.name for p in patterns}
    composite = [p for p in patterns if isinstance(p, CompositePattern)]

    # 미정의 참조 검사
    for p in composite:
        for ref in p.components:
            if ref not in name_set:
                raise ValueError(
                    f"COMPOSITE '{p.name}': 참조 패턴 '{ref}' 이 정의되지 않았습니다"
                )

    # 순환 참조 검사 (DFS)
    deps: dict[str, list[str]] = {
        p.name: (p.components if isinstance(p, CompositePattern) else [])
        for p in patterns
    }

    def _dfs(node: str, visiting: set[str]) -> None:
        if node in visiting:
            raise ValueError(f"순환 참조 감지: {' → '.join(visiting)} → {node}")
        visiting.add(node)
        for child in deps.get(node, []):
            _dfs(child, visiting)
        visiting.discard(node)

    for name in deps:
        _dfs(name, set())


def _insert_all(patterns: list[AnyPattern], db_path: Path) -> int:
    """
    검증된 패턴 목록을 DB 에 삽입한다.
    COMPOSITE 는 참조 대상이 먼저 삽입되도록 비-COMPOSITE 를 먼저 처리한다.
    """
    non_composite = [p for p in patterns if not isinstance(p, CompositePattern)]
    composite     = [p for p in patterns if isinstance(p, CompositePattern)]

    name_to_id: dict[str, int] = {}

    with get_conn(db_path) as conn:
        for p in non_composite + composite:
            pid = _insert_one(conn, p, name_to_id)
            name_to_id[p.name] = pid

    return len(patterns)


def _insert_one(conn: sqlite3.Connection, p: AnyPattern, name_to_id: dict[str, int]) -> int:
    """패턴 한 건을 patterns / pattern_steps / pattern_components 에 삽입한다."""
    cur = conn.execute(
        """
        INSERT INTO patterns (
            name, type, description, keywords,
            pattern, event_dedup_window_sec,
            step_dedup, non_overlapping,
            window_sec, count_threshold, count_unique_only,
            trigger_pattern, absent_pattern,
            operator, weight
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            p.name,
            p.type,
            p.description,
            json.dumps(p.keywords, ensure_ascii=False),
            getattr(p, "pattern",                None),
            getattr(p, "event_dedup_window_sec", None),
            int(getattr(p, "step_dedup",         False)),
            int(getattr(p, "non_overlapping",    False)),
            getattr(p, "window_sec",             None),
            getattr(p, "count_threshold",        None),
            int(getattr(p, "count_unique_only",  False)),
            getattr(p, "trigger_pattern",        None),
            getattr(p, "absent_pattern",         None),
            getattr(p, "operator",               None),
            p.weight,
        ),
    )
    pid: int = cur.lastrowid

    # SEQUENCE steps
    if isinstance(p, SequencePattern):
        for i, step_pat in enumerate(p.steps):
            conn.execute(
                "INSERT INTO pattern_steps (pattern_id, step_order, pattern) VALUES (?, ?, ?)",
                (pid, i, step_pat),
            )

    # COMPOSITE components
    if isinstance(p, CompositePattern):
        for i, ref_name in enumerate(p.components):
            ref_id = name_to_id[ref_name]   # _validate_references 로 보장됨
            conn.execute(
                """
                INSERT INTO pattern_components (pattern_id, component_order, ref_pattern_id)
                VALUES (?, ?, ?)
                """,
                (pid, i, ref_id),
            )

    return pid
