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
from datetime import datetime
import json
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
            "login":                          LoginAction,
            "click":                          ClickAction,
            "wait":                           WaitAction,
            "download":                       DownloadAction,
            "individual_download":            IndividualDownloadAction,
            "read_table":                     ReadTableAction,
            "read_text":                      ReadTextAction,
            "read_title":                     ReadTitleAction,
            "goto":                           GotoAction,
            "jump":                           JumpAction,
            "lookup_comment_attachment":      LookupCommentAttachmentAction,
            "close_popup":                    ClosePopupAction,
            "download_comment_attachments":   DownloadCommentAttachmentsAction,
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
        ok = await do_login(page, login_config, site_name, site_config)
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
                site_config["_parent_page"] = page
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
# Goto
# =============================================================================

class GotoAction(Action):
    """
    특정 URL로 이동합니다.

    config:
        url: 이동할 URL (필수)
             "site" 입력 시 site_config의 url로 이동
    """

    async def execute(self, page: Page, site_config: dict, site_name: str) -> tuple[bool, Page]:
        url = self.config.get("url")

        if not url:
            print(f"[{site_name}] ⚠️ goto action에 url이 없습니다.")
            return False, page

        # "site" 키워드이면 site_config의 url 사용
        if url == "site":
            url = site_config.get("url")
            if not url:
                print(f"[{site_name}] ⚠️ site_config에 url이 없습니다.")
                return False, page

        try:
            print(f"[{site_name}] 🔀 goto: '{url}'")
            await page.goto(url)
            await page.wait_for_load_state("networkidle")
            print(f"[{site_name}] ✅ goto 완료: {page.url}")
            return True, page

        except Exception as e:
            print(f"[{site_name}] ⚠️ goto 실패: {e}")
            return False, page

class ReadTitleAction(Action):
    """
    특정 셀렉터의 텍스트를 읽어 result.title로 저장합니다.

    config:
        selector: 텍스트를 읽을 셀렉터 (필수)
        frame / frame_url: iframe 지정 (선택)
        timeout: 대기 시간 ms (기본값: 5000)
    """

    async def execute(self, page: Page, site_config: dict, site_name: str) -> tuple[bool, Page]:
        selector = self.config.get("selector")

        if not selector:
            print(f"[{site_name}] ⚠️ read_title action에 selector가 없습니다.")
            return False, page

        target = await self._resolve_frame(page, site_name)
        if target is None:
            return False, page

        try:
            await target.wait_for_selector(selector, timeout=self.timeout)

            tag = await target.evaluate(
                "(selector) => document.querySelector(selector)?.tagName.toLowerCase()",
                selector
            )

            if tag in ("input", "textarea"):
                text = await target.input_value(selector)
            else:
                text = await target.inner_text(selector)

            text = text.strip()

            site_config["_title"] = text
            print(f"[{site_name}] 📝 title 읽기 완료: '{selector}' → '{text[:50]}'")
            return True, page

        except PlaywrightTimeoutError:
            print(f"[{site_name}] ⚠️ 셀렉터 대기 timeout: '{selector}'")
            return False, page

# =============================================================================
# ReadText
# =============================================================================

