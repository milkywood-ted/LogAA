"""
Step 클래스 모듈

Step은 Interaction 안에서만 존재합니다 (composition).
Condition은 Step 안에 embedding된 내부 클래스입니다.

step types:
    - sequence:     메인 플로우. expect 충족 시 next_step으로, 미충족 시 non-sequence로
    - non-sequence: 예외/부가 처리. 조건 매칭 시 실행, jump로 sequence step 복귀

condition types:
    - domain_is
    - url_contains
    - url_matches
    - url_equals
    - selector_exists
    - selector_not_exists
    - title_contains
"""

from __future__ import annotations
import re
from urllib.parse import urlparse

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from .action import Action


class Step:

    # =========================================================================
    # Condition (내부 클래스)
    # =========================================================================

    class Condition:
        def __init__(self, config: dict):
            self.type    = config.get("type")
            self.value   = config.get("value")
            self.timeout = config.get("timeout", 5000)

        async def check(self, page: Page, site_name: str, site_config: dict = None) -> bool:
            # {key} 형식을 param_values로 치환
            value = self.value
            if site_config and value:
                for k, v in site_config.get("_param_values", {}).items():
                    value = value.replace(f"{{{k}}}", str(v))

            if self.type == "domain_is":
                current = urlparse(page.url).netloc
                result  = current == value
                print(f"[{site_name}] 조건 domain_is('{value}'): {result} / 현재: '{current}'")
                return result

            elif self.type == "url_contains":
                result = value in page.url
                print(f"[{site_name}] 조건 url_contains('{value}'): {result} / 현재: '{page.url}'")
                return result

            elif self.type == "url_matches":
                result = bool(re.search(value, page.url))
                print(f"[{site_name}] 조건 url_matches('{value}'): {result} / 현재: '{page.url}'")
                return result

            elif self.type == "url_equals":
                result = page.url == value
                print(f"[{site_name}] 조건 url_equals('{value}'): {result} / 현재: '{page.url}'")
                return result

            elif self.type == "selector_exists":
                try:
                    await page.wait_for_selector(value, timeout=self.timeout)
                    print(f"[{site_name}] 조건 selector_exists('{value}'): True")
                    return True
                except PlaywrightTimeoutError:
                    print(f"[{site_name}] 조건 selector_exists('{value}'): False")
                    return False

            elif self.type == "selector_not_exists":
                try:
                    await page.wait_for_selector(value, timeout=self.timeout)
                    print(f"[{site_name}] 조건 selector_not_exists('{value}'): False")
                    return False
                except PlaywrightTimeoutError:
                    print(f"[{site_name}] 조건 selector_not_exists('{value}'): True")
                    return True

            elif self.type == "title_contains":
                title  = await page.title()
                result = value in title
                print(f"[{site_name}] 조건 title_contains('{value}'): {result} / 현재: '{title}'")
                return result

            else:
                print(f"[{site_name}] ⚠️ 알 수 없는 condition type: '{self.type}'")
                return False

    # =========================================================================
    # Step
    # =========================================================================

    SEQUENCE     = "sequence"
    NON_SEQUENCE = "non-sequence"

    def __init__(self, config: dict):
        self.name       = config.get("name", "unnamed")
        self.step_type  = config.get("type", self.SEQUENCE)
        self.is_final   = config.get("final", False)
        self.next_step  = config.get("next_step")
        self.next_steps = config.get("next_steps", [])   # [{condition: ..., step: ...}]
        self.actions    = [Action.from_config(a) for a in config.get("actions", [])]
        self._config    = config

        expect_config   = config.get("expect")
        self.expect     = self.Condition(expect_config) if expect_config else None

    @property
    def is_sequence(self) -> bool:
        return self.step_type == self.SEQUENCE

    @property
    def is_non_sequence(self) -> bool:
        return self.step_type == self.NON_SEQUENCE

    async def check_condition(self, page: Page, site_name: str, site_config: dict = None) -> bool:
        condition_config = self._config.get("condition")
        if condition_config:
            if isinstance(condition_config, list):
                print(f"[{site_name}] ⚠️ condition이 list 형식입니다. 단일 조건으로 작성해 주세요.")
                return False
            return await self.Condition(condition_config).check(page, site_name, site_config)

        conditions_config = self._config.get("conditions")
        if conditions_config:
            if isinstance(conditions_config, list):
                print(f"[{site_name}] ⚠️ conditions가 list 형식입니다. operator/items 구조로 작성해 주세요.")
                return False
            operator = conditions_config.get("operator", "and").lower()
            items    = conditions_config.get("items", [])
            print(f"[{site_name}] 복수 조건 확인 (operator: {operator}, {len(items)}개)")
            results = [await self.Condition(item).check(page, site_name, site_config) for item in items]
            if operator == "and":
                result = all(results)
            elif operator == "or":
                result = any(results)
            else:
                print(f"[{site_name}] ⚠️ 알 수 없는 operator: '{operator}'")
                return False
            print(f"[{site_name}] 복수 조건 결과 ({operator}): {result}")
            return result

        return True

    async def check_expect(self, page: Page, site_name: str, site_config: dict = None) -> bool:
        if not self.expect:
            return True
        result = await self.expect.check(page, site_name, site_config)
        print(f"[{site_name}] 📋 expect 결과: {result}")
        return result

    async def resolve_next_step(self, page: Page, site_name: str, site_config: dict = None) -> str | None:
        """
        next_steps 중 조건이 맞는 step 이름을 반환합니다.
        next_steps가 없으면 next_step을 반환합니다.
        조건이 맞는 게 없으면 None을 반환합니다.
        """
        # next_steps (복수) 우선 처리
        if self.next_steps:
            for item in self.next_steps:
                if not isinstance(item, dict):
                    print(f"[{site_name}] ⚠️ next_steps 항목이 dict가 아닙니다: {type(item)} / {item}")
                    continue
                condition_config = item.get("condition")
                step_name        = item.get("step")
                if not step_name:
                    continue
                if condition_config:
                    result = await self.Condition(condition_config).check(page, site_name, site_config)
                    if result:
                        print(f"[{site_name}] ➡ next_steps 조건 충족 → '{step_name}'")
                        return step_name
                else:
                    # 조건 없으면 바로 선택
                    print(f"[{site_name}] ➡ next_steps 조건 없음 → '{step_name}'")
                    return step_name
            print(f"[{site_name}] ⚠️ next_steps 조건 모두 미충족")
            return None

        # next_step (단수)
        return self.next_step

    async def execute(self, page: Page, site_config: dict, site_name: str) -> tuple[bool, Page]:
        current_page = page
        for action in self.actions:
            ok, current_page = await action.execute(current_page, site_config, site_name)
            if not ok:
                print(f"[{site_name}] ❌ action 실패: {action.__class__.__name__}")
                return False, current_page
        return True, current_page

    @classmethod
    def from_config_list(cls, configs: list[dict]) -> list[Step]:
        return [cls(c) for c in configs]
