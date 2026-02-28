"""
Search orchestrator.
Fans out state adapter searches + USPTO search in parallel using asyncio.
Writes results to the database as they complete so the SSE stream can push them live.
"""
import asyncio

from adapters.states.ca import CaliforniaAdapter
from adapters.states.de import DelawareAdapter
from adapters.states.fl import FloridaAdapter
from adapters.states.nj import NewJerseyAdapter
from adapters.states.ny import NewYorkAdapter
from adapters.states.wa import WashingtonAdapter
from agents.uspto import search_uspto
from database import AsyncSessionLocal
from llm.client import analyze_similarity
from models import StateResult
from rules.engine import apply_rules, get_rules_summary

# Registry: add new state adapters here as they are built
STATE_ADAPTERS = {
    "CA": CaliforniaAdapter,
    "DE": DelawareAdapter,
    "FL": FloridaAdapter,
    "NJ": NewJerseyAdapter,
    "NY": NewYorkAdapter,
    "WA": WashingtonAdapter,
}


async def run_search(job_id: str, name: str, entity_type: str, states: list[str]) -> None:
    """
    Run all state lookups + USPTO in parallel, persisting results as they arrive.
    """
    tasks = []

    for state_code in states:
        adapter_cls = STATE_ADAPTERS.get(state_code)
        if adapter_cls:
            tasks.append(_run_state(job_id, name, entity_type, adapter_cls()))

    # USPTO always runs alongside state lookups
    tasks.append(search_uspto(job_id, name))

    await asyncio.gather(*tasks, return_exceptions=True)


async def _run_state(job_id: str, name: str, entity_type: str, adapter) -> None:
    """Run a single state adapter, apply rules, optionally run similarity analysis, persist."""
    result = await adapter.search(name, entity_type)

    # Apply deterministic naming rules
    flags = apply_rules(name, entity_type, adapter.state_code)

    # If similar names found, run LLM similarity analysis
    if result.availability == "similar" and result.similar_names:
        rules_summary = get_rules_summary(adapter.state_code)
        try:
            similarity = await analyze_similarity(
                search_name=name,
                entity_type=entity_type,
                state_name=adapter.state_name,
                similar_names=result.similar_names[:10],
                state_rules_summary=rules_summary,
            )
            # Fold similarity risk into notes and flags
            risk = similarity.get("risk_level", "unknown")
            explanation = similarity.get("explanation", "")
            recommendation = similarity.get("recommendation", "")
            flags.append(f"[SIMILARITY] Risk: {risk.upper()}. {explanation} {recommendation}")
        except Exception as e:
            flags.append(f"[SIMILARITY] Analysis failed: {e}")

    async with AsyncSessionLocal() as db:
        state_result = StateResult(
            job_id=job_id,
            state_code=result.state_code,
            state_name=result.state_name,
            availability=result.availability,
            confidence=result.confidence,
            similar_names=result.similar_names,
            flags=flags + result.flags,
            raw_matches=[m if isinstance(m, dict) else m.__dict__ for m in result.raw_matches],
            notes=result.notes,
        )
        db.add(state_result)
        await db.commit()
