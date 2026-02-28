"""
Florida Division of Corporations (Sunbiz) — Entity Name Search Adapter
URL: https://search.sunbiz.org/Inquiry/CorporationSearch/ByName

Uses httpx with a persistent session instead of Playwright. Sunbiz blocks headless
Chromium (bot detection) AND direct HTTP GETs to the results URL (403 without
session cookies). The solution is a two-step session approach:
  1. GET the form page to acquire Sunbiz session cookies.
  2. GET the results URL with those cookies + Referer header — the server now
     treats the request as coming from a legitimate form submission.

HTML is parsed with Python's built-in html.parser (no external dependencies).

FL returns all entity statuses (Active, INACT, CROSS RF, RPEND/UA, etc.).
Results table has 3 columns: Corporate Name | Document Number | Status.
An empty <tbody> means no results — no LLM call needed.
"""
from html.parser import HTMLParser

import httpx

from adapters.base import AdapterResult, BaseStateAdapter, EntityMatch

FORM_URL = "https://search.sunbiz.org/Inquiry/CorporationSearch/ByName"
RESULTS_URL = "https://search.sunbiz.org/Inquiry/CorporationSearch/SearchResults"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_INACTIVE_STATUSES = {
    "inact", "inactive", "cross rf", "dissolved", "revoked",
    "cancelled", "canceled", "merged", "converted", "withdrawn",
}

NO_RESULTS_TEXT = [
    "no filings", "no matching", "no records", "no results",
    "0 results", "not found",
]


class FloridaAdapter(BaseStateAdapter):
    state_code = "FL"
    state_name = "Florida"

    async def search(self, name: str, entity_type: str) -> AdapterResult:
        try:
            async with httpx.AsyncClient(
                headers=_HEADERS,
                follow_redirects=True,
                timeout=30.0,
            ) as client:
                # Step 1: Warm the session — Sunbiz sets cookies on the form page
                # that are required before it will serve results.
                await client.get(FORM_URL)

                # Step 2: Fetch results with session cookies + Referer in place.
                resp = await client.get(
                    RESULTS_URL,
                    params={
                        "inquiryType": "EntityName",
                        "inquiryDirective": "StartsWith",
                        "allCurrentNames": "true",
                        "corporationNameSearchTerm": name.strip(),
                        "Search": "Search",
                    },
                    headers={"Referer": FORM_URL},
                )
                resp.raise_for_status()

            return self._parse_html(resp.text, name)

        except httpx.TimeoutException:
            return AdapterResult(
                state_code=self.state_code,
                state_name=self.state_name,
                availability="error",
                confidence=0.1,
                notes="FL Sunbiz request timed out.",
            )
        except httpx.HTTPStatusError as exc:
            return AdapterResult(
                state_code=self.state_code,
                state_name=self.state_name,
                availability="error",
                confidence=0.1,
                notes=f"FL Sunbiz returned HTTP {exc.response.status_code}. Site may be blocking automated requests.",
            )
        except Exception as exc:
            return AdapterResult(
                state_code=self.state_code,
                state_name=self.state_name,
                availability="error",
                confidence=0.1,
                notes=f"Unexpected error: {type(exc).__name__}: {exc}",
            )

    def _parse_html(self, html: str, name: str) -> AdapterResult:
        """Parse the Sunbiz results page HTML and classify matches."""
        lower = html.lower()
        if any(phrase in lower for phrase in NO_RESULTS_TEXT):
            return AdapterResult(
                state_code=self.state_code,
                state_name=self.state_name,
                availability="available",
                confidence=self._build_confidence("primary", "clear"),
                notes="No matching entities found in Florida registry.",
                source_type="api",
            )

        parser = _TableParser()
        parser.feed(html)

        if not parser.rows:
            # Table present but empty, or no table — both mean no results on Sunbiz
            return AdapterResult(
                state_code=self.state_code,
                state_name=self.state_name,
                availability="available",
                confidence=self._build_confidence("primary", "clear"),
                notes="No matching entities found in Florida registry.",
                source_type="api",
            )

        matches = [
            EntityMatch(
                name=row[0],
                entity_type="",
                status=row[2] if len(row) > 2 else "unknown",
                file_number=row[1] if len(row) > 1 else "",
                registered="",
            )
            for row in parser.rows
            if row and row[0]
        ]

        return self._classify(matches, name)

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
                source_type="api",
            )

        return AdapterResult(
            state_code=self.state_code,
            state_name=self.state_name,
            availability="similar",
            confidence=self._build_confidence("primary", "inferred"),
            raw_matches=[m.__dict__ for m in matches],
            similar_names=[m.name for m in matches],
            notes=f"{len(matches)} similar name(s) found in Florida registry. No exact match.",
            source_type="api",
        )


# ---------------------------------------------------------------------------
# HTML parser
# ---------------------------------------------------------------------------

class _TableParser(HTMLParser):
    """
    Extracts rows from the first <tbody> in the Sunbiz results page.
    Columns: Corporate Name (0) | Document Number (1) | Status (2).
    The entity name is inside an <a> tag — convert_charrefs=True handles
    HTML entities automatically so handle_data receives clean text.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._table_depth = 0
        self._in_tbody = False
        self._in_tr = False
        self._in_td = False
        self._current_row: list[str] = []
        self._current_text = ""
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._table_depth += 1
        elif tag == "tbody" and self._table_depth == 1:
            self._in_tbody = True
        elif tag == "tr" and self._in_tbody:
            self._in_tr = True
            self._current_row = []
        elif tag == "td" and self._in_tr:
            self._in_td = True
            self._current_text = ""

    def handle_endtag(self, tag):
        if tag == "table":
            self._table_depth -= 1
            if self._table_depth == 0:
                self._in_tbody = False
        elif tag == "tbody":
            self._in_tbody = False
        elif tag == "tr" and self._in_tbody:
            self._in_tr = False
            if self._current_row:
                self.rows.append(self._current_row[:])
        elif tag == "td" and self._in_tr:
            self._in_td = False
            self._current_row.append(self._current_text.strip())

    def handle_data(self, data):
        if self._in_td:
            self._current_text += data
