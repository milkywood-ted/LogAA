"""
Interaction 클래스 모듈

조건 반응형으로 Step들을 순회하며 실행합니다.

동작 방식:
    - 매 iteration마다 현재 페이지 상태를 확인
    - depends_on 충족 + 조건 매칭된 step의 actions 실행
    - until_step_name 지정 시 해당 step 조건 충족 시 actions 없이 중단
    - final: true인 step 완료 시 종료
    - 매칭 step 없으면 target URL로 복귀
    - max_iterations 초과 시 종료
"""

from __future__ import annotations

from playwright.async_api import Page

from .step import Step
from .login import wait_for_url_stable


class Interaction:
    """
    Step들을 조건 반응형으로 순회하며 실행합니다.
    Step과 composition 관계입니다.
    """

    def __init__(self, config: dict, site_config: dict):
        self.max_iterations = config.get("max_iterations", 10)
        self.steps          = Step.from_config_list(config.get("steps", []))
        self.site_config    = site_config
        self.target_url     = site_config.get("url")
        self.site_name      = site_config.get("name", "unknown")

    async def run(self, page: Page, until_step_name: str = None) -> tuple[bool, Page]:
        """
        interaction을 실행합니다.

        Args:
            page: Playwright Page 객체
            until_step_name: 이 step 이름의 조건이 충족되면 actions 없이 중단
                             None이면 전체 실행

        Returns:
            (bool, Page): 성공 여부, 현재 page
        """
        current_page    = page
        iteration       = 0
        completed_steps = set()

        print(f"[{self.site_name}] 🔄 interaction 시작 (최대 {self.max_iterations}회, {len(self.steps)}개 step)")
        if until_step_name:
            print(f"[{self.site_name}] 🎯 중단 조건: '{until_step_name}' 조건 충족 시 중단")

        while iteration < self.max_iterations:
            iteration += 1
            print(f"\n[{self.site_name}] ─── iteration {iteration}/{self.max_iterations} ───")

            matched = False

            for step in self.steps:
                print(f"[{self.site_name}] 🔍 step 체크: '{step.name}'")

                # depends_on 체크 (대소문자 구분 없음)
                if not step.is_depends_met(completed_steps):
                    unmet = [d for d in step.depends_on if d.lower() not in {s.lower() for s in completed_steps}]
                    print(f"[{self.site_name}] ⏸ '{step.name}' 스킵 - 미완료 의존성: {unmet}")
                    continue

                # match_once 체크 - 이미 완료된 step이면 스킵
                if step.match_once and step.name in completed_steps:
                    print(f"[{self.site_name}] ⏭ '{step.name}' 스킵 (match_once - 이미 실행됨)")
                    continue

                # 조건 확인
                condition_result = await step.check_condition(current_page, self.site_name)
                print(f"[{self.site_name}] 📋 '{step.name}' 조건 결과: {condition_result}")
                if not condition_result:
                    continue

                # until_step_name 조건 충족 시 중단
                if until_step_name and step.name == until_step_name:
                    print(f"[{self.site_name}] 🎯 중단 조건 충족: '{step.name}' → 중단")
                    completed_steps.add(step.name)
                    return True, current_page

                # 조건 매칭 → actions 실행
                matched = True
                action_types = [a.__class__.__name__ for a in step.actions]
                print(f"[{self.site_name}] ✔ 매칭된 step: '{step.name}' actions: {action_types}")

                ok, current_page = await step.execute(current_page, self.site_config, self.site_name)
                if not ok:
                    print(f"[{self.site_name}] ❌ step 실패: '{step.name}'")
                    return False, current_page

                # URL 안정화 대기
                if step.actions:
                    await wait_for_url_stable(current_page, self.site_name)

                # step 완료 기록
                completed_steps.add(step.name)
                print(f"[{self.site_name}] 📝 완료된 steps: {completed_steps}")

                # final step 완료 시 종료
                if step.is_final:
                    print(f"[{self.site_name}] ✅ final step 완료: '{step.name}'")
                    return True, current_page

                # 한 iteration에서 하나의 step만 실행
                break

            if not matched:
                print(f"[{self.site_name}] ⚠️ 매칭되는 step 없음. 현재 URL: {current_page.url}")
                if self.target_url and current_page.url != self.target_url:
                    print(f"[{self.site_name}] 🔄 target URL로 복귀: {self.target_url}")
                    await current_page.goto(self.target_url)
                    await current_page.wait_for_load_state("networkidle")
                else:
                    print(f"[{self.site_name}] ❌ target URL에서도 매칭 없음. 중단합니다.")
                    return False, current_page

        print(f"[{self.site_name}] ⚠️ max_iterations({self.max_iterations}) 초과로 종료")
        return False, current_page

    @classmethod
    def from_site_config(cls, site_config: dict) -> Interaction | None:
        """
        site_config에 interactions가 있으면 Interaction 객체를 생성합니다.
        없으면 None을 반환합니다.
        """
        interaction_config = site_config.get("interactions")
        if not interaction_config:
            return None
        return cls(interaction_config, site_config)