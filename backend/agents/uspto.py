"""
USPTO Trademark Search Agent.

Status: USPTO's public trademark search APIs have been removed or moved behind
WAF protection as of early 2026. This module currently returns a graceful
"manual review required" result with a link to the official TESS search.

TODO: Implement Playwright-based scraping of tmsearch.uspto.gov once the
Delaware Playwright adapter is confirmed stable.
"""
from database import AsyncSessionLocal
from models import UsptoResult

TESS_URL = "https://tmsearch.uspto.gov"


async def search_uspto(job_id: str, name: str) -> None:
    """Persist a graceful unavailable result for the USPTO check."""
    async with AsyncSessionLocal() as db:
        result = UsptoResult(
            job_id=job_id,
            exact_matches=[],
            similar_marks=[],
            risk_level="unknown",
            notes=(
                f"Automated USPTO trademark search is temporarily unavailable. "
                f"Search manually at {TESS_URL} using the mark name: \"{name}\"."
            ),
        )
        db.add(result)
        await db.commit()
