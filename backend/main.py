import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from adapters.detail.opencorporates import fetch_entity_detail
from agents.orchestrator import run_search
from config import settings
from database import AsyncSessionLocal, get_db, init_db
from models import EntityDetailCache, Job, StateResult, UsptoResult

# In-memory set of active job IDs being streamed
_active_jobs: dict[str, asyncio.Event] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Clear Path Entity", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SUPPORTED_STATES = ["DE"]  # expand as adapters are added

ENTITY_TYPES = ["LLC", "Corporation", "LP", "LLP", "PC", "PLLC"]


class SearchRequest(BaseModel):
    name: str
    entity_type: str
    states: list[str] | None = None  # None = all supported states


class SearchResponse(BaseModel):
    job_id: str
    states_queued: list[str]


@app.post("/api/search", response_model=SearchResponse)
async def create_search(
    req: SearchRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "Name cannot be empty")
    if req.entity_type not in ENTITY_TYPES:
        raise HTTPException(400, f"Entity type must be one of: {ENTITY_TYPES}")

    states = req.states or SUPPORTED_STATES
    states = [s.upper() for s in states if s.upper() in SUPPORTED_STATES]
    if not states:
        raise HTTPException(400, f"No supported states requested. Supported: {SUPPORTED_STATES}")

    job = Job(name=name, entity_type=req.entity_type, states=states, status="pending")
    db.add(job)
    await db.commit()
    await db.refresh(job)

    done_event = asyncio.Event()
    _active_jobs[job.id] = done_event

    background_tasks.add_task(_run_and_signal, job.id, name, req.entity_type, states, done_event)

    return SearchResponse(job_id=job.id, states_queued=states)


async def _run_and_signal(
    job_id: str,
    name: str,
    entity_type: str,
    states: list[str],
    done_event: asyncio.Event,
):
    async with AsyncSessionLocal() as db:
        job = await db.get(Job, job_id)
        job.status = "running"
        await db.commit()

    try:
        await run_search(job_id, name, entity_type, states)
    except Exception as exc:
        async with AsyncSessionLocal() as db:
            job = await db.get(Job, job_id)
            job.status = "error"
            job.completed_at = datetime.now(timezone.utc)
            await db.commit()
        print(f"[job {job_id}] error: {exc}")
        return
    finally:
        done_event.set()
        _active_jobs.pop(job_id, None)

    async with AsyncSessionLocal() as db:
        job = await db.get(Job, job_id)
        job.status = "complete"
        job.completed_at = datetime.now(timezone.utc)
        await db.commit()


@app.get("/api/jobs/{job_id}/stream")
async def stream_results(job_id: str):
    """SSE endpoint — streams state results as they complete."""

    async def event_generator():
        sent_result_ids: set[str] = set()
        done_event = _active_jobs.get(job_id)

        while True:
            async with AsyncSessionLocal() as db:
                # Fetch job status
                job = await db.get(Job, job_id)
                if job is None:
                    yield {"event": "error", "data": json.dumps({"message": "Job not found"})}
                    return

                # Fetch any new state results
                result = await db.execute(
                    select(StateResult).where(StateResult.job_id == job_id)
                )
                state_results = result.scalars().all()

                for sr in state_results:
                    if sr.id not in sent_result_ids:
                        sent_result_ids.add(sr.id)
                        yield {
                            "event": "state_result",
                            "data": json.dumps({
                                "state_code": sr.state_code,
                                "state_name": sr.state_name,
                                "availability": sr.availability,
                                "confidence": sr.confidence,
                                "similar_names": sr.similar_names,
                                "flags": sr.flags,
                                "raw_matches": sr.raw_matches,
                                "notes": sr.notes,
                            }),
                        }

                # Fetch USPTO result if available
                uspto_result = await db.execute(
                    select(UsptoResult).where(UsptoResult.job_id == job_id)
                )
                uspto = uspto_result.scalar_one_or_none()
                if uspto and f"uspto_{uspto.id}" not in sent_result_ids:
                    sent_result_ids.add(f"uspto_{uspto.id}")
                    yield {
                        "event": "uspto_result",
                        "data": json.dumps({
                            "exact_matches": uspto.exact_matches,
                            "similar_marks": uspto.similar_marks,
                            "risk_level": uspto.risk_level,
                            "notes": uspto.notes,
                        }),
                    }

                if job.status in ("complete", "error"):
                    yield {"event": "done", "data": json.dumps({"status": job.status})}
                    return

            # Wait a bit before polling again; wake early if job signals done
            if done_event:
                try:
                    await asyncio.wait_for(asyncio.shield(done_event.wait()), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
            else:
                await asyncio.sleep(1.0)

    return EventSourceResponse(event_generator())


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    state_results = await db.execute(
        select(StateResult).where(StateResult.job_id == job_id)
    )
    uspto_result = await db.execute(
        select(UsptoResult).where(UsptoResult.job_id == job_id)
    )

    return {
        "job_id": job.id,
        "name": job.name,
        "entity_type": job.entity_type,
        "status": job.status,
        "created_at": job.created_at.isoformat(),
        "state_results": [
            {
                "state_code": r.state_code,
                "state_name": r.state_name,
                "availability": r.availability,
                "confidence": r.confidence,
                "similar_names": r.similar_names,
                "flags": r.flags,
                "raw_matches": r.raw_matches,
                "notes": r.notes,
            }
            for r in state_results.scalars().all()
        ],
        "uspto_result": (
            {
                "exact_matches": _u.exact_matches,
                "similar_marks": _u.similar_marks,
                "risk_level": _u.risk_level,
                "notes": _u.notes,
            }
            if (_u := uspto_result.scalar_one_or_none()) is not None
            else None
        ),
    }


@app.get("/api/entity/{state_code}/{file_number}")
async def get_entity_detail(state_code: str, file_number: str, db: AsyncSession = Depends(get_db)):
    """
    Return detail for a single entity by state code and file number.
    Checks the cache first; fetches live from OpenCorporates if not cached.
    """
    state_code = state_code.upper()
    cache_key = f"{state_code}:{file_number}"
    cached = await db.get(EntityDetailCache, cache_key)
    if cached:
        oc_url = f"https://opencorporates.com/companies/us_{state_code.lower()}/{file_number}"
        return {
            "file_number": cached.file_number,
            "entity_name": cached.entity_name,
            "entity_kind": cached.entity_kind,
            "formation_date": cached.formation_date,
            "registered_agent": cached.registered_agent,
            "opencorporates_url": oc_url,
            "cached": True,
        }

    detail = await fetch_entity_detail(state_code, file_number)

    if "error" not in detail:
        cache_row = EntityDetailCache(
            file_number=cache_key,
            state_code=state_code,
            entity_name=detail.get("entity_name"),
            entity_kind=detail.get("entity_kind"),
            formation_date=detail.get("formation_date"),
            registered_agent=detail.get("registered_agent"),
        )
        db.add(cache_row)
        await db.commit()

    return {
        "file_number": file_number,
        "entity_name": detail.get("entity_name"),
        "entity_kind": detail.get("entity_kind"),
        "formation_date": detail.get("formation_date"),
        "registered_agent": detail.get("registered_agent"),
        "opencorporates_url": detail.get("opencorporates_url"),
        "error": detail.get("error"),
        "cached": False,
    }
