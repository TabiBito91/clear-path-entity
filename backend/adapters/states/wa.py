"""
Washington Secretary of State — Corporations & Charities Search Adapter
URL: https://ccfs.sos.wa.gov/#/AdvancedSearch

The CCFS portal is an Angular SPA. The public SODA data extract was deprecated
in August 2024; programmatic access now requires Playwright.

Uses async_playwright with an extended wait strategy to allow Angular to
hydrate before interacting with form elements. Tries multiple selector variants
for both the input field and submit button.

WA results include: Business Name, UBI (file number), Entity Type, Status.
Inactive statuses: Dissolved, Delinquent, Revoked, Cancelled, Withdrawn, Expired.
"""
import asyncio

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

from adapters.base import AdapterResult, BaseStateAdapter, EntityMatch

SEARCH_URL = "https://ccfs.sos.wa.gov/#/AdvancedSearch"

NAME_INPUT_SELECTORS = [
    "input[placeholder*='Business Name' i]",
    "input[formcontrolname*='businessName' i]",
    "input[formcontrolname*='name' i]",
    "input[aria-label*='Business Name' i]",
    "input[aria-label*='name' i]",
    "mat-form-field input",
    "input[type='text']",
]

SUBMIT_SELECTORS = [
    "button[type='submit']",
    "button:has-text('Search')",
    "button.mat-raised-button",
    "button.mat-flat-button",
    "button.search-btn",
    "input[type='submit']",
]

_INACTIVE_STATUSES = {
    "inactive", "dissolved", "cancelled", "canceled", "revoked",
    "delinquent", "expired", "withdrawn", "merged", "terminated",
}

NO_RESULTS_TEXT = [
    "no results", "no records", "no matches", "not found",
    "no entities", "no businesses", "0 results", "no data",
    "no business", "no organization",
]


class WashingtonAdapter(BaseStateAdapter):
    state_code = "WA"
    state_name = "Washington"

    async def search(self, name: str, entity_type: str) -> AdapterResult:
        """Async entry point — interacts with Angular SPA via async_playwright."""
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
                notes="WA search timed out after 90 seconds.",
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
        """Launch browser and navigate to the Angular search page."""
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
                # Angular SPAs need networkidle to fully hydrate
                await page.goto(SEARCH_URL, wait_until="networkidle", timeout=30_000)
                return await self._fill_and_extract(page, name, entity_type)
            finally:
                await browser.close()

    async def _fill_and_extract(self, page, name: str, entity_type: str) -> AdapterResult:
        # Extra buffer for Angular framework initialisation
        await page.wait_for_timeout(2_000)

        # Find name input
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
                notes="WA search form not found — site structure may have changed.",
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
                notes="WA search submit button not found.",
                extraction_method="failed",
            )

        # Wait for Angular to re-render results
        try:
            await page.wait_for_load_state("networkidle", timeout=20_000)
        except PWTimeout:
            pass
        await page.wait_for_timeout(1_500)  # Angular render buffer

        return await self._parse_results(page, name, entity_type)

    async def _parse_results(self, page, name: str, entity_type: str) -> AdapterResult:
        page_text = (await page.inner_text("body")).lower()

        if any(phrase in page_text for phrase in NO_RESULTS_TEXT):
            return AdapterResult(
                state_code=self.state_code,
                state_name=self.state_name,
                availability="available",
                confidence=self._build_confidence("primary", "clear"),
                notes="No matching entities found in Washington registry.",
            )

        matches = await self._parse_table(page)
        if not matches:
            return await self._llm_fallback(page_text, name, entity_type)

        return self._classify(matches, name)

    async def _parse_table(self, page) -> list[EntityMatch]:
        """
        Parse the CCFS results table.
        Tries both standard <table> and Angular Material <mat-table>.
        Column detection is header-driven to handle layout changes.
        """
        matches: list[EntityMatch] = []

        # Angular Material tables use mat-row / mat-cell; also try standard table
        for row_sel, cell_sel, header_sel in [
            ("mat-row", "mat-cell", "mat-header-cell"),
            ("tr", "td", "th"),
            ("cdk-row", "cdk-cell", "cdk-header-cell"),
        ]:
            try:
                if await page.locator(row_sel).count() == 0:
                    continue

                header_cells = page.locator(header_sel)
                n_headers = await header_cells.count()
                header_texts = []
                for i in range(n_headers):
                    text = await header_cells.nth(i).inner_text()
                    header_texts.append(text.strip().lower())
                col = _col_index(header_texts)

                rows = await page.locator(row_sel).all()
                for row in rows:
                    cells_loc = row.locator(cell_sel)
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
                        registered="",
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
            notes=f"{len(matches)} similar name(s) found in Washington registry. No exact match.",
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
    """Map semantic column names to indices from the header row."""
    mapping = {}
    for i, h in enumerate(headers):
        if any(k in h for k in ("business name", "entity name", "organization name", "name")):
            mapping.setdefault("name", i)
        elif any(k in h for k in ("ubi", "entity id", "file", "number", "id")):
            mapping.setdefault("id", i)
        elif any(k in h for k in ("type", "entity type", "business type", "organization type")):
            mapping.setdefault("type", i)
        elif "status" in h:
            mapping.setdefault("status", i)
    return mapping


async def _cell(cells: list, index: int) -> str:
    """Safely await inner_text() from a Locator at the given index."""
    if index < 0 or index >= len(cells):
        return ""
    try:
        text = await cells[index].inner_text()
        return text.strip()
    except Exception:
        return ""
