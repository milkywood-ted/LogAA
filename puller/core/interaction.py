"""
Interaction 클래스 모듈

sequence/non-sequence 타입 기반 step 실행 엔진입니다.

실행 흐름:
    entry_step에서 시작
    → condition 체크 (있으면)
    → actions 실행
    → expect 체크
      → 충족: next_step으로
      → 미충족: failure_count++
        → max_failures 초과: 전체 실패
        → 미초과: non-sequence steps 중 조건 맞는 것 실행
          → 매칭 없음: 전체 실패 (configuration 오류)
          → 매칭 있음: jump로 sequence step 복귀
"""

from __future__ import annotations

from playwright.async_api import Page

from .step import Step
from .login import wait_for_url_stable


class Interaction:
    """
    Step들을 sequence/non-sequence 타입 기반으로 실행합니다.
    """

    def __init__(self, config: dict, site_config: dict):
        self.max_iterations = config.get("max_iterations", 10)
        self.max_failures   = config.get("max_failures", 3)
        self.entry_step     = config.get("entry_step")
        self.site_config    = site_config
        self.site_name      = site_config.get("name", "unknown")

        all_steps = Step.from_config_list(config.get("steps", []))
        self.sequence_steps     = [s for s in all_steps if s.is_sequence]
        self.non_sequence_steps = [s for s in all_steps if s.is_non_sequence]

        print(f"[{self.site_name}] sequence steps: {[s.name for s in self.sequence_steps]}")
        print(f"[{self.site_name}] non-sequence steps: {[s.name for s in self.non_sequence_steps]}")

    def _find_step(self, name: str, steps: list[Step]) -> Step | None:
        """이름으로 step을 찾습니다. 대소문자 구분 없음."""
        name_lower = name.lower()
        return next((s for s in steps if s.name.lower() == name_lower), None)

    async def _run_non_sequence(self, page: Page, until_step_name: str = None) -> tuple[bool, Page, str | None]:
        """
        non-sequence steps 중 조건이 맞는 것을 실행합니다.
        Returns: (성공 여부, 현재 page, jump_to step 이름)
        """
        for step in self.non_sequence_steps:
            print(f"[{self.site_name}] 🔍 non-sequence 체크: '{step.name}'")

            if not await step.check_condition(page, self.site_name, self.site_config):
                continue

            print(f"[{self.site_name}] ✔ non-sequence 매칭: '{step.name}'")
            ok, page = await step.execute(page, self.site_config, self.site_name)
            if not ok:
                print(f"[{self.site_name}] ❌ non-sequence step 실패: '{step.name}'")
                return False, page, None

            # jump action 결과 확인
            jump_to = self.site_config.pop("_jump_to", None)
            return True, page, jump_to

        print(f"[{self.site_name}] ❌ 매칭되는 non-sequence step 없음 (configuration 오류)")
        return False, page, None

    async def run(self, page: Page, until_step_name: str = None) -> tuple[bool, Page]:
        """
        interaction을 실행합니다.

        Args:
            page: Playwright Page 객체
            until_step_name: 이 step 조건 충족 시 actions 없이 중단 (None이면 전체 실행)

        Returns:
            (bool, Page): 성공 여부, 현재 page
        """
        current_page    = page
        failure_count   = 0
        current_step    = None

        print(f"[{self.site_name}] 🔄 interaction 시작")
        print(f"[{self.site_name}] max_iterations: {self.max_iterations}, max_failures: {self.max_failures}")

        # entry_step 결정
        if self.entry_step:
            current_step = self._find_step(self.entry_step, self.sequence_steps)
            if not current_step:
                print(f"[{self.site_name}] ❌ entry_step을 찾을 수 없습니다: '{self.entry_step}'")
                return False, current_page
        elif self.sequence_steps:
            current_step = self.sequence_steps[0]
        else:
            print(f"[{self.site_name}] ❌ sequence step이 없습니다.")
            return False, current_page

        print(f"[{self.site_name}] 🎯 entry step: '{current_step.name}'")

        for iteration in range(self.max_iterations):
            print(f"\n[{self.site_name}] ─── iteration {iteration + 1}/{self.max_iterations} ───")
            print(f"[{self.site_name}] 현재 step: '{current_step.name}' / failures: {failure_count}/{self.max_failures}")

            # until_step_name 체크
            if until_step_name and current_step.name == until_step_name:
                print(f"[{self.site_name}] 🎯 중단 조건 충족: '{current_step.name}'")
                return True, current_page

            # condition 체크 (있으면)
            if not await current_step.check_condition(current_page, self.site_name, self.site_config):
                print(f"[{self.site_name}] ⏸ '{current_step.name}' 조건 미충족 → non-sequence 실행")
                ok, current_page, jump_to = await self._run_non_sequence(current_page, until_step_name)
                if not ok:
                    return False, current_page
                if jump_to:
                    next_s = self._find_step(jump_to, self.sequence_steps)
                    if next_s:
                        current_step = next_s
                continue
            # actions 실행
            action_types = [a.__class__.__name__ for a in current_step.actions]
            print(f"[{self.site_name}] ✔ '{current_step.name}' 실행 actions: {action_types}")

            ok, current_page = await current_step.execute(current_page, self.site_config, self.site_name)
            if not ok:
                print(f"[{self.site_name}] ❌ step 실패: '{current_step.name}'")
                failure_count += 1
                if failure_count >= self.max_failures:
                    print(f"[{self.site_name}] ❌ max_failures({self.max_failures}) 초과 → 전체 실패")
                    return False, current_page
                continue

            # jump action 처리
            jump_to = self.site_config.pop("_jump_to", None)
            if jump_to:
                print(f"[{self.site_name}] 🔀 jump → '{jump_to}'")
                next_s = self._find_step(jump_to, self.sequence_steps)
                if next_s:
                    current_step = next_s
                continue

            # URL 안정화 대기
            if current_step.actions:
                await wait_for_url_stable(current_page, self.site_name)

            # expect 체크
            expect_ok = await current_step.check_expect(current_page, self.site_name, self.site_config)

            if expect_ok:
                # final step이면 종료
                if current_step.is_final:
                    print(f"[{self.site_name}] ✅ final step 완료: '{current_step.name}'")
                    return True, current_page

                # next_step 결정 (next_steps 복수 또는 next_step 단수)
                resolved = await current_step.resolve_next_step(current_page, self.site_name, self.site_config)
                if resolved:
                    next_s = self._find_step(resolved, self.sequence_steps)
                    if next_s:
                        print(f"[{self.site_name}] ➡ next_step: '{next_s.name}'")
                        current_step  = next_s
                        failure_count = 0
                    else:
                        print(f"[{self.site_name}] ❌ next_step을 찾을 수 없습니다: '{resolved}'")
                        return False, current_page
                else:
                    print(f"[{self.site_name}] ⚠️ next_step이 없거나 조건 미충족: '{current_step.name}'")
                    return False, current_page

            else:
                # expect 미충족 → failure_count 증가
                failure_count += 1
                print(f"[{self.site_name}] ⚠️ expect 미충족 ({failure_count}/{self.max_failures})")

                if failure_count >= self.max_failures:
                    print(f"[{self.site_name}] ❌ max_failures({self.max_failures}) 초과 → 전체 실패")
                    return False, current_page

                # non-sequence steps 실행
                ok, current_page, jump_to = await self._run_non_sequence(current_page, until_step_name)
                if not ok:
                    return False, current_page
                if jump_to:
                    next_s = self._find_step(jump_to, self.sequence_steps)
                    if next_s:
                        current_step = next_s

        print(f"[{self.site_name}] ⚠️ max_iterations({self.max_iterations}) 초과 → 종료")
        return False, current_page

    @classmethod
    def from_site_config(cls, site_config: dict) -> Interaction | None:
        interaction_config = site_config.get("interactions")
        if not interaction_config:
            return None
        return cls(interaction_config, site_config)
