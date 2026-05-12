"""
Step 클래스 모듈

Step은 Interaction 안에서만 존재합니다 (composition).
Condition은 Step 안에 embedding된 내부 클래스입니다.

condition types:
    - domain_is
    - url_contains
    - url_matches
    - url_equals
    - selector_exists
    - title_contains

conditions (복수):
    - operator: "and" / "or"
    - items: list[condition]
"""

from __future__ import annotations
import re
from urllib.parse import urlparse

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from .action import Action


class Step:
    """
    Interaction의 단일 실행 단위입니다.
    condition 매칭 시 actions를 순서대로 실행합니다.
    """

    # =========================================================================
    # Condition (내부 클래스)
    # =========================================================================

    class Condition:
        """
        페이지 상태를 확인하는 조건 클래스입니다.
        Step 외부에서 독립적으로 사용되지 않습니다.

        condition types:
            - domain_is:       base domain 정확히 일치
            - url_contains:    URL에 문자열 포함
            - url_matches:     URL이 정규식과 일치
            - url_equals:      URL 완전 일치
            - selector_exists: 셀렉터가 페이지에 존재
            - title_contains:  페이지 타이틀에 문자열 포함
        """

        def __init__(self, config: dict):
            self.type  = config.get("type")
            self.value = config.get("value")

        async def check(self, page: Page, site_name: str) -> bool:
            """단일 조건을 확인합니다."""

            if self.type == "domain_is":
                current = urlparse(page.url).netloc
                result  = current == self.value
                print(f"[{site_name}] 조건 domain_is('{self.value}'): {result} / 현재: '{current}'")
                return result

            elif self.type == "url_contains":
                result = self.value in page.url
                print(f"[{site_name}] 조건 url_contains('{self.value}'): {result} / 현재: '{page.url}'")
                return result

            elif self.type == "url_matches":
                result = bool(re.search(self.value, page.url))
                print(f"[{site_name}] 조건 url_matches('{self.value}'): {result} / 현재: '{page.url}'")
                return result

            elif self.type == "url_equals":
                result = page.url == self.value
                print(f"[{site_name}] 조건 url_equals('{self.value}'): {result} / 현재: '{page.url}'")
                return result

            elif self.type == "selector_exists":
                try:
                    await page.wait_for_selector(self.value, timeout=3000)
                    print(f"[{site_name}] 조건 selector_exists('{self.value}'): True")
                    return True
                except PlaywrightTimeoutError:
                    print(f"[{site_name}] 조건 selector_exists('{self.value}'): False")
                    return False

            elif self.type == "title_contains":
                title  = await page.title()
                result = self.value in title
                print(f"[{site_name}] 조건 title_contains('{self.value}'): {result} / 현재: '{title}'")
                return result

            else:
                print(f"[{site_name}] ⚠️ 알 수 없는 condition type: '{self.type}'")
                return False

    # =========================================================================
    # Step
    # =========================================================================

    def __init__(self, config: dict):
        self.name       = config.get("name", "unnamed")
        self.is_final   = config.get("final", False)
        self.match_once = config.get("match_once", False)
        self.actions    = [Action.from_config(a) for a in config.get("actions", [])]
        self.depends_on = self._parse_depends_on(config.get("depends_on"))
        self._condition_config = config  # 조건 확인 시 참조

    def _parse_depends_on(self, depends_on) -> list[str]:
        """depends_on을 항상 list로 반환합니다."""
        if depends_on is None:
            return []
        if isinstance(depends_on, str):
            return [depends_on]
        return depends_on

    def is_depends_met(self, completed_steps: set[str]) -> bool:
        """
        모든 의존 step이 완료됐는지 확인합니다.
        대소문자 구분 없이 비교합니다.
        """
        completed_lower = {s.lower() for s in completed_steps}
        for dep in self.depends_on:
            if dep.lower() not in completed_lower:
                return False
        return True

    async def check_condition(self, page: Page, site_name: str) -> bool:
        """
        step의 condition 또는 conditions를 확인합니다.
        조건이 없으면 항상 True (무조건 실행)
        """
        # 단일 condition
        condition_config = self._condition_config.get("condition")
        if condition_config:
            return await self.Condition(condition_config).check(page, site_name)

        # 복수 conditions
        conditions_config = self._condition_config.get("conditions")
        if conditions_config:
            operator = conditions_config.get("operator", "and").lower()
            items    = conditions_config.get("items", [])

            print(f"[{site_name}] 복수 조건 확인 (operator: {operator}, {len(items)}개)")
            results = [await self.Condition(item).check(page, site_name) for item in items]

            if operator == "and":
                result = all(results)
            elif operator == "or":
                result = any(results)
            else:
                print(f"[{site_name}] ⚠️ 알 수 없는 operator: '{operator}'")
                return False

            print(f"[{site_name}] 복수 조건 결과 ({operator}): {result}")
            return result

        # 조건 없음 → 항상 매칭
        return True

    async def execute(self, page: Page, site_config: dict, site_name: str) -> tuple[bool, Page]:
        """
        step의 actions를 순서대로 실행합니다.
        Returns: (성공 여부, 현재 page)
        """
        current_page = page
        for action in self.actions:
            ok, current_page = await action.execute(current_page, site_config, site_name)
            if not ok:
                print(f"[{site_name}] ❌ action 실패: {action.__class__.__name__}")
                return False, current_page
        return True, current_page

    @classmethod
    def from_config_list(cls, configs: list[dict]) -> list[Step]:
        """config 배열로부터 Step 목록을 생성합니다."""
        return [cls(c) for c in configs]