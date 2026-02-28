"""
Delaware Division of Corporations — Entity Name Search Adapter
URL: https://icis.corp.delaware.gov/Ecorp/EntitySearch/NameSearch.aspx

Uses sync_playwright in a ThreadPoolExecutor to avoid asyncio subprocess
limitations on Windows (SelectorEventLoop does not support subprocess transport).
"""
import asyncio
import sys
from concurrent.futures import ThreadPoolExecutor

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from adapters.base import AdapterResult, BaseStateAdapter, EntityMatch

SEARCH_URL = "https://icis.corp.delaware.gov/Ecorp/EntitySearch/NameSearch.aspx"
NAME_INPUT = "#ctl00_ContentPlaceHolder1_frmEntityName"
SUBMIT_BUTTON = "input[type='submit']"
NO_RESULTS_TEXT = ["no entity", "no records", "not found", "0 records", "no results"]

_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="playwright")


class DelawareAdapter(BaseStateAdapter):
    state_code = "DE"
    state_name = "Delaware"

    async def search(self, name: str, entity_type: str) -> AdapterResult:
        """Async entry point — runs the sync Playwright search in a thread."""
        loop = asyncio.get_event_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(_executor, self._search_sync, name, entity_type),
                timeout=90,
            )
        except asyncio.TimeoutError:
            return AdapterResult(
                state_code=self.state_code,
                state_name=self.state_name,
                availability="error",
                confidence=0.1,
                notes="Search timed out after 90 seconds.",
            )
        except Exception as exc:
            return AdapterResult(
                state_code=self.state_code,
                state_name=self.state_name,
                availability="error",
                confidence=0.1,
                notes=f"Unexpected error: {type(exc).__name__}: {exc}",
            )

    # ------------------------------------------------------------------
    # Everything below is synchronous — runs inside the ThreadPoolExecutor
    # ------------------------------------------------------------------

    def _search_sync(self, name: str, entity_type: str) -> AdapterResult:
        # sync_playwright creates its own asyncio event loop internally via
        # asyncio.new_event_loop(). On Windows, the loop type is determined by
        # the active policy. We must set ProactorEventLoopPolicy here, inside
        # the worker thread/process, before sync_playwright initialises.
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                ],
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                extra_http_headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            page = context.new_page()
            try:
                # "commit" fires as soon as HTTP response headers arrive — much
                # earlier than "domcontentloaded", which can stall on slow ASPX
                # servers. 60s accommodates government server latency.
                page.goto(SEARCH_URL, wait_until="commit", timeout=60_000)
                return self._fill_and_extract(page, name, entity_type)
            finally:
                browser.close()

    def _fill_and_extract(self, page, name: str, entity_type: str) -> AdapterResult:
        try:
            page.wait_for_selector(NAME_INPUT, timeout=10_000)
        except PWTimeout:
            return AdapterResult(
                state_code=self.state_code,
                state_name=self.state_name,
                availability="error",
                confidence=0.1,
                notes="Name input field not found — Delaware site structure may have changed.",
                extraction_method="failed",
            )

        page.fill(NAME_INPUT, name)
        page.click(SUBMIT_BUTTON)

        try:
            page.wait_for_load_state("networkidle", timeout=20_000)
        except PWTimeout:
            pass  # parse whatever loaded

        return self._parse_results(page, name, entity_type)

    def _parse_results(self, page, name: str, entity_type: str) -> AdapterResult:
        page_text = page.inner_text("body").lower()

        # Delaware shows #tblResults when there are hits; its absence means no results.
        has_results_table = page.locator("#tblResults").count() > 0

        if not has_results_table or any(phrase in page_text for phrase in NO_RESULTS_TEXT):
            return AdapterResult(
                state_code=self.state_code,
                state_name=self.state_name,
                availability="available",
                confidence=self._build_confidence("primary", "clear"),
                notes="No matching entities found in Delaware registry.",
                extraction_method="primary",
            )

        matches = self._parse_table_rows(page)

        if not matches:
            return self._llm_fallback(page_text, name, entity_type)

        return self._classify(matches, name, entity_type)

    def _parse_table_rows(self, page) -> list[EntityMatch]:
        """
        Parse Delaware's #tblResults table.
        Structure: col 0 = FILE NUMBER, col 1 = ENTITY NAME (link text).
        Row 0 is the header; skip it.
        """
        matches: list[EntityMatch] = []
        try:
            rows = page.locator("#tblResults tr").all()
            for row in rows:
                cells = row.locator("td").all()
                if len(cells) < 2:
                    continue
                file_number = cells[0].inner_text().strip()
                entity_name = cells[1].inner_text().strip()

                # Skip the header row
                if entity_name.upper() == "ENTITY NAME" or file_number.upper() == "FILE NUMBER":
                    continue
                if not entity_name:
                    continue

                matches.append(EntityMatch(
                    name=entity_name,
                    entity_type="",   # not returned on the list page
                    status="unknown",
                    file_number=file_number,
                ))
        except Exception:
            pass
        return matches

    def _classify(self, matches: list[EntityMatch], search_name: str, entity_type: str) -> AdapterResult:
        name_upper = search_name.upper().strip()
        exact = [m for m in matches if m.name.upper().strip() == name_upper]

        if exact:
            return AdapterResult(
                state_code=self.state_code,
                state_name=self.state_name,
                availability="taken",
                confidence=self._build_confidence("primary", "clear"),
                raw_matches=[m.__dict__ for m in exact],
                similar_names=[m.name for m in matches if m not in exact],
                notes=f"Exact match found: '{exact[0].name}'",
            )

        similar = [m.name for m in matches]
        return AdapterResult(
            state_code=self.state_code,
            state_name=self.state_name,
            availability="similar",
            confidence=self._build_confidence("primary", "inferred"),
            raw_matches=[m.__dict__ for m in matches],
            similar_names=similar,
            notes=f"{len(similar)} similar name(s) found. No exact match. Review for deceptive similarity.",
        )

    def _llm_fallback(self, page_text: str, name: str, entity_type: str) -> AdapterResult:
        """Called only when deterministic extraction fails. Uses sync LLM client."""
        from llm.client import interpret_state_page_sync

        interpretation = interpret_state_page_sync(
            state_name=self.state_name,
            search_name=name,
            entity_type=entity_type,
            page_text=page_text[:3000],
        )

        return AdapterResult(
            state_code=self.state_code,
            state_name=self.state_name,
            availability=interpretation.get("availability", "unknown"),
            confidence=self._build_confidence("llm", interpretation.get("clarity", "ambiguous")),
            similar_names=interpretation.get("similar_names", []),
            notes=interpretation.get("notes", "Result interpreted via LLM fallback."),
            extraction_method="llm",
        )
