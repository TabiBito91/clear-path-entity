"""
Washington Secretary of State — Corporations & Charities Search Adapter

STATUS: Manual search stub — automated search temporarily unavailable.

Investigation findings:
  - Legacy JSON API (sos.wa.gov/corps/search_results.aspx?format=json) is fully
    deprecated; both endpoints 301-redirect to the Angular SPA and return HTML.
  - CCFS Angular SPA (ccfs.sos.wa.gov) requires Playwright, but:
      * wait_until="networkidle" guaranteed timeout (Angular never goes idle).
      * wait_until="domcontentloaded" passes goto, but the SPA's true form
        selectors are unknown — all 7 candidates fail, burning the 90s budget.
  - Plain HTTP GET returns the SPA shell (no usable data) or redirects.

Path to a real implementation:
  1. Open ccfs.sos.wa.gov in a real browser with DevTools Network tab open.
  2. Perform a business name search and observe the XHR/fetch call the Angular
     app makes to its backend (URL, method, payload, response shape).
  3. Implement that endpoint as an httpx call (same pattern as NY/CA adapters).

Until then, users are directed to perform the search manually.
"""
from adapters.base import AdapterResult, BaseStateAdapter

MANUAL_SEARCH_URL = "https://ccfs.sos.wa.gov/#/AdvancedSearch"


class WashingtonAdapter(BaseStateAdapter):
    state_code = "WA"
    state_name = "Washington"

    async def search(self, name: str, entity_type: str) -> AdapterResult:
        return AdapterResult(
            state_code=self.state_code,
            state_name=self.state_name,
            availability="unknown",
            confidence=0.0,
            notes=(
                f"Automated Washington search is temporarily unavailable — "
                f"the CCFS portal uses an Angular SPA with no accessible API. "
                f"Search manually at {MANUAL_SEARCH_URL} "
                f'using the name: "{name}".'
            ),
            extraction_method="failed",
            source_type="web_form",
        )