class ReadTextAction(Action):
    """
    특정 셀렉터의 텍스트를 읽습니다.
    테이블 구조와 무관하게 selector로 직접 텍스트를 가져옵니다.

    config:
        selector: 텍스트를 읽을 셀렉터 (필수)
        name: 결과 dict에 저장될 key (선택, 미지정 시 selector를 key로 사용)
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

            # input/textarea는 input_value(), 나머지는 inner_text()로 읽기
            tag = await target.evaluate("""
                (selector) => document.querySelector(selector)?.tagName.toLowerCase()
            """, selector)

            if tag in ("input", "textarea"):
                text = await target.input_value(selector)
            else:
                text = await target.inner_text(selector)

            text = text.strip()

            # 결과를 site_config에 저장 (name 지정 시 name을 key로, 없으면 selector를 key로)
            key = self.config.get("name") or selector
            if "_text_results" not in site_config:
                site_config["_text_results"] = {}
            site_config["_text_results"][key] = text

            print(f"[{site_name}] 📝 텍스트 읽기 완료: '{selector}' → '{text[:50]}'")
            return True, page

        except PlaywrightTimeoutError:
            print(f"[{site_name}] ⚠️ 셀렉터 대기 timeout: '{selector}'")
            return False, page



# =============================================================================
# Jump
# =============================================================================

class JumpAction(Action):
    """
    특정 step으로 점프합니다.
    site_config["_jump_to"]에 step 이름을 저장하고
    Interaction.run()에서 해당 step으로 이동합니다.

    config:
        step: 이동할 step 이름 (필수)
    """

    async def execute(self, page: Page, site_config: dict, site_name: str) -> tuple[bool, Page]:
        step_name = self.config.get("step")

        if not step_name:
            print(f"[{site_name}] ⚠️ jump action에 step이 없습니다.")
            return False, page

        print(f"[{site_name}] 🔀 jump → '{step_name}'")
        site_config["_jump_to"] = step_name
        return True, page


# =============================================================================
# ClosePopup
# =============================================================================

class ClosePopupAction(Action):
    """
    현재 팝업 페이지를 닫고 부모 페이지로 돌아옵니다.
    ClickAction의 expect_popup: true로 열린 팝업에서만 사용합니다.

    config:
        timeout: ms (기본값: 5000)
    """

    async def execute(self, page: Page, site_config: dict, site_name: str) -> tuple[bool, Page]:
        parent_page = site_config.pop("_parent_page", None)

        if parent_page is None:
            print(f"[{site_name}] ⚠️ close_popup: 저장된 부모 페이지가 없습니다.")
            return False, page

        try:
            await page.close()
            print(f"[{site_name}] ✅ 팝업 닫기 완료 → 부모 페이지로 복귀")
            await parent_page.bring_to_front()
            return True, parent_page
        except Exception as e:
            print(f"[{site_name}] ⚠️ 팝업 닫기 실패: {e}")
            return False, page


# =============================================================================
# LookupCommentAttachment
# =============================================================================

class LookupCommentAttachmentAction(Action):
    """
    selector(ul)의 각 li를 순회하며 sub_selectors에 해당하는 하위 요소를 수집합니다.
    결과는 site_config["_comment_attachment_items"]에 저장됩니다.

    config:
        selector:     ul 셀렉터 (필수)
        sub_selectors: 각 li에서 수집할 하위 셀렉터 목록 (필수)
        timeout:      ms (기본값: 5000)
        frame / frame_url: iframe 지정 (선택)

    결과 구조 (_comment_attachment_items):
        [
            {
                "index": 0,
                "text": "li 전체 텍스트",
                "sub_elements": {
                    "span.attachLink": [
                        {"text": "파일명.pdf", "href": null, "onclick": "fn()"}
                    ],
                    ...
                }
            },
            ...
        ]
    """

    async def execute(self, page: Page, site_config: dict, site_name: str) -> tuple[bool, Page]:
        selector      = self.config.get("selector")
        sub_selectors = self.config.get("sub_selectors", [])
        if isinstance(sub_selectors, str):
            sub_selectors = [sub_selectors]

        if not selector:
            print(f"[{site_name}] ⚠️ lookup_comment_attachment action에 selector가 없습니다.")
            return False, page

        if not sub_selectors:
            print(f"[{site_name}] ⚠️ lookup_comment_attachment action에 sub_selectors가 없습니다.")
            return False, page

        target = await self._resolve_frame(page, site_name)
        if target is None:
            return False, page

        try:
            await target.wait_for_selector(selector, timeout=self.timeout)
        except PlaywrightTimeoutError:
            print(f"[{site_name}] ⚠️ 셀렉터 대기 timeout: '{selector}'")
            return False, page

        items = await self._collect_items(target, selector, sub_selectors)
        print(f"[{site_name}] 🔎 evaluate 완료: items={len(items)}")

        attachment_iframe_selector = self.config.get("attachment_iframe_selector")
        print(f"[{site_name}] 🔎 attachment_iframe_selector='{attachment_iframe_selector}', items={len(items)}")
        if attachment_iframe_selector:
            await self._enrich_with_urls(target, items, selector, attachment_iframe_selector, site_name)

        print(f"[{site_name}] 🔍 lookup_comment_attachment: {len(items)}개 항목 수집")
        for item in items:
            print(f"  [{item['index']}] {item['text'][:60]}")
            for sel, els in item["sub_elements"].items():
                for el in els:
                    print(f"       {sel} → text='{el['text']}' href={el['href']} onclick={el['onclick']} url={el.get('url')}")

        base_dir  = Path(site_config.get("download_dir", "./downloads"))
        ca_dir    = base_dir / "CommentAttachment"
        ca_dir.mkdir(parents=True, exist_ok=True)
        meta_path = ca_dir / "comment_attachments_meta.json"

        existing_meta = self._load_existing_meta(meta_path)
        for item in items:
            for sel, els in item["sub_elements"].items():
                for el in els:
                    prev = existing_meta.get(el.get("url"), {})
                    el.setdefault("downloaded",    prev.get("downloaded", False))
                    el.setdefault("download_path", prev.get("download_path"))
                    el.setdefault("error",         prev.get("error"))

        self._save_meta(meta_path, items)
        print(f"[{site_name}] 💾 메타 저장: {meta_path}")

        site_config["_comment_attachment_items"] = items
        site_config["_comment_attachment_dir"]   = str(ca_dir)
        return True, page

    async def _collect_items(self, target, selector: str, sub_selectors: list) -> list[dict]:
        """ul > li 순회하며 sub_selectors에 해당하는 하위 요소를 수집합니다."""
        return await target.evaluate("""
            ([selector, subSelectors]) => {
                const ul = document.querySelector(selector);
                if (!ul) return [];

                const result = [];
                const lis = ul.querySelectorAll(':scope > li');

                lis.forEach((li, i) => {
                    const subElements = {};

                    subSelectors.forEach(sel => {
                        const found = Array.from(li.querySelectorAll(sel))
                            .map(el => ({
                                text:    el.innerText.trim(),
                                href:    el.getAttribute('href') || null,
                                onclick: el.getAttribute('onclick') || null,
                            }))
                            .filter(el => {
                                if (!el.text) return false;
                                // "(숫자)" 형식이면 숫자가 0 초과일 때만 포함
                                const m = el.text.match(/^\((\d+)\)$/);
                                if (m) return parseInt(m[1], 10) > 0;
                                return true;
                            });
                        if (found.length > 0) {
                            subElements[sel] = found;
                        }
                    });

                    // #commentAttachLink가 있을 때만 첨부파일이 있는 댓글로 간주
                    if (subElements['#commentAttachLink']) {
                        const noteEl = li.querySelector('#cafe-note-contents');
                        result.push({
                            index:        i,
                            text:         noteEl ? noteEl.innerText.trim() : li.innerText.trim(),
                            sub_elements: subElements,
                        });
                    }
                });

                return result;
            }
        """, [selector, sub_selectors])

    async def _enrich_with_urls(self, target, items: list[dict], selector: str,
                                attachment_iframe_selector: str, site_name: str) -> None:
        """각 sub_element를 클릭하고 attachment iframe의 src를 el_data["url"]에 추가합니다."""
        for item in items:
            li_locator = target.locator(f"{selector} > li").nth(item["index"])
            for sub_sel, els in item["sub_elements"].items():
                for j, el_data in enumerate(els):
                    label = f"[{item['index']}] {sub_sel}[{j}] '{el_data['text'][:30]}'"
                    print(f"[{site_name}] 🖱️ 클릭 시도: {label}")
                    try:
                        await li_locator.locator(sub_sel).nth(j).click(timeout=self.timeout)
                        print(f"[{site_name}]   ✅ 클릭 성공")
                    except PlaywrightTimeoutError:
                        el_data["url"] = None
                        print(f"[{site_name}]   ❌ 클릭 timeout — 셀렉터를 찾지 못했거나 클릭 불가")
                        continue
                    except Exception as e:
                        el_data["url"] = None
                        print(f"[{site_name}]   ❌ 클릭 오류: {e}")
                        continue

                    print(f"[{site_name}]   iframe 대기 중: '{attachment_iframe_selector}'")
                    try:
                        await target.wait_for_selector(attachment_iframe_selector, timeout=self.timeout)
                        src = await target.evaluate(
                            "(sel) => document.querySelector(sel)?.src || null",
                            attachment_iframe_selector
                        )
                        el_data["url"] = src
                        print(f"[{site_name}]   ✅ src='{src}'")
                    except PlaywrightTimeoutError:
                        el_data["url"] = None
                        print(f"[{site_name}]   ❌ iframe 대기 timeout — 클릭 후 '{attachment_iframe_selector}'가 나타나지 않음")

    def _load_existing_meta(self, meta_path: Path) -> dict:
        """기존 메타 파일에서 url → 다운로드 상태 매핑을 반환합니다."""
        if not meta_path.exists():
            return {}
        try:
            existing = json.loads(meta_path.read_text(encoding="utf-8"))
            result = {}
            for ex_item in existing.get("items", []):
                for sel, els in ex_item.get("sub_elements", {}).items():
                    for el in els:
                        key = el.get("url")
                        if key:
                            result[key] = {
                                "downloaded":    el.get("downloaded", False),
                                "download_path": el.get("download_path"),
                                "error":         el.get("error"),
                            }
            return result
        except Exception:
            return {}

    def _save_meta(self, meta_path: Path, items: list[dict]) -> None:
        """items를 comment_attachments_meta.json에 저장합니다."""
        meta = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "items":      items,
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


# =============================================================================
# DownloadCommentAttachments
# =============================================================================

class DownloadCommentAttachmentsAction(Action):
    """
    lookup_comment_attachment가 저장한 메타 파일을 읽어
    downloaded: false 항목을 순회하며 url로 이동 후 다운로드합니다.
    완료/실패 시 메타 파일을 업데이트합니다.

    config:
        download_selector: 각 url 페이지에서 클릭할 다운로드 버튼 셀렉터 (필수)
        timeout:           ms (기본값: 5000)
    """

    async def execute(self, page: Page, site_config: dict, site_name: str) -> tuple[bool, Page]:
        from .result import FileResult

        dl_selector       = self.config.get("selector")
        dn_table_selector = self.config.get("dn_table_selector")
        dn_frame          = self.config.get("dn_frame")
        dn_frame_url      = self.config.get("dn_frame_url")

        if not dl_selector:
            print(f"[{site_name}] ⚠️ download_comment_attachments에 selector가 없습니다.")
            return False, page
        if not dn_table_selector:
            print(f"[{site_name}] ⚠️ download_comment_attachments에 dn_table_selector가 없습니다.")
            return False, page

        ca_dir    = Path(site_config.get("_comment_attachment_dir", ""))
        meta_path = ca_dir / "comment_attachments_meta.json"

        if not meta_path.exists():
            print(f"[{site_name}] ⚠️ 메타 파일이 없습니다: {meta_path}")
            return False, page

        meta  = json.loads(meta_path.read_text(encoding="utf-8"))
        items = meta.get("items", [])

        # 미완료 항목 수집
        targets = [
            (item, sel, j, el)
            for item in items
            for sel, els in item.get("sub_elements", {}).items()
            for j, el in enumerate(els)
            if el.get("url") and not el.get("downloaded")
        ]

        if not targets:
            print(f"[{site_name}] ✅ 다운로드할 항목이 없습니다 (모두 완료)")
            return True, page

        print(f"[{site_name}] 📥 download_comment_attachments: {len(targets)}건 다운로드 시작")

        for item, sel, j, el in targets:
            url   = el["url"]
            label = f"[{item['index']}] {sel}[{j}] '{el['text'][:30]}'"
            print(f"[{site_name}] 🔀 goto: {label} → {url}")

            try:
                await page.goto(url)
                await page.wait_for_load_state("networkidle")

                # IndividualDownloadAction과 동일한 로직 — frame 결정
                if dn_frame:
                    table_target = page.frame(name=dn_frame)
                    if not table_target:
                        raise RuntimeError(f"frame을 찾을 수 없습니다: name='{dn_frame}'")
                elif dn_frame_url:
                    table_target = page.frame(url=dn_frame_url)
                    if not table_target:
                        raise RuntimeError(f"frame을 찾을 수 없습니다: url='{dn_frame_url}'")
                else:
                    table_target = page

                dl_target = await self._resolve_frame(page, site_name)
                if dl_target is None:
                    raise RuntimeError("dl_target frame을 찾을 수 없습니다.")

                # tbody 행 순회 → (checkbox, filename) 수집
                rows           = await table_target.locator(f"{dn_table_selector} tr").all()
                download_items = []
                for i, row in enumerate(rows):
                    cells    = await row.locator("td, th").all()
                    checkbox = None
                    filename = ""
                    for k, cell in enumerate(cells):
                        cb = cell.locator("input[type='checkbox']")
                        if await cb.count() > 0:
                            checkbox = cb.first
                            if k + 1 < len(cells):
                                filename = (await cells[k + 1].inner_text()).strip()
                            break
                    if checkbox is None:
                        print(f"[{site_name}] {i+1}번째 행에 체크박스 없음 → 순회 종료")
                        break
                    download_items.append((checkbox, filename))

                if not download_items:
                    raise RuntimeError("다운로드 항목이 없습니다.")

                file_results = []
                for idx, (checkbox, filename) in enumerate(download_items):
                    print(f"[{site_name}] {idx+1}/{len(download_items)} 다운로드: '{filename}'")
                    file_result = FileResult(filename=filename)
                    try:
                        await checkbox.check(timeout=self.timeout)
                        async with page.expect_download(timeout=self.timeout) as dl_info:
                            await dl_target.click(dl_selector, timeout=self.timeout)
                        download   = await dl_info.value
                        item_dir   = ca_dir / str(item["index"])
                        item_dir.mkdir(parents=True, exist_ok=True)
                        save_path  = item_dir / download.suggested_filename
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

                el["downloaded"]    = all(f.success for f in file_results)
                el["download_path"] = [f.path for f in file_results if f.path]
                el["error"]         = None

            except PlaywrightTimeoutError:
                el["downloaded"] = False
                el["error"]      = "timeout"
                print(f"[{site_name}] ❌ timeout: {label}")

            except Exception as e:
                el["downloaded"] = False
                el["error"]      = str(e)
                print(f"[{site_name}] ❌ 오류: {label} → {e}")

            # 항목마다 메타 즉시 업데이트
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        success_count = sum(1 for _, _, _, el in targets if el.get("downloaded"))
        print(f"[{site_name}] 📥 완료: {success_count}/{len(targets)}건 성공")

        # 메타 파일 기준 최신 items로 덮어써야 downloader.py가 pop할 때 올바른 상태를 가져감
        site_config["_comment_attachment_items"] = items
        return True, page
