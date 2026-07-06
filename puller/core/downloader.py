"""
WebDownloader 모듈

UI와 Core의 단일 접점입니다.
UI는 WebDownloader만 알면 되고, Core는 UI를 전혀 모릅니다.

Browser는 WebDownloader 내부에 포함됩니다 (필수 요소).
"""

from __future__ import annotations
from pathlib import Path

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from .interaction import Interaction
from .result import (
    DownloadResult, FileResult,
    InspectResult,
    ScanResult,
    ReadTableResult, TableData,
)


class WebDownloader:
    """
    단일 진입점 클래스입니다.
    UI는 이 클래스만 사용합니다.

    download() → DownloadResult
    inspect()  → InspectResult
    scan()     → ScanResult
    """

    def __init__(self, site_config: dict, browser_config: dict = None):
        self.site_config    = site_config
        self.browser_config = browser_config or {}
        self.site_name      = site_config.get("name", "unknown")
        self.url            = site_config["url"]

        # param_values에서 첫 번째 입력값을 하위 폴더명으로 사용
        base_dir     = Path(site_config.get("download_dir", "./downloads"))
        param_values = site_config.get("_param_values", {})
        sub_folder   = next(iter(param_values.values()), None) if param_values else None

        if sub_folder:
            self.download_dir = base_dir / sub_folder
        else:
            self.download_dir = base_dir

        self.download_dir.mkdir(parents=True, exist_ok=True)

        # 계산된 download_dir을 site_config에 반영 (action에서 참조)
        self.site_config["download_dir"] = str(self.download_dir)

    # =========================================================================
    # Browser (내부)
    # =========================================================================

    async def _launch_browser(self, playwright, downloads_path: str = None):
        """브라우저를 실행합니다."""
        chromium_path = self.browser_config.get("chromium_path")
        headless      = self.browser_config.get("headless", False)

        kwargs = {
            "headless": headless,
            "args":     ["--window-position=-9999,-9999"] if headless else []
        }
        if chromium_path:
            kwargs["executable_path"] = chromium_path
        if downloads_path:
            kwargs["downloads_path"] = downloads_path

        return await playwright.chromium.launch(**kwargs)

    async def _new_context(self, browser: Browser) -> BrowserContext:
        """브라우저 컨텍스트를 생성합니다."""
        ignore_https_errors = self.browser_config.get("ignore_https_errors", False)
        user_agent          = self.browser_config.get("user_agent")

        kwargs = {
            "ignore_https_errors": ignore_https_errors,
            "accept_downloads":    True,
        }
        if user_agent:
            kwargs["user_agent"] = user_agent

        return await browser.new_context(**kwargs)

    async def _open_page(self, playwright, downloads_path: str = None) -> tuple[Page, BrowserContext, object]:
        """브라우저를 열고 페이지를 반환합니다."""
        browser = await self._launch_browser(playwright, downloads_path)
        context = await self._new_context(browser)
        page    = await context.new_page()
        await page.goto(self.url)
        await page.wait_for_load_state("networkidle")
        print(f"[{self.site_name}] 페이지 로딩 완료: {await page.title()}")
        return page, context, browser

    def _get_final_step_name(self) -> str | None:
        """첫 번째 final step 이름을 반환합니다. (스캔/탐색 목적)"""
        steps = self.site_config.get("interactions", {}).get("steps", [])
        for step in steps:
            if step.get("final"):
                return step.get("name")
        return None

    async def download(self, until_step_name: str = None) -> DownloadResult:
        """
        interaction을 실행하여 파일을 다운로드합니다.

        Args:
            until_step_name: 테스트용 - 이 step 조건 충족 시 중단 (None이면 전체 실행)

        Returns:
            DownloadResult
        """
        print(f"\n[{self.site_name}] 다운로드 시작: {self.url}")

        interaction = Interaction.from_site_config(self.site_config)
        if not interaction:
            return DownloadResult(success=False, error="interactions 설정이 없습니다.")

        # no_intercept 모드 확인
        steps        = self.site_config.get("interactions", {}).get("steps", [])
        no_intercept = any(
            action.get("no_intercept")
            for step in steps
            for action in step.get("actions", [])
            if action.get("type") == "download"
        )

        async with async_playwright() as p:
            downloads_path = str(self.download_dir) if no_intercept else None
            page, context, browser = await self._open_page(p, downloads_path)

            ok, _ = await interaction.run(page, until_step_name=until_step_name)

            # 개별 다운로드 결과 수집
            file_results              = self.site_config.pop("_file_results", [])
            comment_attachment_items  = self.site_config.pop("_comment_attachment_items", [])

            await context.close()
            await browser.close()

        if not ok:
            return DownloadResult(success=False, error="interaction 실패")

        if comment_attachment_items:
            print(f"\n[{self.site_name}] 📎 comment_attachment_items ({len(comment_attachment_items)}건):")
            for item in comment_attachment_items:
                print(f"  [{item['index']}] {item['text'][:80]}")
                for sel, els in item.get("sub_elements", {}).items():
                    for el in els:
                        print(f"       {sel} → text='{el['text']}' href={el['href']} onclick={el['onclick']}")

        return DownloadResult(success=True, files=file_results, comment_attachment_items=comment_attachment_items)

    async def inspect(self, until_step_name: str = None) -> InspectResult:
        """
        interaction을 실행하고 최종 페이지 상태를 반환합니다.

        Args:
            until_step_name: 이 step 조건 충족 시 중단 (None이면 final step까지)

        Returns:
            InspectResult
        """
        print(f"\n[{self.site_name}] 페이지 탐색: {self.url}")

        interaction = Interaction.from_site_config(self.site_config)

        # until_step_name 미지정 시 final step까지 자동 설정
        if until_step_name is None and interaction:
            until_step_name = self._get_final_step_name()

        async with async_playwright() as p:
            page, context, browser = await self._open_page(p)

            if interaction:
                ok, page = await interaction.run(page, until_step_name=until_step_name)
                if not ok:
                    await context.close()
                    await browser.close()
                    return InspectResult(success=False, error="interaction 실패")

            result = InspectResult(
                success     = True,
                title       = await page.title(),
                current_url = page.url,
            )

            await context.close()
            await browser.close()

        return result

    async def scan(self, until_step_name: str = None) -> ScanResult:
        """
        interaction 실행 후 페이지의 셀렉터를 스캔합니다.

        Args:
            until_step_name: 이 step 조건 충족 시 중단 (None이면 final step까지)

        Returns:
            ScanResult
        """
        print(f"\n[{self.site_name}] 셀렉터 스캔: {self.url}")

        interaction = Interaction.from_site_config(self.site_config)

        # until_step_name 미지정 시 final step까지 자동 설정
        if until_step_name is None and interaction:
            until_step_name = self._get_final_step_name()

        async with async_playwright() as p:
            page, context, browser = await self._open_page(p)

            if interaction:
                ok, page = await interaction.run(page, until_step_name=until_step_name)
                if not ok:
                    await context.close()
                    await browser.close()
                    return ScanResult(success=False, error="interaction 실패")

            # 모든 프레임 스캔
            frames = [{"frame": page.main_frame, "frame_name": "main", "frame_url": page.url}]
            for frame in page.frames:
                if frame != page.main_frame:
                    frames.append({
                        "frame":      frame,
                        "frame_name": frame.name or "(no name)",
                        "frame_url":  frame.url
                    })

            inputs     = []
            buttons    = []
            links      = []
            tables     = []
            clickables = []

            for frame_info in frames:
                frame      = frame_info["frame"]
                frame_name = frame_info["frame_name"]
                frame_url  = frame_info["frame_url"]

                try:
                    # input
                    _inputs = await frame.evaluate("""
                        () => Array.from(document.querySelectorAll('input')).map(el => {
                            let label = '';
                            if (el.id) {
                                const labelEl = document.querySelector('label[for="' + el.id + '"]');
                                if (labelEl) label = labelEl.innerText.trim();
                            }
                            if (!label) {
                                const parent = el.closest('label');
                                if (parent) label = parent.innerText.trim();
                            }
                            return {
                                type: el.type || 'text',
                                id: el.id || '',
                                name: el.name || '',
                                placeholder: el.placeholder || '',
                                label: label,
                                checked: el.type === 'checkbox' || el.type === 'radio' ? el.checked : null,
                                selector: el.id ? '#' + el.id
                                    : el.name ? 'input[name="' + el.name + '"]'
                                    : 'input[type="' + (el.type || 'text') + '"]'
                            };
                        })
                    """)
                    for item in _inputs:
                        item["frame_name"] = frame_name
                        item["frame_url"]  = frame_url
                    inputs.extend(_inputs)

                    # button
                    _buttons = await frame.evaluate("""
                        () => Array.from(document.querySelectorAll('button, input[type="submit"], a[role="button"]')).map(el => ({
                            tag: el.tagName.toLowerCase(),
                            type: el.type || '',
                            id: el.id || '',
                            text: (el.innerText || el.value || '').trim().slice(0, 50),
                            selector: el.id ? '#' + el.id
                                : el.type ? el.tagName.toLowerCase() + '[type="' + el.type + '"]'
                                : el.tagName.toLowerCase()
                        }))
                    """)
                    for item in _buttons:
                        item["frame_name"] = frame_name
                        item["frame_url"]  = frame_url
                    buttons.extend(_buttons)

                    # link
                    _links = await frame.evaluate(r"""
                        () => Array.from(document.querySelectorAll('a[href], a[download]')).filter(el => {
                            const href = el.href || '';
                            const text = el.innerText || '';
                            return el.hasAttribute('download')
                                || /\.(pdf|xlsx?|csv|zip|docx?|hwp|txt)(\?|$)/i.test(href)
                                || /(다운로드|download|저장|export)/i.test(text);
                        }).map(el => ({
                            text: (el.innerText || '').trim().slice(0, 50),
                            href: el.href || '',
                            selector: el.id ? '#' + el.id
                                : 'a:has-text("' + (el.innerText || '').trim().slice(0, 20) + '")'
                        }))
                    """)
                    for item in _links:
                        item["frame_name"] = frame_name
                        item["frame_url"]  = frame_url
                    links.extend(_links)

                    # data-table
                    _tables = await frame.evaluate("""
                        () => Array.from(document.querySelectorAll('table[data-table]')).map(el => ({
                            data_table: el.getAttribute('data-table'),
                            id: el.id || '',
                            headers: Array.from(el.querySelectorAll('th')).map(th => th.innerText.trim()).filter(Boolean),
                            row_count: el.querySelectorAll('tbody tr').length,
                            selector: el.id ? '#' + el.id
                                : 'table[data-table="' + el.getAttribute('data-table') + '"]'
                        }))
                    """)
                    for item in _tables:
                        item["frame_name"] = frame_name
                        item["frame_url"]  = frame_url
                    tables.extend(_tables)

                    # clickable
                    _clickables = await frame.evaluate("""
                        () => Array.from(document.querySelectorAll('*')).filter(el => {
                            const style = window.getComputedStyle(el);
                            const tag = el.tagName.toLowerCase();
                            if (['input', 'button', 'select', 'textarea'].includes(tag)) return false;
                            return style.cursor === 'pointer'
                                || el.hasAttribute('onclick')
                                || el.hasAttribute('ng-click')
                                || el.hasAttribute('@click');
                        }).map(el => ({
                            tag: el.tagName.toLowerCase(),
                            id: el.id || '',
                            text: (el.innerText || '').trim().slice(0, 50),
                            class: el.className ? el.className.toString().trim().split(' ')[0] : '',
                            selector: el.id ? '#' + el.id
                                : el.className
                                    ? el.tagName.toLowerCase() + '.' + el.className.toString().trim().split(' ')[0]
                                    : el.tagName.toLowerCase()
                        })).slice(0, 50)
                    """)
                    for item in _clickables:
                        item["frame_name"] = frame_name
                        item["frame_url"]  = frame_url
                    clickables.extend(_clickables)

                except Exception as e:
                    print(f"[{self.site_name}] iframe 스캔 실패 ({frame_name}): {e}")
                    continue

            result = ScanResult(
                success     = True,
                title       = await page.title(),
                current_url = page.url,
                inputs      = inputs,
                buttons     = buttons,
                links       = links,
                tables      = tables,
                clickables  = clickables,
            )

            await context.close()
            await browser.close()

        return result

    async def final_result(self) -> FinalResult:
        """
        전체 interaction을 실행하고 final step의 모든 결과를 수집합니다.
        download, read_text, read_table 결과를 모두 포함합니다.

        Returns:
            FinalResult
        """
        from .result import FinalResult

        print(f"\n[{self.site_name}] 통합 실행: {self.url}")

        interaction = Interaction.from_site_config(self.site_config)
        if not interaction:
            return FinalResult(success=False, error="interactions 설정이 없습니다.")

        steps        = self.site_config.get("interactions", {}).get("steps", [])
        no_intercept = any(
            action.get("no_intercept")
            for step in steps
            for action in step.get("actions", [])
            if action.get("type") == "download"
        )

        async with async_playwright() as p:
            downloads_path = str(self.download_dir) if no_intercept else None
            page, context, browser = await self._open_page(p, downloads_path)

            ok, current_page = await interaction.run(page, until_step_name=None)

            current_url = current_page.url if ok else ""
            title       = self.site_config.pop("_title", None) or (await current_page.title() if ok else "")

            # 모든 결과 수집
            file_results             = self.site_config.pop("_file_results", [])
            text_results             = self.site_config.pop("_text_results", {})
            table_results            = self.site_config.pop("_table_results", [])
            comment_attachment_items = self.site_config.pop("_comment_attachment_items", [])

            await context.close()
            await browser.close()

        if not ok:
            return FinalResult(success=False, error="interaction 실패")

        if comment_attachment_items:
            print(f"\n[{self.site_name}] 📎 comment_attachment_items ({len(comment_attachment_items)}건):")
            for item in comment_attachment_items:
                print(f"  [{item['index']}] {item['text'][:80]}")
                for sel, els in item.get("sub_elements", {}).items():
                    for el in els:
                        print(f"       {sel} → text='{el['text']}' href={el['href']} onclick={el['onclick']}")
        
        return FinalResult(
            success                  = True,
            title                    = title,
            current_url              = current_url,
            files                    = file_results,
            texts                    = text_results,
            tables                   = table_results,
            comment_attachment_items = comment_attachment_items,
        )

    async def lookup_comment_attachment(self, until_step_name: str = None) -> list[dict]:
        """
        interaction을 실행하고 lookup_comment_attachment action의 수집 결과를 반환합니다.

        Returns:
            list[dict] — _comment_attachment_items
        """
        print(f"\n[{self.site_name}] comment attachment 조회: {self.url}")

        interaction = Interaction.from_site_config(self.site_config)
        if not interaction:
            return []

        async with async_playwright() as p:
            page, context, browser = await self._open_page(p)

            ok, _ = await interaction.run(page, until_step_name=until_step_name)
            items = self.site_config.pop("_comment_attachment_items", [])

            await context.close()
            await browser.close()

        if not ok:
            return []

        return items
    
    async def read_text(self) -> ReadTextResult:
        """
        전체 interaction 실행 후 final step의 read_text action 결과를 반환합니다.

        Returns:
            ReadTextResult
        """
        from .result import ReadTextResult

        print(f"\n[{self.site_name}] 텍스트 읽기: {self.url}")

        interaction = Interaction.from_site_config(self.site_config)
        if not interaction:
            return ReadTextResult(success=False, error="interactions 설정이 없습니다.")

        # final step의 마지막 action이 read_text인지 확인
        steps = self.site_config.get("interactions", {}).get("steps", [])
        has_read_text = any(
            step.get("actions", []) and step.get("actions", [])[-1].get("type") == "read_text"
            for step in steps
            if step.get("final")
        )
        if not has_read_text:
            return ReadTextResult(success=False, error="final step의 마지막 action이 read_text가 아닙니다.")

        async with async_playwright() as p:
            page, context, browser = await self._open_page(p)

            ok, current_page = await interaction.run(page, until_step_name=None)

            title       = await current_page.title() if ok else ""
            current_url = current_page.url if ok else ""
            text_results = self.site_config.pop("_text_results", {})

            await context.close()
            await browser.close()

        if not ok:
            return ReadTextResult(success=False, error="interaction 실패")

        return ReadTextResult(
            success     = True,
            title       = title,
            current_url = current_url,
            texts       = text_results,
        )
        
    async def read_table(self) -> ReadTableResult:
        """
        전체 interaction 실행 후 final step의 read_table action 결과를 반환합니다.

        Returns:
            ReadTableResult
        """
        print(f"\n[{self.site_name}] 테이블 읽기: {self.url}")

        interaction = Interaction.from_site_config(self.site_config)
        if not interaction:
            return ReadTableResult(success=False, error="interactions 설정이 없습니다.")

        steps = self.site_config.get("interactions", {}).get("steps", [])
        has_read_table = any(
            step.get("actions", []) and step.get("actions", [])[-1].get("type") == "read_table"
            for step in steps
            if step.get("final")
        )
        if not has_read_table:
            return ReadTableResult(success=False, error="final step의 마지막 action이 read_table이 아닙니다.")

        async with async_playwright() as p:
            page, context, browser = await self._open_page(p)

            ok, current_page = await interaction.run(page, until_step_name=None)

            title       = await current_page.title() if ok else ""
            current_url = current_page.url if ok else ""
            table_results = self.site_config.pop("_table_results", [])

            await context.close()
            await browser.close()

        if not ok:
            return ReadTableResult(success=False, error="interaction 실패")

        return ReadTableResult(
            success     = True,
            title       = title,
            current_url = current_url,
            tables      = table_results,
        )
