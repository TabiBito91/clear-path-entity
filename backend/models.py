import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from database import Base


def utcnow():
    return datetime.now(timezone.utc)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String, nullable=False)
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    states: Mapped[list] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending | running | complete | error
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StateResult(Base):
    __tablename__ = "state_results"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    state_code: Mapped[str] = mapped_column(String(2), nullable=False)
    state_name: Mapped[str] = mapped_column(String, nullable=False)
    availability: Mapped[str] = mapped_column(String, nullable=False)  # available | taken | similar | unknown | error
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    similar_names: Mapped[list] = mapped_column(JSON, default=list)
    flags: Mapped[list] = mapped_column(JSON, default=list)       # restricted words, naming rule warnings
    raw_matches: Mapped[list] = mapped_column(JSON, default=list)  # entities found on state site
    notes: Mapped[str] = mapped_column(Text, default="")
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UsptoResult(Base):
    __tablename__ = "uspto_results"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    exact_matches: Mapped[list] = mapped_column(JSON, default=list)
    similar_marks: Mapped[list] = mapped_column(JSON, default=list)
    risk_level: Mapped[str] = mapped_column(String, default="low")  # low | medium | high
    notes: Mapped[str] = mapped_column(Text, default="")
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EntityDetailCache(Base):
    """Cache for per-entity detail fetches (formation date, entity kind, registered agent)."""
    __tablename__ = "entity_detail_cache"

    file_number: Mapped[str] = mapped_column(String, primary_key=True)
    state_code: Mapped[str] = mapped_column(String(2), nullable=False)
    entity_name: Mapped[str | None] = mapped_column(String, nullable=True)
    entity_kind: Mapped[str | None] = mapped_column(String, nullable=True)
    formation_date: Mapped[str | None] = mapped_column(String, nullable=True)
    registered_agent: Mapped[str | None] = mapped_column(String, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
