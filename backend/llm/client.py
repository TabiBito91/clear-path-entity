"""
LLM client wrapper.
- Haiku 4.5  → used for fallback page interpretation (cheap, fast)
- Sonnet 4.6 → used for deceptive similarity analysis (more capable)
"""
import json

import anthropic

from config import settings

_async_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
_sync_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"


async def interpret_state_page(
    state_name: str,
    search_name: str,
    entity_type: str,
    page_text: str,
) -> dict:
    """
    Haiku call: parse an ambiguous state results page.
    Returns structured JSON with availability, similar_names, clarity, notes.
    """
    prompt = f"""You are analyzing the text of a U.S. state Secretary of State entity search results page.

State: {state_name}
Searched for: "{search_name}" ({entity_type})

Page text (truncated):
---
{page_text}
---

Return a JSON object with these fields:
- availability: "available" | "taken" | "similar" | "unknown"
- similar_names: list of entity names that appear similar to the searched name (may be empty)
- clarity: "clear" | "inferred" | "ambiguous"
- notes: one sentence explaining your conclusion

Return only valid JSON, no other text."""

    response = await _async_client.messages.create(
        model=HAIKU,
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )

    try:
        return json.loads(response.content[0].text)
    except (json.JSONDecodeError, IndexError, KeyError):
        return {
            "availability": "unknown",
            "similar_names": [],
            "clarity": "ambiguous",
            "notes": "LLM response could not be parsed.",
        }


def interpret_state_page_sync(
    state_name: str,
    search_name: str,
    entity_type: str,
    page_text: str,
) -> dict:
    """Sync version for use inside ThreadPoolExecutor (no event loop available)."""
    prompt = f"""You are analyzing the text of a U.S. state Secretary of State entity search results page.

State: {state_name}
Searched for: "{search_name}" ({entity_type})

Page text (truncated):
---
{page_text}
---

Return a JSON object with these fields:
- availability: "available" | "taken" | "similar" | "unknown"
- similar_names: list of entity names that appear similar to the searched name (may be empty)
- clarity: "clear" | "inferred" | "ambiguous"
- notes: one sentence explaining your conclusion

Return only valid JSON, no other text."""

    try:
        response = _sync_client.messages.create(
            model=HAIKU,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        return json.loads(response.content[0].text)
    except Exception:
        return {
            "availability": "unknown",
            "similar_names": [],
            "clarity": "ambiguous",
            "notes": "LLM response could not be parsed.",
        }


async def analyze_similarity(
    search_name: str,
    entity_type: str,
    state_name: str,
    similar_names: list[str],
    state_rules_summary: str,
) -> dict:
    """
    Sonnet call: assess deceptive similarity risk between searched name and found names.
    Only called when similar names are present.
    """
    prompt = f"""You are a business name availability specialist assessing deceptive similarity risk.

Searched name: "{search_name}" ({entity_type})
State: {state_name}
State naming rules summary:
{state_rules_summary}

Similar names already registered in this state:
{json.dumps(similar_names, indent=2)}

Assess whether "{search_name}" would likely be rejected due to deceptive similarity to any of the above names.

Return a JSON object with:
- risk_level: "low" | "medium" | "high"
- conflicting_names: list of the specific names that pose the greatest conflict
- explanation: 2-3 sentences explaining the risk assessment
- recommendation: one actionable sentence for the user

Return only valid JSON, no other text."""

    response = await _async_client.messages.create(
        model=SONNET,
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )

    try:
        return json.loads(response.content[0].text)
    except (json.JSONDecodeError, IndexError, KeyError):
        return {
            "risk_level": "unknown",
            "conflicting_names": similar_names,
            "explanation": "Could not parse similarity analysis.",
            "recommendation": "Review similar names manually.",
        }
