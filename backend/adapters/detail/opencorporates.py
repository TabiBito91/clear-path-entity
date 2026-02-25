"""
OpenCorporates entity detail fetcher.

Uses the OpenCorporates free JSON API to retrieve formation date, entity kind,
and registered agent for any U.S. state company by file number.

API pattern: https://api.opencorporates.com/v0.4/companies/us_{state_code}/{file_number}

No authentication required for basic fields (name, type, incorporation date,
current status). Officers / registered agent requires an API key on paid plans.
"""
import httpx

OC_BASE = "https://api.opencorporates.com/v0.4/companies"
TIMEOUT = 15.0


async def fetch_entity_detail(state_code: str, file_number: str) -> dict:
    """
    Fetch entity detail from OpenCorporates.

    Returns a dict with keys:
      entity_name, entity_kind, formation_date, registered_agent,
      opencorporates_url, error (optional)
    """
    jurisdiction = f"us_{state_code.lower()}"
    url = f"{OC_BASE}/{jurisdiction}/{file_number}"
    oc_url = f"https://opencorporates.com/companies/{jurisdiction}/{file_number}"

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(
                url,
                headers={"Accept": "application/json"},
                follow_redirects=True,
            )

        if resp.status_code == 404:
            return {
                "entity_name": None,
                "entity_kind": None,
                "formation_date": None,
                "registered_agent": None,
                "opencorporates_url": oc_url,
                "error": f"Entity #{file_number} not found on OpenCorporates.",
            }

        if resp.status_code != 200:
            return {
                "entity_name": None,
                "entity_kind": None,
                "formation_date": None,
                "registered_agent": None,
                "opencorporates_url": oc_url,
                "error": f"OpenCorporates returned HTTP {resp.status_code}.",
            }

        data = resp.json()
        company = data.get("results", {}).get("company", {})

        # Registered agent: look in officers list for role containing "agent"
        registered_agent = None
        for officer in company.get("officers", []):
            role = (officer.get("officer", {}).get("position") or "").lower()
            if "agent" in role:
                registered_agent = officer.get("officer", {}).get("name")
                break

        return {
            "entity_name": company.get("name"),
            "entity_kind": company.get("company_type"),
            "formation_date": company.get("incorporation_date"),
            "registered_agent": registered_agent,
            "opencorporates_url": company.get("opencorporates_url") or oc_url,
        }

    except httpx.TimeoutException:
        return {
            "entity_name": None,
            "entity_kind": None,
            "formation_date": None,
            "registered_agent": None,
            "opencorporates_url": oc_url,
            "error": "Request to OpenCorporates timed out.",
        }
    except Exception as exc:
        return {
            "entity_name": None,
            "entity_kind": None,
            "formation_date": None,
            "registered_agent": None,
            "opencorporates_url": oc_url,
            "error": f"{type(exc).__name__}: {exc}",
        }
