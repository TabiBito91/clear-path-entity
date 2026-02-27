"""
New York Department of State — Entity Name Search Adapter

Uses the NY Open Data SODA API instead of Playwright scraping.
Dataset: "Active Corporations: Beginning 1800"
API:     https://data.ny.gov/resource/n9v6-gdp6.json

No authentication required. Covers active NY entities only —
dissolved/inactive entities are excluded (their names may be available).

Columns used:
  current_entity_name  — registered entity name
  dos_id               — DOS ID number
  entity_type          — e.g. "DOMESTIC LIMITED LIABILITY COMPANY"
  initial_dos_filing_date — formation date (ISO 8601)
  county, jurisdiction — location info
"""
import httpx

from adapters.base import AdapterResult, BaseStateAdapter, EntityMatch

SODA_URL = "https://data.ny.gov/resource/n9v6-gdp6.json"
LIMIT = 100
TIMEOUT = 20.0


class NewYorkAdapter(BaseStateAdapter):
    state_code = "NY"
    state_name = "New York"

    async def search(self, name: str, entity_type: str) -> AdapterResult:
        name_upper = name.strip().upper()
        name_with_suffix = f"{name_upper} {entity_type.upper()}"

        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                # 1. Exact name match (bare name and name + entity type suffix)
                exact, exact2 = await _fetch_two(
                    client,
                    f"upper(current_entity_name)='{_esc(name_upper)}'",
                    f"upper(current_entity_name)='{_esc(name_with_suffix)}'",
                )
                exact_matches = exact + exact2

                # 2. Similar names — all active entities starting with the search term
                similar_resp = await client.get(SODA_URL, params={
                    "$where": f"upper(current_entity_name) like '{_esc(name_upper)}%'",
                    "$limit": LIMIT,
                    "$order": "current_entity_name",
                })
                similar_resp.raise_for_status()
                all_matches = similar_resp.json()

        except httpx.TimeoutException:
            return AdapterResult(
                state_code=self.state_code,
                state_name=self.state_name,
                availability="error",
                confidence=0.1,
                notes="NY Open Data API request timed out.",
            )
        except Exception as exc:
            return AdapterResult(
                state_code=self.state_code,
                state_name=self.state_name,
                availability="error",
                confidence=0.1,
                notes=f"NY Open Data API error: {type(exc).__name__}: {exc}",
            )

        return self._classify(exact_matches, all_matches, name)

    def _classify(self, exact_matches, all_matches, search_name: str) -> AdapterResult:
        exact = [_to_match(m) for m in exact_matches]

        all_entity = [_to_match(m) for m in all_matches]
        exact_names = {m.name.upper() for m in exact}
        similar_only = [m for m in all_entity if m.name.upper() not in exact_names]

        if exact:
            return AdapterResult(
                state_code=self.state_code,
                state_name=self.state_name,
                availability="taken",
                confidence=self._build_confidence("primary", "clear"),
                raw_matches=[m.__dict__ for m in exact],
                similar_names=[m.name for m in similar_only],
                notes=f"Exact match found: '{exact[0].name}'",
                source_type="api",
            )

        if all_entity:
            return AdapterResult(
                state_code=self.state_code,
                state_name=self.state_name,
                availability="similar",
                confidence=self._build_confidence("primary", "inferred"),
                raw_matches=[m.__dict__ for m in all_entity],
                similar_names=[m.name for m in all_entity],
                notes=f"{len(all_entity)} similar active NY entity name(s) found. No exact match.",
                source_type="api",
            )

        return AdapterResult(
            state_code=self.state_code,
            state_name=self.state_name,
            availability="available",
            confidence=self._build_confidence("primary", "clear"),
            notes="No matching active entities found in NY registry.",
            source_type="api",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _esc(s: str) -> str:
    """Escape single quotes for SODA $where clauses."""
    return s.replace("'", "''")


def _to_match(row: dict) -> EntityMatch:
    filed = row.get("initial_dos_filing_date", "")
    return EntityMatch(
        name=row.get("current_entity_name", ""),
        entity_type=row.get("entity_type", ""),
        status="active",
        file_number=row.get("dos_id", ""),
        registered=filed[:10] if filed else "",
    )


async def _fetch_two(client, where1: str, where2: str):
    """Fire two SODA queries sequentially and return both result lists."""
    r1 = await client.get(SODA_URL, params={"$where": where1, "$limit": 10})
    r2 = await client.get(SODA_URL, params={"$where": where2, "$limit": 10})
    r1.raise_for_status()
    r2.raise_for_status()
    return r1.json(), r2.json()
