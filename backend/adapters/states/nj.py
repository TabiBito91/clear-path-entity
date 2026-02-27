"""
New Jersey Division of Revenue & Enterprise Services — Entity Name Search Adapter
URL: https://www.njportal.com/DOR/BusinessNameSearch/Search/BusinessName

Uses sync_playwright in a ThreadPoolExecutor (same pattern as Delaware adapter)
to avoid asyncio subprocess limitations.

NJ returns: Business Name, Entity ID, Business Type, Status, Date Incorporated.
The search is prefix-based — returns all entities starting with the entered name.
"""
import asyncio
import sys
from concurrent.futures import ThreadPoolExecutor

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from adapters.base import AdapterResult, BaseStateAdapter, EntityMatch

SEARCH_URL = "https://www.njportal.com/DOR/BusinessNameSearch/Search/BusinessName"

# Candidate selectors tried in order — NJ portal may use any of these
NAME_INPUT_SELECTORS = [
    "input[name='businessName']",
    "#businessName",
    "input[placeholder*='name' i]",
    "input[placeholder*='Business' i]",
    "input.form-control",
]
SUBMIT_SELECTORS = [
    "button[type='submit']",
    "input[type='submit']",
    "button:has-text('Search')",
    "a:has-text('Search')",
]
# Results table candidates
RESULTS_TABLE_SELECTORS = [
    "table.table",
    "#searchResults table",
    ".search-results table",
    "table",
]
NO_RESULTS_TEXT = [
    "no results", "no records", "not found", "no entities",
    "no businesses", "0 results", "0 records",
]

_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="playwright-nj")


class NewJerseyAdapter(BaseStateAdapter):
    state_code = "NJ"
    state_name = "New Jersey"

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
                notes="NJ search timed out after 90 seconds.",
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
    # Synchronous — runs inside ThreadPoolExecutor
    # ------------------------------------------------------------------

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
                page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=30_000)
                return self._fill_and_extract(page, name, entity_type)
            finally:
                browser.close()

    def _fill_and_extract(self, page, name: str, entity_type: str) -> AdapterResult:
        # Find the name input field
        input_sel = None
        for sel in NAME_INPUT_SELECTORS:
            try:
                page.wait_for_selector(sel, timeout=8_000)
                input_sel = sel
                break
            except PWTimeout:
                continue

        if not input_sel:
            return AdapterResult(
                state_code=self.state_code,
                state_name=self.state_name,
                availability="error",
                confidence=0.1,
                notes="NJ search form not found — site structure may have changed.",
                extraction_method="failed",
            )

        page.fill(input_sel, name.strip())

        # Click submit
        submitted = False
        for sel in SUBMIT_SELECTORS:
            try:
                page.click(sel, timeout=5_000)
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
                notes="NJ search submit button not found.",
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

        if any(phrase in page_text for phrase in NO_RESULTS_TEXT):
            return AdapterResult(
                state_code=self.state_code,
                state_name=self.state_name,
                availability="available",
                confidence=self._build_confidence("primary", "clear"),
                notes="No matching entities found in New Jersey registry.",
            )

        matches = self._parse_table(page)
        if not matches:
            # No table found but no explicit "no results" either — use LLM fallback
            return self._llm_fallback(page_text, name, entity_type)

        return self._classify(matches, name)

    def _parse_table(self, page) -> list[EntityMatch]:
        """
        Parse NJ results table.
        Expected columns: Business Name | Entity ID | Business Type | Status | Date
        Tries multiple table selectors in case of markup changes.
        """
        matches: list[EntityMatch] = []

        for table_sel in RESULTS_TABLE_SELECTORS:
            try:
                if page.locator(table_sel).count() == 0:
                    continue
                rows = page.locator(f"{table_sel} tr").all()
                if len(rows) < 2:
                    continue  # header only — no data rows

                # Detect column positions from header row
                header_cells = [
                    c.inner_text().strip().lower()
                    for c in rows[0].locator("th, td").all()
                ]
                col = _col_index(header_cells)

                for row in rows[1:]:
                    cells = row.locator("td").all()
                    if not cells:
                        continue
                    entity_name = _cell(cells, col.get("name", 0))
                    if not entity_name:
                        continue
                    matches.append(EntityMatch(
                        name=entity_name,
                        entity_type=_cell(cells, col.get("type", -1)),
                        status=_cell(cells, col.get("status", -1)) or "unknown",
                        file_number=_cell(cells, col.get("id", -1)),
                        registered=_cell(cells, col.get("date", -1)),
                    ))
                if matches:
                    break  # found a working table
            except Exception:
                continue

        return matches

    def _classify(self, matches: list[EntityMatch], search_name: str) -> AdapterResult:
        name_upper = search_name.strip().upper()
        exact = [m for m in matches if m.name.strip().upper() == name_upper]
        similar = [m for m in matches if m not in exact]

        if exact:
            return AdapterResult(
                state_code=self.state_code,
                state_name=self.state_name,
                availability="taken",
                confidence=self._build_confidence("primary", "clear"),
                raw_matches=[m.__dict__ for m in exact],
                similar_names=[m.name for m in similar],
                notes=f"Exact match found: '{exact[0].name}'",
            )

        return AdapterResult(
            state_code=self.state_code,
            state_name=self.state_name,
            availability="similar",
            confidence=self._build_confidence("primary", "inferred"),
            raw_matches=[m.__dict__ for m in matches],
            similar_names=[m.name for m in matches],
            notes=f"{len(matches)} similar name(s) found in NJ registry. No exact match.",
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _col_index(headers: list[str]) -> dict[str, int]:
    """Map semantic column names to their index from the header row."""
    mapping = {}
    for i, h in enumerate(headers):
        if any(k in h for k in ("business name", "entity name", "name")):
            mapping.setdefault("name", i)
        elif any(k in h for k in ("entity id", "entity number", "file", "id")):
            mapping.setdefault("id", i)
        elif any(k in h for k in ("type", "entity type", "business type")):
            mapping.setdefault("type", i)
        elif any(k in h for k in ("status",)):
            mapping.setdefault("status", i)
        elif any(k in h for k in ("date", "incorporated", "formed", "registered")):
            mapping.setdefault("date", i)
    return mapping


def _cell(cells, index: int) -> str:
    """Safely get text from a cell by index."""
    if index < 0 or index >= len(cells):
        return ""
    try:
        return cells[index].inner_text().strip()
    except Exception:
        return ""
