"""
Action 클래스 모듈

각 action 타입별 구현체입니다.
Action은 Step 안에서만 존재하며 (composition),
Step 외부에서 독립적으로 사용되지 않습니다.

action types:
    - LoginAction
    - ClickAction
    - WaitAction
    - DownloadAction
        └─ IndividualDownloadAction
    - ReadTableAction
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from pathlib import Path

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError


# =============================================================================
# Base
# =============================================================================

class Action(ABC):
    """Action 추상 기반 클래스"""

    def __init__(self, config: dict):
        self.config  = config
        self.timeout = config.get("timeout", 5000)

    @abstractmethod
    async def execute(self, page: Page, site_config: dict, site_name: str) -> tuple[bool, Page]:
        """
        action을 실행합니다.
        Returns: (성공 여부, 현재 page)
        """
        pass

    async def _resolve_frame(self, page: Page, site_name: str):
        """
        config의 frame/frame_url 옵션에 따라 실행할 프레임을 반환합니다.
        미지정 시 page 반환
        """
        frame_name = self.config.get("frame")
        frame_url  = self.config.get("frame_url")

        if frame_name:
            frame = page.frame(name=frame_name)
            if not frame:
                print(f"[{site_name}] ⚠️ frame을 찾을 수 없습니다: name='{frame_name}'")
                return None
            return frame

        if frame_url:
            frame = page.frame(url=frame_url)
            if not frame:
                print(f"[{site_name}] ⚠️ frame을 찾을 수 없습니다: url='{frame_url}'")
                return None
            return frame

        return page

    @classmethod
    def from_config(cls, config: dict) -> Action:
        """
        config의 type에 따라 적절한 Action 구현체를 반환합니다.
        """
        action_type = config.get("type")
        action_map  = {
            "login":       LoginAction,
            "click":       ClickAction,
            "wait":        WaitAction,
            "download":    DownloadAction,
            "read_table":  ReadTableAction,
            "read_text":   ReadTextAction,
        }
        klass = action_map.get(action_type)
        if not klass:
            raise ValueError(f"알 수 없는 action type: '{action_type}'")
        return klass(config)


# =============================================================================
# Login
# =============================================================================

class LoginAction(Action):
    """ID/PW 입력 후 로그인"""

    async def execute(self, page: Page, site_config: dict, site_name: str) -> tuple[bool, Page]:
        from .login import do_login
        login_config = site_config.get("login")
        if not login_config:
            print(f"[{site_name}] ⚠️ login 설정이 없습니다.")
            return False, page
        ok = await do_login(page, login_config, site_name)
        return ok, page


# =============================================================================
# Click
# =============================================================================

class ClickAction(Action):
    """특정 셀렉터 클릭"""

    async def execute(self, page: Page, site_config: dict, site_name: str) -> tuple[bool, Page]:
        selector     = self.config.get("selector")
        expect_popup = self.config.get("expect_popup", False)
        js_click     = self.config.get("js_click", False)

        if not selector:
            print(f"[{site_name}] ⚠️ click action에 selector가 없습니다.")
            return False, page

        target = await self._resolve_frame(page, site_name)
        if target is None:
            return False, page

        try:
            await target.wait_for_selector(selector, timeout=self.timeout)

            if js_click:
                await target.evaluate("""
                    (selector) => {
                        const el = document.querySelector(selector);
                        if (el) {
                            if (el.onclick) el.onclick();
                            else el.click();
                        }
                    }
                """, selector)
                await page.wait_for_load_state("networkidle")
                print(f"[{site_name}] 🖱️ JS 클릭 완료: '{selector}'")
                return True, page

            elif expect_popup:
                async with page.expect_popup(timeout=self.timeout) as popup_info:
                    await target.click(selector, timeout=self.timeout)
                popup_page = await popup_info.value
                await popup_page.wait_for_load_state("networkidle")
                print(f"[{site_name}] 🖱️ 클릭 완료 + 팝업 전환: '{selector}'")
                return True, popup_page

            else:
                await target.click(selector, timeout=self.timeout)
                await page.wait_for_load_state("networkidle")
                print(f"[{site_name}] 🖱️ 클릭 완료: '{selector}'")
                return True, page

        except PlaywrightTimeoutError:
            print(f"[{site_name}] ⚠️ 클릭 실패 - 셀렉터를 찾을 수 없습니다: '{selector}'")
            return False, page


# =============================================================================
# Wait
# =============================================================================

class WaitAction(Action):
    """대기 - selector 대기 또는 delay"""

    async def execute(self, page: Page, site_config: dict, site_name: str) -> tuple[bool, Page]:
        selector = self.config.get("selector")
        delay    = self.config.get("delay")

        if delay is not None:
            import asyncio
            print(f"[{site_name}] ⏳ {delay}ms 대기 중...")
            await asyncio.sleep(int(delay) / 1000)
            print(f"[{site_name}] ⏳ 대기 완료")

        if selector:
            target = await self._resolve_frame(page, site_name)
            if target is None:
                return False, page
            try:
                await target.wait_for_selector(selector, timeout=self.timeout)
                print(f"[{site_name}] ⏳ 셀렉터 대기 완료: '{selector}'")
            except PlaywrightTimeoutError:
                print(f"[{site_name}] ⚠️ 대기 timeout: '{selector}'")
                return False, page

        return True, page


# =============================================================================
# Download
# =============================================================================

class DownloadAction(Action):
    """파일 다운로드"""

    async def execute(self, page: Page, site_config: dict, site_name: str) -> tuple[bool, Page]:
        # individual_download 분기
        if self.config.get("individual_download", False):
            return await IndividualDownloadAction(self.config).execute(page, site_config, site_name)

        selector     = self.config.get("selector")
        no_intercept = self.config.get("no_intercept", False)
        wait_after   = self.config.get("wait_after")
        download_dir = Path(site_config.get("download_dir", "./downloads"))
        download_dir.mkdir(parents=True, exist_ok=True)

        if not selector:
            print(f"[{site_name}] ⚠️ download action에 selector가 없습니다.")
            return False, page

        target = await self._resolve_frame(page, site_name)
        if target is None:
            return False, page

        try:
            print(f"[{site_name}] 🖱️ 다운로드 클릭 시도: '{selector}'")

            if no_intercept:
                await target.click(selector, timeout=self.timeout)
                await page.wait_for_load_state("networkidle")
                print(f"[{site_name}] 🖱️ 클릭 완료 (no_intercept) - 브라우저가 직접 저장")
                return True, page

            elif wait_after:
                downloads = []
                page.on("download", lambda d: downloads.append(d))
                await target.click(selector, timeout=self.timeout)

                import asyncio
                print(f"[{site_name}] ⏳ {wait_after}ms 동안 다운로드 이벤트 수집 중...")
                await asyncio.sleep(int(wait_after) / 1000)

                if not downloads:
                    print(f"[{site_name}] ⚠️ 다운로드 이벤트 미감지")
                    return False, page

                print(f"[{site_name}] ✅ 총 {len(downloads)}개 다운로드 이벤트 감지!")
                for download in downloads:
                    save_path = download_dir / download.suggested_filename
                    await download.save_as(save_path)
                    print(f"[{site_name}] ✅ 저장 완료: {save_path}")
                return True, page

            else:
                async with page.expect_download(timeout=self.timeout) as download_info:
                    await target.click(selector, timeout=self.timeout)

                download = await download_info.value
                print(f"[{site_name}] ✅ 다운로드 이벤트 감지!")
                print(f"[{site_name}]    파일명: {download.suggested_filename}")
                print(f"[{site_name}]    URL: {download.url}")

                save_path = download_dir / download.suggested_filename
                await download.save_as(save_path)
                print(f"[{site_name}] ✅ 저장 완료: {save_path} ({save_path.stat().st_size / 1024:.1f} KB)")
                return True, page

        except PlaywrightTimeoutError:
            print(f"[{site_name}] ⚠️ 다운로드 이벤트 미감지: '{selector}'")
            return False, page


class IndividualDownloadAction(Action):
    """개별 다운로드 - 체크박스 순회하며 하나씩 다운로드"""

    async def execute(self, page: Page, site_config: dict, site_name: str) -> tuple[bool, Page]:
        from .result import FileResult

        dn_table_selector = self.config.get("dn_table_selector")
        dl_selector       = self.config.get("selector")
        download_dir      = Path(site_config.get("download_dir", "./downloads"))
        download_dir.mkdir(parents=True, exist_ok=True)

        if not dn_table_selector:
            print(f"[{site_name}] ⚠️ dn_table_selector가 없습니다.")
            return False, page

        if not dl_selector:
            print(f"[{site_name}] ⚠️ selector가 없습니다.")
            return False, page

        # table frame 결정
        dn_frame     = self.config.get("dn_frame")
        dn_frame_url = self.config.get("dn_frame_url")

        if dn_frame:
            table_target = page.frame(name=dn_frame)
            if not table_target:
                print(f"[{site_name}] ⚠️ frame을 찾을 수 없습니다: name='{dn_frame}'")
                return False, page
        elif dn_frame_url:
            table_target = page.frame(url=dn_frame_url)
            if not table_target:
                print(f"[{site_name}] ⚠️ frame을 찾을 수 없습니다: url='{dn_frame_url}'")
                return False, page
        else:
            table_target = page

        dl_target = await self._resolve_frame(page, site_name)
        if dl_target is None:
            return False, page

        # tbody 행 순회 → (checkbox, filename) 수집
        rows           = await table_target.locator(f"{dn_table_selector} tr").all()
        download_items = []

        for i, row in enumerate(rows):
            cells    = await row.locator("td, th").all()
            checkbox = None
            filename = ""

            for j, cell in enumerate(cells):
                cb = cell.locator("input[type='checkbox']")
                if await cb.count() > 0:
                    checkbox = cb.first
                    if j + 1 < len(cells):
                        filename = (await cells[j + 1].inner_text()).strip()
                    break

            if checkbox is None:
                print(f"[{site_name}] {i+1}번째 행에 체크박스 없음 → 순회 종료")
                break

            download_items.append((checkbox, filename))

        if not download_items:
            print(f"[{site_name}] ⚠️ 다운로드 항목이 없습니다.")
            return False, page

        total = len(download_items)
        print(f"[{site_name}] 총 {total}개 항목 개별 다운로드 시작")

        file_results = []
        for i, (checkbox, filename) in enumerate(download_items):
            print(f"\n[{site_name}] {i+1}/{total} 다운로드: '{filename}'")
            file_result = FileResult(filename=filename)

            try:
                await checkbox.check(timeout=self.timeout)

                async with page.expect_download(timeout=self.timeout) as download_info:
                    await dl_target.click(dl_selector, timeout=self.timeout)

                download = await download_info.value
                save_path = download_dir / download.suggested_filename
                await download.save_as(save_path)
                file_result.path   = str(save_path)
                file_result.status = "success"
                print(f"[{site_name}] ✅ 저장 완료: {save_path} ({save_path.stat().st_size / 1024:.1f} KB)")

                await checkbox.uncheck(timeout=self.timeout)

            except PlaywrightTimeoutError:
                print(f"[{site_name}] ⚠️ '{filename}' 다운로드 실패")
                try:
                    await checkbox.uncheck(timeout=self.timeout)
                except Exception:
                    pass

            file_results.append(file_result)

        # 결과를 site_config에 저장 (WebDownloader에서 수집)
        site_config["_file_results"] = file_results

        print(f"\n[{site_name}] ✅ 개별 다운로드 완료")
        return True, page


# =============================================================================
# ReadTable
# =============================================================================

class ReadTableAction(Action):
    """테이블 내용 읽기"""

    async def execute(self, page: Page, site_config: dict, site_name: str) -> tuple[bool, Page]:
        from .result import TableData

        selector = self.config.get("selector")
        wait_for = self.config.get("wait_for")

        if not selector:
            print(f"[{site_name}] ⚠️ read_table action에 selector가 없습니다.")
            return False, page

        target = await self._resolve_frame(page, site_name)
        if target is None:
            return False, page

        try:
            await target.wait_for_selector(selector, timeout=self.timeout)

            if wait_for:
                print(f"[{site_name}] ⏳ 데이터 로딩 대기: '{wait_for}'")
                await target.wait_for_selector(wait_for, timeout=self.timeout)
                print(f"[{site_name}] ⏳ 데이터 로딩 완료")

            table_data = await target.evaluate("""
                (selector) => {
                    const el = document.querySelector(selector);
                    if (!el) return null;

                    const getCellValue = (cell) => {
                        const checkbox = cell.querySelector('input[type="checkbox"]');
                        if (checkbox) return checkbox.checked ? 'checked' : 'unchecked';
                        return cell.innerText.trim();
                    };

                    let headers = [];
                    let rows    = [];

                    if (el.tagName.toLowerCase() === 'tbody') {
                        rows = Array.from(el.querySelectorAll('tr')).map(row =>
                            Array.from(row.querySelectorAll('td, th')).map(getCellValue)
                        );
                        return { headers, rows };
                    }

                    const thead = el.querySelector('thead');
                    const tbody = el.querySelector('tbody');

                    if (thead) {
                        headers = Array.from(thead.querySelectorAll('th, td')).map(el => el.innerText.trim());
                    }

                    if (tbody) {
                        rows = Array.from(tbody.querySelectorAll('tr')).map(row =>
                            Array.from(row.querySelectorAll('td, th')).map(getCellValue)
                        );
                    } else {
                        const allRows = Array.from(el.querySelectorAll('tr'));
                        if (thead) {
                            rows = allRows.filter(r => !thead.contains(r)).map(row =>
                                Array.from(row.querySelectorAll('td, th')).map(getCellValue)
                            );
                        } else {
                            if (allRows.length > 0) {
                                headers = Array.from(allRows[0].querySelectorAll('td, th')).map(el => el.innerText.trim());
                                rows    = allRows.slice(1).map(row =>
                                    Array.from(row.querySelectorAll('td, th')).map(getCellValue)
                                );
                            }
                        }
                    }

                    return { headers, rows };
                }
            """, selector)

            if not table_data:
                print(f"[{site_name}] ⚠️ 테이블을 찾을 수 없습니다: '{selector}'")
                return False, page

            # 행/열 범위 필터 적용
            table_data = self._filter(table_data)

            # 결과를 site_config에 저장 (WebDownloader에서 수집)
            if "_table_results" not in site_config:
                site_config["_table_results"] = []
            site_config["_table_results"].append(
                TableData(
                    selector=selector,
                    headers=table_data["headers"],
                    rows=table_data["rows"]
                )
            )

            print(f"[{site_name}] 📊 테이블 읽기 완료: '{selector}' ({len(table_data['rows'])}행)")
            return True, page

        except PlaywrightTimeoutError:
            print(f"[{site_name}] ⚠️ 셀렉터 대기 timeout: '{wait_for or selector}'")
            return False, page

    def _filter(self, table_data: dict) -> dict:
        """행/열 범위 및 skip_empty 필터 적용"""
        rows       = table_data.get("rows", [])
        headers    = table_data.get("headers", [])
        skip_empty = self.config.get("skip_empty", False)

        row = self.config.get("row")
        if row is not None:
            row_idx = int(row) - 1
            rows = [rows[row_idx]] if 0 <= row_idx < len(rows) else []

        col = self.config.get("col")
        if col is not None:
            col_idx   = int(col) - 1
            row_start = int(self.config.get("row_start", 1)) - 1
            rows      = [[r[col_idx]] for r in rows[row_start:] if col_idx < len(r)]
            headers   = [headers[col_idx]] if col_idx < len(headers) else []
        else:
            row_start = int(self.config.get("row_start", 1)) - 1
            rows      = rows[row_start:]
            col_start = int(self.config.get("col_start", 1)) - 1
            col_end   = self.config.get("col_end")
            if col_end is not None:
                col_end = int(col_end)
                rows    = [r[col_start:col_end] for r in rows]
                headers = headers[col_start:col_end]
            else:
                rows    = [r[col_start:] for r in rows]
                headers = headers[col_start:]

        if skip_empty:
            rows = [r for r in rows if any(cell.strip() for cell in r)]

        return {"headers": headers, "rows": rows}


# =============================================================================
# ReadText
# =============================================================================

class ReadTextAction(Action):
    """
    특정 셀렉터의 텍스트를 읽습니다.
    테이블 구조와 무관하게 selector로 직접 텍스트를 가져옵니다.

    config:
        selector: 텍스트를 읽을 셀렉터 (필수)
        frame / frame_url: iframe 지정 (선택)
        timeout: 대기 시간 ms (기본값: 5000)
    """

    async def execute(self, page: Page, site_config: dict, site_name: str) -> tuple[bool, Page]:
        selector = self.config.get("selector")

        if not selector:
            print(f"[{site_name}] ⚠️ read_text action에 selector가 없습니다.")
            return False, page

        target = await self._resolve_frame(page, site_name)
        if target is None:
            return False, page

        try:
            await target.wait_for_selector(selector, timeout=self.timeout)

            # textarea는 input_value(), 나머지는 inner_text()로 읽기
            tag = await target.evaluate("""
                (selector) => document.querySelector(selector)?.tagName.toLowerCase()
            """, selector)

            if tag == "textarea":
                text = await target.input_value(selector)
            else:
                text = await target.inner_text(selector)

            text = text.strip()

            # 결과를 site_config에 저장
            if "_text_results" not in site_config:
                site_config["_text_results"] = {}
            site_config["_text_results"][selector] = text

            print(f"[{site_name}] 📝 텍스트 읽기 완료: '{selector}' → '{text[:50]}'")
            return True, page

        except PlaywrightTimeoutError:
            print(f"[{site_name}] ⚠️ 셀렉터 대기 timeout: '{selector}'")
            return False, page