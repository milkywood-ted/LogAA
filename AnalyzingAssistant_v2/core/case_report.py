"""
core/case_report.py

케이스 리포트 v2 필드(판정·조치)의 표기 헬퍼.

`cases` 테이블의 판정/조치 컬럼은 enum 토큰으로 저장된다. 이를 사람이 읽는
문구로 바꾸는 변환을 한곳에 모아, Stage 5 프롬프트(`report_generator`)와
분석 결과 직렬화(`pipeline.serialize_result`)가 같은 표기를 쓰도록 한다.

라벨은 frontend `CaseManagePage.jsx` 의 상수(KEEP_OPTIONS / ADDITIONAL_ITEMS /
HANDOVER_ITEMS / REASON_OPTIONS)와 동일하게 유지한다 — 케이스 편집 화면과
분석 리포트가 같은 용어로 말해야 한다.

저장 스키마는 `api/router/cases.py` 의 `CaseSaveRequest` / `CaseActions` 참조.
"""

from __future__ import annotations

# ── 라벨 ──────────────────────────────────────────────────────────────────────

# 케이스가 원 분석에서 받은 판정. 분석 실행의 유사도 판정("유사도 높음"/…)과는
# 다른 어휘이며, 케이스 편집 화면(CaseManagePage VERDICT_LABEL)과 같은 말을 쓴다.
VERDICT_LABEL = {
    "defect":       "결함",
    "no_defect":    "비결함",
    "undetermined": "판정불가",
}

UNDETERMINED_REASON_LABEL = {
    "insufficient_logs": "로그 부족",
    "not_reproducible":  "재현 불가",
    "other":             "기타",
}

KEEP_DETAIL_LABEL = {
    "close_no_defect":    "비결함 종결",
    "accept_defect":      "결함 수용·보류",
    "close_undetermined": "판정불가 종결",
}

ADDITIONAL_ITEM_LABEL = {
    "add_logs":        "추가 로그 삽입",
    "secure_repro":    "재현 조건 확보",
    "wait_recurrence": "재발 대기",
}

AREA_TYPE_LABEL = {
    "module":       "특정 모듈",
    "external":     "외부요인",
    "verification": "검증계",
}

# external / verification 의 하위 항목 (CaseManagePage EXTERNAL_ITEMS + VERIFICATION_ITEMS).
AREA_ITEM_LABEL = {
    "hw":           "HW",
    "env":          "환경",
    "third_party":  "서드파티",
    "customer":     "고객사",
    "test_env":     "테스트 환경",
    "test_script":  "테스트 스크립트",
    "measurement":  "측정 장치",
}

HANDOVER_ITEM_LABEL = {
    "other_module": "타 모듈 수정",
    "customer":     "고객사 수정",
    "third_party":  "서드파티 수정",
    "hw":           "HW 조치",
    "verification": "검증계 담당 조치",
}

# "기타" 항목은 `other:<서술>` 토큰 규약을 쓴다 (케이스 스키마 개선 구현 설계 §2.2).
_OTHER_PREFIX = "other:"


# ── 판정 ──────────────────────────────────────────────────────────────────────

def verdict_label(code: str | None) -> str:
    """판정 코드를 표기 문구로 바꾼다. 미기재(None)면 빈 문자열."""
    if code is None:
        return ""
    return VERDICT_LABEL.get(code, code)


def undetermined_reason_text(code: str | None, note: str = "") -> str:
    """판정불가 사유를 '라벨 — 서술' 형태로 바꾼다."""
    if code is None:
        return "사유 미기재"
    label = UNDETERMINED_REASON_LABEL.get(code, code)
    note = (note or "").strip()
    return f"{label} — {note}" if note else label


def defect_area_text(
    area_type: str | None,
    module: str = "",
    items: list[str] | None = None,
) -> str:
    """결함영역을 '유형 (대상)' 형태로 바꾼다. 미기재면 빈 문자열.

    증상이 보이는 곳(symptom_module)과 결함이 실제로 있는 곳은 다를 수 있고,
    원 분석이 이미 그 구분을 확정해 두었다면 재분석에서 가장 값진 정보다.
    """
    if not area_type:
        return ""
    label = AREA_TYPE_LABEL.get(area_type, area_type)
    if area_type == "module":
        return f"{label} ({module.strip()})" if module.strip() else label
    names = ", ".join(
        _token_label(t, AREA_ITEM_LABEL) for t in (items or []) if isinstance(t, str)
    )
    return f"{label} ({names})" if names else label


