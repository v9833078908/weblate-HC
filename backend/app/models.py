# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from sqlalchemy import JSON, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    username: Mapped[str] = mapped_column(String, primary_key=True)
    password_hash: Mapped[str] = mapped_column(String)
    is_admin: Mapped[int] = mapped_column(Integer, default=0)


class SessionRecord(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    username: Mapped[str] = mapped_column(String)
    csrf: Mapped[str] = mapped_column(String)
    expires: Mapped[float] = mapped_column(Float)


class Record(Base):
    __tablename__ = "records"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String, index=True)
    owner: Mapped[str] = mapped_column(String, index=True)
    revision: Mapped[str] = mapped_column(String)
    data: Mapped[dict] = mapped_column(JSON)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner: Mapped[str] = mapped_column(String)
    idempotency_key: Mapped[str] = mapped_column(String, unique=True)
    lease_owner: Mapped[str] = mapped_column(String, default="")
    lease_until: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String, index=True)
    data: Mapped[dict] = mapped_column(JSON)


class RunUnitProgress(Base):
    __tablename__ = "run_unit_progress"
    __table_args__ = (UniqueConstraint("job_id", "unit_id", "purpose", name="uq_run_unit_purpose"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    job_id: Mapped[str] = mapped_column(String, index=True)
    unit_id: Mapped[int] = mapped_column(Integer)
    purpose: Mapped[str] = mapped_column(String)
    disposition: Mapped[str] = mapped_column(String)
    receipt_key: Mapped[str] = mapped_column(String, default="")
    remote_revision: Mapped[str] = mapped_column(String)
    updated: Mapped[float] = mapped_column(Float)


class QualityCycle(Base):
    __tablename__ = "quality_cycles"
    __table_args__ = (
        UniqueConstraint("job_id", "policy_revision", name="uq_quality_cycle_job_policy"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner: Mapped[str] = mapped_column(String, index=True)
    job_id: Mapped[str] = mapped_column(String, index=True)
    remote_project: Mapped[str] = mapped_column(String)
    policy_revision: Mapped[str] = mapped_column(String)
    policy: Mapped[dict] = mapped_column(JSON)
    phase: Mapped[str] = mapped_column(String, index=True)
    round: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, index=True)
    lease_owner: Mapped[str] = mapped_column(String, default="")
    lease_until: Mapped[float] = mapped_column(Float, default=0)
    heartbeat_at: Mapped[float] = mapped_column(Float, default=0)
    next_wakeup: Mapped[float] = mapped_column(Float, default=0)
    counts: Mapped[dict] = mapped_column(JSON, default=dict)


class QualityOperation(Base):
    __tablename__ = "quality_operations"
    __table_args__ = (
        UniqueConstraint("cycle_id", "operation_key", name="uq_quality_operation_key"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    cycle_id: Mapped[str] = mapped_column(String, index=True)
    operation_key: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)
    state: Mapped[str] = mapped_column(String, index=True)
    remote_id: Mapped[str] = mapped_column(String, default="")
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    next_retry: Mapped[float] = mapped_column(Float, default=0)
    failure_class: Mapped[str] = mapped_column(String, default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class QualityUnit(Base):
    __tablename__ = "quality_units"
    __table_args__ = (UniqueConstraint("cycle_id", "remote_unit_id", name="uq_quality_cycle_unit"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    cycle_id: Mapped[str] = mapped_column(String, index=True)
    remote_unit_id: Mapped[int] = mapped_column(Integer)
    input_revision: Mapped[str] = mapped_column(String)
    input_hash: Mapped[str] = mapped_column(String, default="")
    round: Mapped[int] = mapped_column(Integer, default=0)
    outcome: Mapped[str] = mapped_column(String, index=True)
    candidate_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    application_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    issue_kinds: Mapped[list] = mapped_column(JSON, default=list)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class QualityEvent(Base):
    __tablename__ = "quality_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cycle_id: Mapped[str] = mapped_column(String, index=True)
    kind: Mapped[str] = mapped_column(String)
    created: Mapped[float] = mapped_column(Float)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class QualityGroup(Base):
    __tablename__ = "quality_groups"
    __table_args__ = (
        UniqueConstraint("cycle_id", "remote_group_id", name="uq_quality_cycle_group"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    cycle_id: Mapped[str] = mapped_column(String, index=True)
    remote_group_id: Mapped[str] = mapped_column(String)
    revision: Mapped[str] = mapped_column(String)
    round: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String)
    data: Mapped[dict] = mapped_column(JSON)
