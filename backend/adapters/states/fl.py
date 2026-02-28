"""
Florida Division of Corporations (Sunbiz) — Entity Name Search Adapter
URL: https://search.sunbiz.org/Inquiry/CorporationSearch/ByName

Navigates to the Sunbiz search form, fills the entity name input, and submits.
The direct results URL approach was abandoned because Sunbiz requires session
cookies and a valid referer from the form page before it will return results —
without them it redirects back to the form, producing no table to parse.

FL returns all entity statuses (Active, INACT, CROSS RF, RPEND/UA, etc.).
Results table has 3 columns: Corporate Name | Document Number | Status.

An empty <tbody> means no results — handled without an LLM call.
Exact matches against inactive entities are flagged but still returned as "taken"
since name history matters; the user should verify availability with an attorney.
"""
import asyncio

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

from adapters.base import AdapterResult, BaseStateAdapter, EntityMatch

FORM_URL = "https://search.sunbiz.org/Inquiry/CorporationSearch/ByName"

# FL status codes that indicate the entity is no longer active
_INACTIVE_STATUSES = {
    "inact", "inactive", "cross rf", "dissolved", "revoked",
    "cancelled", "canceled", "merged", "converted", "withdrawn",
}

NO_RESULTS_TEXT = [
    "no filings", "no matching", "no records", "no results",
    "0 results", "not found",
]

TABLE_SELECTORS = [
    "table",
    "#search-results table",
    ".sr-voyager",
]


class FloridaAdapter(BaseStateAdapter):
    state_code = "FL"
    state_name = "Florida"

    async def search(self, name: str, entity_type: str) -> AdapterResult:
        """Async entry point — navigates directly to search results URL."""
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
                notes="FL search timed out after 90 seconds.",
            )
        except Exception as exc:
            return AdapterResult(
                state_code=self.state_code,
                state_name=self.state_name,
                availability="error",
                confidence=0.1,
                notes=f"Unexpected error: {type(exc).__name__}: {exc}",
            )

    async def _run(self, name: str, entity_type: str) -> AdapterResult:
        """Navigate to the Sunbiz form, fill it, submit, and parse results.

        The direct results URL was abandoned — Sunbiz requires session cookies
        and a valid Referer set from the form page, otherwise it silently
        redirects back to the blank form with no results table.
        """
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
                await page.goto(FORM_URL, wait_until="domcontentloaded", timeout=30_000)
                return await self._fill_and_extract(page, name, entity_type)
            finally:
                await browser.close()

    async def _fill_and_extract(self, page, name: str, entity_type: str) -> AdapterResult:
        """Fill the Sunbiz search form and submit."""
        # Name input candidates
        input_sel = None
        for sel in [
            "input[name='corporationNameSearchTerm']",
            "input#corporationName",
            "input[placeholder*='name' i]",
            "input[type='text']",
        ]:
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
                notes="FL search form not found — Sunbiz site structure may have changed.",
                extraction_method="failed",
            )

        await page.fill(input_sel, name.strip())

        # Submit the form
        submitted = False
        for sel in [
            "input[type='submit']",
            "button[type='submit']",
            "input[value='Search']",
            "button:has-text('Search')",
        ]:
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
                notes="FL search submit button not found.",
                extraction_method="failed",
            )

        try:
            await page.wait_for_load_state("networkidle", timeout=20_000)
        except PWTimeout:
            pass

        return await self._parse_results(page, name, entity_type)

    async def _parse_results(self, page, name: str, entity_type: str) -> AdapterResult:
        page_text = (await page.inner_text("body")).lower()

        # Explicit "no results" message from Sunbiz
        if any(phrase in page_text for phrase in NO_RESULTS_TEXT):
            return AdapterResult(
                state_code=self.state_code,
                state_name=self.state_name,
                availability="available",
                confidence=self._build_confidence("primary", "clear"),
                notes="No matching entities found in Florida registry.",
            )

        table_exists = await page.locator("table").count() > 0
        matches = await self._parse_table(page)

        if not matches:
            if table_exists:
                # Table rendered but tbody is empty — definitively no results
                return AdapterResult(
                    state_code=self.state_code,
                    state_name=self.state_name,
                    availability="available",
                    confidence=self._build_confidence("primary", "clear"),
                    notes="No matching entities found in Florida registry.",
                )
            # No table at all — unknown state, fall back to LLM
            return await self._llm_fallback(page_text, name, entity_type)

        return self._classify(matches, name)

    async def _parse_table(self, page) -> list[EntityMatch]:
        """
        Parse Sunbiz results table.
        Confirmed columns: Corporate Name (0) | Document Number (1) | Status (2)
        Entity name is wrapped in an <a> tag; inner_text() extracts it cleanly.
        """
        matches: list[EntityMatch] = []

        for table_sel in TABLE_SELECTORS:
            try:
                if await page.locator(table_sel).count() == 0:
                    continue
                rows = await page.locator(f"{table_sel} tbody tr").all()
                if not rows:
                    continue

                for row in rows:
                    cells_loc = row.locator("td")
                    cell_count = await cells_loc.count()
                    if cell_count < 2:
                        continue

                    cells = [cells_loc.nth(i) for i in range(cell_count)]
                    entity_name = (await cells[0].inner_text()).strip()
                    if not entity_name:
                        continue

                    doc_number = (await cells[1].inner_text()).strip() if cell_count > 1 else ""
                    status = (await cells[2].inner_text()).strip() if cell_count > 2 else "unknown"

                    matches.append(EntityMatch(
                        name=entity_name,
                        entity_type="",  # not shown in FL list view
                        status=status,
                        file_number=doc_number,
                        registered="",   # not shown in FL list view
                    ))

                if matches:
                    break
            except Exception:
                continue

        return matches

    def _classify(self, matches: list[EntityMatch], search_name: str) -> AdapterResult:
        name_upper = search_name.strip().upper()
        exact = [m for m in matches if m.name.strip().upper() == name_upper]
        similar = [m for m in matches if m not in exact]

        if exact:
            active_exact = [m for m in exact if m.status.lower() not in _INACTIVE_STATUSES]
            note = f"Exact match found: '{exact[0].name}' (status: {exact[0].status})."
            if not active_exact:
                note += (
                    " All exact matches are inactive —"
                    " name may be available, but verify with an attorney."
                )
            return AdapterResult(
                state_code=self.state_code,
                state_name=self.state_name,
                availability="taken",
                confidence=self._build_confidence("primary", "clear"),
                raw_matches=[m.__dict__ for m in exact],
                similar_names=[m.name for m in similar],
                notes=note,
            )

        return AdapterResult(
            state_code=self.state_code,
            state_name=self.state_name,
            availability="similar",
            confidence=self._build_confidence("primary", "inferred"),
            raw_matches=[m.__dict__ for m in matches],
            similar_names=[m.name for m in matches],
            notes=f"{len(matches)} similar name(s) found in Florida registry. No exact match.",
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
