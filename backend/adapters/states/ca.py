"""
California Secretary of State — Entity Name Search Adapter

Uses the official CA SOS BE Public Search API.
Register free at: https://calicodev.sos.ca.gov/
Set env var:      CA_SOS_API_KEY=<your subscription key>

Endpoint: https://calico.sos.ca.gov/cbc/v1/api/BusinessEntityKeywordSearch
Auth:     Ocp-Apim-Subscription-Key header
Returns:  Up to 150 entities whose names contain the search term (all statuses).

Notes:
  - Unlike NY, CA returns all entity statuses (active, dissolved, suspended, etc.)
  - Exact match against a dissolved/cancelled entity is flagged — name may be
    available, but the user should verify with an attorney.
"""
import httpx

from adapters.base import AdapterResult, BaseStateAdapter, EntityMatch
from config import settings

API_URL = "https://calico.sos.ca.gov/cbc/v1/api/BusinessEntityKeywordSearch"
TIMEOUT = 20.0

# Statuses that indicate the entity is no longer active
_INACTIVE = {
    "dissolved", "cancelled", "canceled", "forfeited",
    "sos canceled", "sos cancelled", "void",
}


class CaliforniaAdapter(BaseStateAdapter):
    state_code = "CA"
    state_name = "California"

    async def search(self, name: str, entity_type: str) -> AdapterResult:
        if not settings.ca_sos_api_key:
            return AdapterResult(
                state_code=self.state_code,
                state_name=self.state_name,
                availability="error",
                confidence=0.1,
                notes="CA SOS API key not configured. Set the CA_SOS_API_KEY environment variable.",
            )

        name_upper = name.strip().upper()
        headers = {"Ocp-Apim-Subscription-Key": settings.ca_sos_api_key}

        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                resp = await client.get(
                    API_URL,
                    params={"search-term": name_upper},
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.TimeoutException:
            return AdapterResult(
                state_code=self.state_code,
                state_name=self.state_name,
                availability="error",
                confidence=0.1,
                notes="CA SOS API request timed out.",
            )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 401:
                msg = "CA SOS API key is invalid or expired."
            elif status == 429:
                msg = "CA SOS API rate limit exceeded. Try again shortly."
            else:
                msg = f"CA SOS API returned HTTP {status}."
            return AdapterResult(
                state_code=self.state_code,
                state_name=self.state_name,
                availability="error",
                confidence=0.1,
                notes=msg,
            )
        except Exception as exc:
            return AdapterResult(
                state_code=self.state_code,
                state_name=self.state_name,
                availability="error",
                confidence=0.1,
                notes=f"CA SOS API error: {type(exc).__name__}: {exc}",
            )

        return self._classify(_parse_results(data), name)

    def _classify(self, matches: list[EntityMatch], search_name: str) -> AdapterResult:
        if not matches:
            return AdapterResult(
                state_code=self.state_code,
                state_name=self.state_name,
                availability="available",
                confidence=self._build_confidence("primary", "clear"),
                notes="No matching entities found in California registry.",
                source_type="api",
            )

        name_upper = search_name.strip().upper()
        exact = [m for m in matches if m.name.strip().upper() == name_upper]
        similar = [m for m in matches if m not in exact]

        if exact:
            all_inactive = all(m.status.lower() in _INACTIVE for m in exact)
            note = f"Exact match found: '{exact[0].name}' (status: {exact[0].status})."
            if all_inactive:
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
            notes=f"{len(matches)} similar California entity name(s) found. No exact match.",
            source_type="api",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get(row: dict, *keys: str, default: str = "") -> str:
    """Try multiple possible field names — CA API field names confirmed at runtime."""
    for key in keys:
        val = row.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return default


def _parse_results(data) -> list[EntityMatch]:
    """
    Parse the CA SOS API response.
    Handles both a top-level list and a dict wrapper (common in Azure APIM responses).
    Field names are tried in both PascalCase and camelCase variants.
    """
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = (
            data.get("results")
            or data.get("Results")
            or data.get("entities")
            or data.get("Entities")
            or []
        )
    else:
        return []

    matches = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = _get(row, "EntityName", "entityName", "Name", "name")
        if not name:
            continue
        matches.append(EntityMatch(
            name=name,
            entity_type=_get(
                row,
                "EntityType", "entityType",
                "EntityTypeName", "entityTypeName",
            ),
            status=_get(
                row,
                "Status", "status",
                "StatusType", "statusType",
                "EntityStatus", "entityStatus",
                default="unknown",
            ),
            file_number=_get(
                row,
                "EntityNumber", "entityNumber",
                "FileNumber", "fileNumber",
            ),
            registered=_get(
                row,
                "FormationDate", "formationDate",
                "RegistrationDate", "registrationDate",
                "InitialFilingDate", "initialFilingDate",
            ),
        ))
    return matches
