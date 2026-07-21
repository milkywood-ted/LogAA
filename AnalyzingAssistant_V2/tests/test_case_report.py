"""core/case_report.py — 케이스 판정·조치 토큰의 표기 변환.

조치(cases.actions)는 판정과 다른 축이다. 특히 "결함이지만 미수정 수용"과
"타 주체 이관"은 판정만 봐서는 알 수 없고, 이 정보가 리포트에 전달되지 않으면
이미 처리 방침이 정해진 문제를 다시 조사하게 된다.
"""

import pytest

from core.case_report import (
    action_lines,
    action_summary,
    defect_area_text,
    undetermined_reason_text,
    verdict_label,
)


# ── 판정 표기 ─────────────────────────────────────────────────────────────────

def test_verdict_label():
    assert verdict_label("defect") == "결함"
    assert verdict_label("no_defect") == "비결함"
    assert verdict_label(None) == ""          # 레거시 행 — 표기 없음
    assert verdict_label("unknown_code") == "unknown_code"   # 미지 코드는 원문 노출


def test_undetermined_reason_text():
    assert undetermined_reason_text("insufficient_logs") == "로그 부족"
    assert undetermined_reason_text("other", "재현 장비 회수됨") == "기타 — 재현 장비 회수됨"
    assert undetermined_reason_text(None) == "사유 미기재"


# ── 결함영역 표기 ─────────────────────────────────────────────────────────────

def test_defect_area_text_module():
    assert defect_area_text("module", "pm_core") == "특정 모듈 (pm_core)"
    assert defect_area_text("module", "  ") == "특정 모듈"     # 모듈명 공백뿐이면 유형만


def test_defect_area_text_external_and_verification():
    assert defect_area_text("external", items=["hw", "third_party"]) == "외부요인 (HW, 서드파티)"
    assert defect_area_text("verification", items=["test_script"]) == "검증계 (테스트 스크립트)"


def test_defect_area_text_absent_type():
    assert defect_area_text(None) == ""
    assert defect_area_text("") == ""


# ── 조치 상세 ─────────────────────────────────────────────────────────────────

def test_action_lines_empty_when_nothing_selected():
    assert action_lines(None) == []
    assert action_lines({}) == []
    assert action_lines({"fix": {"selected": False, "entries": [{"module": "m"}]}}) == []


def test_action_lines_fix_entries():
    actions = {"fix": {"selected": True, "entries": [
        {"module": "ata_driver", "change": "링크 리셋 재시도 추가"},
        {"module": "pm_core", "change": ""},
    ]}}
    assert action_lines(actions) == [
        "수정 및 해결: ata_driver — 링크 리셋 재시도 추가; pm_core"
    ]


def test_action_lines_keep_accept_defect_carries_reason():
    actions = {"keep": {"selected": True, "detail": "accept_defect",
                        "reason": "우선순위 낮음, 차기 릴리스 이월"}}
    assert action_lines(actions) == [
        "유지·종결: 결함 수용·보류 (사유: 우선순위 낮음, 차기 릴리스 이월)"
    ]


def test_action_lines_handover_owner_and_channel():
    actions = {"handover": {"selected": True, "items": ["other_module", "third_party"],
                            "owner": "platform팀", "channel": "DTV-1234"}}
    assert action_lines(actions) == [
        "이관·외부 대응: 타 모듈 수정, 서드파티 수정 (해결 주체: platform팀, 채널: DTV-1234)"
    ]


def test_action_lines_additional_with_other_token():
    """'기타' 는 other:<서술> 토큰 규약을 따른다."""
    actions = {"additional": {"selected": True,
                              "items": ["add_logs", "other:전원 시퀀스 계측"],
                              "plan": "다음 스프린트"}}
    assert action_lines(actions) == [
        "추가 조치 필요: 추가 로그 삽입, 기타 — 전원 시퀀스 계측 (계획: 다음 스프린트)"
    ]


def test_action_lines_preserves_multiple_selections():
    actions = {
        "fix": {"selected": True, "entries": [{"module": "m", "change": "c"}]},
        "handover": {"selected": True, "items": [], "owner": "o", "channel": ""},
    }
    lines = action_lines(actions)
    assert len(lines) == 2
    assert lines[0].startswith("수정 및 해결:")
    assert lines[1].startswith("이관·외부 대응:")


@pytest.mark.parametrize("malformed", [
    {"keep": "not-a-dict"},
    {"keep": {"selected": True, "detail": None}},
    {"fix": {"selected": True}},
])
def test_action_lines_tolerates_malformed_blocks(malformed):
    """레거시·부분 저장 행이 있어도 예외 없이 최선의 표기를 낸다."""
    action_lines(malformed)   # 예외가 나지 않으면 통과


# ── 조치 요약 ─────────────────────────────────────────────────────────────────

def test_action_summary_open_case():
    assert action_summary({}) == "미조치(열린 건)"


def test_action_summary_surfaces_unfixed_state_first():
    """수정 완료와 결함 수용이 함께 선택되어도 미수정 상태가 먼저 드러나야 한다."""
    actions = {
        "fix": {"selected": True, "entries": [{"module": "m", "change": "c"}]},
        "keep": {"selected": True, "detail": "accept_defect", "reason": "r"},
    }
    summary = action_summary(actions)
    assert summary.startswith("결함 수용·보류(미수정)")
    assert "수정 완료" in summary


def test_action_summary_closed_case():
    actions = {"keep": {"selected": True, "detail": "close_no_defect", "reason": ""}}
    assert action_summary(actions) == "비결함 종결"
