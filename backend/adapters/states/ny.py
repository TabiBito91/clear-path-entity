"""
New York Department of State — Entity Name Search Adapter
URL: https://apps.dos.ny.gov/publicInquiry/

The NY DOS public inquiry site is a JavaScript SPA. Playwright navigates
the page, selects "Entity Name" from the search-by dropdown, submits the
name, waits for results, then parses the results table.

Results table columns (left to right):
  0: Entity Name
  1: DOS ID
  2: County
  3: Jurisdiction
  4: Type
  5: Status
"""
import asyncio
import sys
from concurrent.futures import ThreadPoolExecutor

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from adapters.base import AdapterResult, BaseStateAdapter, EntityMatch

SEARCH_URL = "https://apps.dos.ny.gov/publicInquiry/"
NO_RESULTS_PHRASES = [
    "no entities",
    "no results",
    "not found",
    "0 results",
    "no records",
    "no matching",
]

_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="playwright-ny")


class NewYorkAdapter(BaseStateAdapter):
    state_code = "NY"
    state_name = "New York"

    async def search(self, name: str, entity_type: str) -> AdapterResult:
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

    def _search_sync(self, name: str, entity_type: str) -> AdapterResult:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            )
            page = context.new_page()
            try:
                # Use domcontentloaded then wait explicitly for SPA hydration
                page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=30_000)
                # Give the JS SPA time to render the search form
                page.wait_for_timeout(4_000)
                return self._fill_and_extract(page, name, entity_type)
            finally:
                browser.close()

    def _fill_and_extract(self, page, name: str, entity_type: str) -> AdapterResult:
        # Wait for the SPA to render — try specific NY DOS elements first,
        # then fall back to any input
        rendered = False
        for selector in ["select", "input[type='text']", "button:has-text('Search')", "input"]:
            try:
                page.wait_for_selector(selector, timeout=20_000)
                rendered = True
                break
            except PWTimeout:
                continue

        if not rendered:
            return AdapterResult(
                state_code=self.state_code,
                state_name=self.state_name,
                availability="error",
                confidence=0.1,
                notes="NY DOS site did not render search form in time.",
                extraction_method="failed",
            )

        # Select "Entity Name" from the search-by dropdown if present
        try:
            select = page.locator("select").first
            if select.count() > 0:
                select.select_option(label="Entity Name")
        except Exception:
            pass

        # Fill the name input — try multiple selector strategies
        filled = False
        for selector in ["input[type='text']", "input[name*='name' i]", "input[placeholder*='name' i]", "input"]:
            try:
                page.fill(selector, name)
                filled = True
                break
            except Exception:
                continue

        if not filled:
            return AdapterResult(
                state_code=self.state_code,
                state_name=self.state_name,
                availability="error",
                confidence=0.1,
                notes="Could not locate search input on NY DOS site.",
                extraction_method="failed",
            )

        # Submit the form — try button text, then submit input, then Enter
        submitted = False
        for approach in [
            lambda: page.click("button:has-text('Search')"),
            lambda: page.click("input[type='submit']"),
            lambda: page.click("button[type='submit']"),
            lambda: page.keyboard.press("Enter"),
        ]:
            try:
                approach()
                submitted = True
                break
            except Exception:
                continue

        if not submitted:
            return AdapterResult(
                state_code=self.state_code,
                state_name=self.state_name,
                availability="error",
                confidence=0.1,
                notes="Could not submit search form on NY DOS site.",
                extraction_method="failed",
            )

        # Wait for results to load
        try:
            page.wait_for_load_state("networkidle", timeout=20_000)
        except PWTimeout:
            pass  # parse whatever loaded

        return self._parse_results(page, name, entity_type)

    def _parse_results(self, page, name: str, entity_type: str) -> AdapterResult:
        page_text = page.inner_text("body").lower()

        if any(phrase in page_text for phrase in NO_RESULTS_PHRASES):
            return AdapterResult(
                state_code=self.state_code,
                state_name=self.state_name,
                availability="available",
                confidence=self._build_confidence("primary", "clear"),
                notes="No matching entities found in New York registry.",
                extraction_method="primary",
            )

        matches = self._parse_table(page)

        if not matches:
            return self._llm_fallback(page_text, name, entity_type)

        return self._classify(matches, name, entity_type)

    def _parse_table(self, page) -> list[EntityMatch]:
        """
        Parse the NY DOS results table.
        Columns: Entity Name | DOS ID | County | Jurisdiction | Type | Status
        """
        matches: list[EntityMatch] = []
        try:
            rows = page.locator("table tr").all()
            for row in rows:
                cells = row.locator("td").all()
                if len(cells) < 2:
                    continue

                entity_name = cells[0].inner_text().strip()
                dos_id = cells[1].inner_text().strip() if len(cells) > 1 else ""
                status = cells[5].inner_text().strip() if len(cells) > 5 else "unknown"

                # Skip header rows
                if not entity_name or entity_name.upper() in ("ENTITY NAME", "NAME"):
                    continue

                matches.append(EntityMatch(
                    name=entity_name,
                    entity_type=cells[4].inner_text().strip() if len(cells) > 4 else "",
                    status=status.lower() or "unknown",
                    file_number=dos_id,
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
