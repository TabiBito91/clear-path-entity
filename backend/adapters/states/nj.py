"""
New Jersey Division of Revenue & Enterprise Services — Entity Name Search Adapter
URL: https://www.njportal.com/DOR/BusinessNameSearch/Search/BusinessName

Uses async_playwright directly in the async event loop — no ThreadPoolExecutor needed.
This avoids the Windows "Racing with another loop to spawn a process" RuntimeError
that occurs when sync_playwright tries to spawn a subprocess from inside a thread
that shares asyncio state with the main event loop.

NJ returns: Business Name, Entity ID, Business Type, Status, Date Incorporated.
The search is prefix-based — returns all entities starting with the entered name.
"""
import asyncio

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

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


class NewJerseyAdapter(BaseStateAdapter):
    state_code = "NJ"
    state_name = "New Jersey"

    async def search(self, name: str, entity_type: str) -> AdapterResult:
        """Async entry point — runs async_playwright directly in the event loop."""
        try:
            return await asyncio.wait_for(
                self._run(name, entity_type),
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
    # Async helpers
    # ------------------------------------------------------------------

    async def _run(self, name: str, entity_type: str) -> AdapterResult:
        """Launch browser, navigate to search page, and return results."""
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            )
            page = await context.new_page()
            try:
                await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=30_000)
                return await self._fill_and_extract(page, name, entity_type)
            finally:
                await browser.close()

    async def _fill_and_extract(self, page, name: str, entity_type: str) -> AdapterResult:
        # Find the name input field
        input_sel = None
        for sel in NAME_INPUT_SELECTORS:
            try:
                await page.wait_for_selector(sel, timeout=8_000)
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

        await page.fill(input_sel, name.strip())

        # Click submit
        submitted = False
        for sel in SUBMIT_SELECTORS:
            try:
                await page.click(sel, timeout=5_000)
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
            await page.wait_for_load_state("networkidle", timeout=20_000)
        except PWTimeout:
            pass  # parse whatever loaded

        return await self._parse_results(page, name, entity_type)

    async def _parse_results(self, page, name: str, entity_type: str) -> AdapterResult:
        page_text = (await page.inner_text("body")).lower()

        if any(phrase in page_text for phrase in NO_RESULTS_TEXT):
            return AdapterResult(
                state_code=self.state_code,
                state_name=self.state_name,
                availability="available",
                confidence=self._build_confidence("primary", "clear"),
                notes="No matching entities found in New Jersey registry.",
            )

        matches = await self._parse_table(page)
        if not matches:
            # No table found but no explicit "no results" either — use LLM fallback
            return await self._llm_fallback(page_text, name, entity_type)

        return self._classify(matches, name)

    async def _parse_table(self, page) -> list[EntityMatch]:
        """
        Parse NJ results table.
        Expected columns: Business Name | Entity ID | Business Type | Status | Date
        Tries multiple table selectors in case of markup changes.
        """
        matches: list[EntityMatch] = []

        for table_sel in RESULTS_TABLE_SELECTORS:
            try:
                if await page.locator(table_sel).count() == 0:
                    continue
                rows = await page.locator(f"{table_sel} tr").all()
                if len(rows) < 2:
                    continue  # header only — no data rows

                # Detect column positions from header row
                header_loc = rows[0].locator("th, td")
                n_headers = await header_loc.count()
                header_texts = []
                for i in range(n_headers):
                    text = await header_loc.nth(i).inner_text()
                    header_texts.append(text.strip().lower())
                col = _col_index(header_texts)

                for row in rows[1:]:
                    cells_loc = row.locator("td")
                    cell_count = await cells_loc.count()
                    if cell_count == 0:
                        continue
                    cells = [cells_loc.nth(i) for i in range(cell_count)]
                    entity_name = await _cell(cells, col.get("name", 0))
                    if not entity_name:
                        continue
                    matches.append(EntityMatch(
                        name=entity_name,
                        entity_type=await _cell(cells, col.get("type", -1)),
                        status=await _cell(cells, col.get("status", -1)) or "unknown",
                        file_number=await _cell(cells, col.get("id", -1)),
                        registered=await _cell(cells, col.get("date", -1)),
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

    async def _llm_fallback(self, page_text: str, name: str, entity_type: str) -> AdapterResult:
        from llm.client import interpret_state_page

        interpretation = await interpret_state_page(
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


async def _cell(cells: list, index: int) -> str:
    """Safely get text from a cell by index."""
    if index < 0 or index >= len(cells):
        return ""
    try:
        text = await cells[index].inner_text()
        return text.strip()
    except Exception:
        return ""