# ── 조치 ──────────────────────────────────────────────────────────────────────

def _token_label(token: str, table: dict[str, str]) -> str:
    """토큰을 라벨로 바꾼다. `other:<서술>` 규약을 인식한다."""
    if token.startswith(_OTHER_PREFIX):
        note = token[len(_OTHER_PREFIX):].strip()
        return f"기타 — {note}" if note else "기타"
    return table.get(token, token)


def _selected(actions: dict, key: str) -> dict | None:
    """선택된 조치 블록만 반환한다. 미선택·형식 불일치면 None."""
    block = (actions or {}).get(key)
    if not isinstance(block, dict) or not block.get("selected"):
        return None
    return block


def action_lines(actions: dict | None) -> list[str]:
    """조치 이력을 사람이 읽는 줄 목록으로 만든다. 조치가 없으면 빈 목록.

    조치는 복수 선택이 가능하므로 선택된 것만 순서대로 담는다.
    """
    actions = actions or {}
    lines: list[str] = []

    fix = _selected(actions, "fix")
    if fix is not None:
        entries = fix.get("entries") or []
        parts = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            module = (e.get("module") or "").strip()
            change = (e.get("change") or "").strip()
            parts.append(f"{module} — {change}" if change else module)
        lines.append("수정 및 해결: " + ("; ".join(p for p in parts if p) or "항목 미기재"))

    additional = _selected(actions, "additional")
    if additional is not None:
        items = [_token_label(t, ADDITIONAL_ITEM_LABEL)
                 for t in (additional.get("items") or []) if isinstance(t, str)]
        plan = (additional.get("plan") or "").strip()
        text = ", ".join(items) or "항목 미기재"
        lines.append(f"추가 조치 필요: {text}" + (f" (계획: {plan})" if plan else ""))

    keep = _selected(actions, "keep")
    if keep is not None:
        detail = keep.get("detail")
        label  = KEEP_DETAIL_LABEL.get(detail, detail or "구분 미기재")
        reason = (keep.get("reason") or "").strip()
        lines.append(f"유지·종결: {label}" + (f" (사유: {reason})" if reason else ""))

    handover = _selected(actions, "handover")
    if handover is not None:
        items = [_token_label(t, HANDOVER_ITEM_LABEL)
                 for t in (handover.get("items") or []) if isinstance(t, str)]
        owner   = (handover.get("owner") or "").strip()
        channel = (handover.get("channel") or "").strip()
        tail = ", ".join(filter(None, [
            f"해결 주체: {owner}" if owner else "",
            f"채널: {channel}" if channel else "",
        ]))
        text = ", ".join(items) or "항목 미기재"
        lines.append(f"이관·외부 대응: {text}" + (f" ({tail})" if tail else ""))

    return lines


def action_summary(actions: dict | None) -> str:
    """조치 상태를 한 줄로 요약한다. 조치가 하나도 없으면 "미조치(열린 건)".

    UI 배지·이력 목록처럼 좁은 자리에 쓰기 위한 짧은 표기이며, 상세는
    `action_lines` 를 쓴다. 미수정 상태(결함 수용·이관)를 먼저 드러내
    "이미 처리된 건" 으로 오해하지 않도록 한다.
    """
    actions = actions or {}
    parts: list[str] = []

    keep = _selected(actions, "keep")
    if keep is not None and keep.get("detail") == "accept_defect":
        parts.append("결함 수용·보류(미수정)")
    if _selected(actions, "handover") is not None:
        parts.append("이관")
    if _selected(actions, "fix") is not None:
        parts.append("수정 완료")
    if _selected(actions, "additional") is not None:
        parts.append("추가 조치 필요")
    if keep is not None and keep.get("detail") != "accept_defect":
        parts.append(KEEP_DETAIL_LABEL.get(keep.get("detail"), "종결"))

    return " · ".join(parts) if parts else "미조치(열린 건)"
