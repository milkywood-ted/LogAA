"""core/chip_filter.py — 칩 태그 필터 순수 로직 (§9-1 캠페인 검증 정식화).

규칙: 태그 없음=공통(항상 통과), defect chip 없음=필터 안 함, 교집합 있으면 통과.
"""

from core.chip_filter import chip_matches, filter_patterns_by_chip, _as_str_list


def _pat(cid, tags):
    return {"case_id": cid, "chip_tags": tags}


def test_as_str_list_normalizes_forms():
    assert _as_str_list(None) == []
    assert _as_str_list("") == []
    assert _as_str_list("RheaM") == ["RheaM"]
    assert _as_str_list('["A", "B"]') == ["A", "B"]      # JSON 문자열
    assert _as_str_list(["A", "", "B"]) == ["A", "B"]    # 빈 값 제거


def test_chip_matches_intersection_case_insensitive():
    assert chip_matches(["Exynos1000"], "exynos1000") is True
    assert chip_matches(["Exynos1000"], ["exynos2000", "exynos1000"]) is True
    assert chip_matches(["Exynos1000"], "exynos2000") is False
    # 한쪽이라도 비면 False (가중치 대상 아님)
    assert chip_matches([], "exynos1000") is False
    assert chip_matches(["Exynos1000"], None) is False


def test_filter_common_pattern_always_passes():
    # chip_tags 비어 있으면 defect 칩과 무관하게 통과
    out = filter_patterns_by_chip([_pat(1, [])], "exynos1000")
    assert [p["case_id"] for p in out] == [1]


def test_filter_excludes_non_intersecting():
    pats = [_pat(1, []), _pat(2, ["Exynos1000"]), _pat(3, ["Exynos2000"])]
    out = filter_patterns_by_chip(pats, "exynos1000")
    assert sorted(p["case_id"] for p in out) == [1, 2]   # 3 제외


def test_filter_multi_chip_any_match():
    pats = [_pat(1, ["Exynos1000"]), _pat(2, ["Exynos2000"])]
    out = filter_patterns_by_chip(pats, ["exynos1000", "exynos2000"])
    assert sorted(p["case_id"] for p in out) == [1, 2]


def test_filter_no_defect_chip_passes_all():
    pats = [_pat(1, []), _pat(2, ["Exynos1000"]), _pat(3, ["Exynos2000"])]
    assert filter_patterns_by_chip(pats, None) == pats     # 필터 안 함
