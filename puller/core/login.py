"""
로그인 유틸리티 모듈 (async 버전)
"""

import os
import asyncio
from dotenv import load_dotenv
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

load_dotenv()


async def wait_for_url_stable(
    page: Page,
    site_name: str,
    interval: float = 0.5,
    stable_duration: float = 1.5,
    timeout: float = 10.0
):
    """
    URL 변화가 멈출 때까지 대기합니다. (redirect 완료 감지용)
    """
    print(f"[{site_name}] ⏳ redirect 완료 대기 중...")
    stable_since = None
    last_url     = None
    start        = asyncio.get_event_loop().time()

    while True:
        current_url = page.url

        if last_url is None:
            last_url     = current_url
            stable_since = asyncio.get_event_loop().time()
        elif current_url != last_url:
            last_url     = current_url
            stable_since = asyncio.get_event_loop().time()
            print(f"[{site_name}] ↪️ URL 변경 감지: {current_url}")
        elif asyncio.get_event_loop().time() - stable_since >= stable_duration:
            print(f"[{site_name}] ✅ redirect 완료: {current_url}")
            break

        if asyncio.get_event_loop().time() - start >= timeout:
            print(f"[{site_name}] ⚠️ redirect 대기 timeout ({timeout}초)")
            break

        await asyncio.sleep(interval)


async def do_login(page: Page, login_config: dict, site_name: str) -> bool:
    """
    로그인을 수행합니다.
    로그인 후 URL이 안정될 때까지 대기합니다.

    Args:
        page: Playwright Page 객체
        login_config: config.yaml의 login 항목
        site_name: 로그 출력용 사이트 이름

    Returns:
        bool: 로그인 성공 여부
    """
    id_selector     = login_config.get("id_selector")
    pw_selector     = login_config.get("pw_selector")
    button_selector = login_config.get("button_selector")
    env_id_key      = login_config.get("env_id_key", "SITE_ID")
    env_pw_key      = login_config.get("env_pw_key", "SITE_PW")

    site_id = os.getenv(env_id_key)
    site_pw = os.getenv(env_pw_key)

    if not site_id or not site_pw:
        print(f"[{site_name}] ⚠️ 환경변수 {env_id_key} 또는 {env_pw_key}가 설정되지 않았습니다.")
        return False

    try:
        await page.wait_for_selector(id_selector, timeout=5000)
        await page.fill(id_selector, site_id)
        await page.fill(pw_selector, site_pw)
        await page.click(button_selector)
        await page.wait_for_load_state("networkidle")
        await wait_for_url_stable(page, site_name)

        print(f"[{site_name}] ✅ 로그인 완료: {page.url}")
        return True

    except PlaywrightTimeoutError:
        print(f"[{site_name}] ⚠️ 로그인 셀렉터를 찾을 수 없습니다.")
        return False