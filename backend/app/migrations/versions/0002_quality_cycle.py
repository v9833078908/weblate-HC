# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("quality_cycles"):
        op.create_table(
            "quality_cycles",
            sa.Column("id", sa.String, primary_key=True),
            sa.Column("owner", sa.String, nullable=False),
            sa.Column("job_id", sa.String, nullable=False),
            sa.Column("remote_project", sa.String, nullable=False),
            sa.Column("policy_revision", sa.String, nullable=False),
            sa.Column("policy", sa.JSON, nullable=False),
            sa.Column("phase", sa.String, nullable=False),
            sa.Column("round", sa.Integer, nullable=False),
            sa.Column("status", sa.String, nullable=False),
            sa.Column("lease_owner", sa.String, nullable=False),
            sa.Column("lease_until", sa.Float, nullable=False),
            sa.Column("heartbeat_at", sa.Float, nullable=False),
            sa.Column("next_wakeup", sa.Float, nullable=False),
            sa.Column("counts", sa.JSON, nullable=False),
            sa.UniqueConstraint("job_id", "policy_revision", name="uq_quality_cycle_job_policy"),
        )
        op.create_index("ix_quality_cycles_owner", "quality_cycles", ["owner"])
        op.create_index("ix_quality_cycles_job_id", "quality_cycles", ["job_id"])
        op.create_index("ix_quality_cycles_phase", "quality_cycles", ["phase"])
        op.create_index("ix_quality_cycles_status", "quality_cycles", ["status"])

    if not inspector.has_table("quality_operations"):
        op.create_table(
            "quality_operations",
            sa.Column("id", sa.String, primary_key=True),
            sa.Column("cycle_id", sa.String, nullable=False),
            sa.Column("operation_key", sa.String, nullable=False),
            sa.Column("kind", sa.String, nullable=False),
            sa.Column("state", sa.String, nullable=False),
            sa.Column("remote_id", sa.String, nullable=False),
            sa.Column("attempt", sa.Integer, nullable=False),
            sa.Column("next_retry", sa.Float, nullable=False),
            sa.Column("failure_class", sa.String, nullable=False),
            sa.Column("data", sa.JSON, nullable=False),
            sa.UniqueConstraint("cycle_id", "operation_key", name="uq_quality_operation_key"),
        )
        op.create_index("ix_quality_operations_cycle_id", "quality_operations", ["cycle_id"])
        op.create_index("ix_quality_operations_state", "quality_operations", ["state"])

    if not inspector.has_table("quality_units"):
        op.create_table(
            "quality_units",
            sa.Column("id", sa.String, primary_key=True),
            sa.Column("cycle_id", sa.String, nullable=False),
            sa.Column("remote_unit_id", sa.Integer, nullable=False),
            sa.Column("input_revision", sa.String, nullable=False),
            sa.Column("input_hash", sa.String, nullable=False),
            sa.Column("round", sa.Integer, nullable=False),
            sa.Column("outcome", sa.String, nullable=False),
            sa.Column("candidate_id", sa.Integer, nullable=True),
            sa.Column("application_id", sa.Integer, nullable=True),
            sa.Column("issue_kinds", sa.JSON, nullable=False),
            sa.Column("retry_count", sa.Integer, nullable=False),
            sa.Column("data", sa.JSON, nullable=False),
            sa.UniqueConstraint("cycle_id", "remote_unit_id", name="uq_quality_cycle_unit"),
        )
        op.create_index("ix_quality_units_cycle_id", "quality_units", ["cycle_id"])
        op.create_index("ix_quality_units_outcome", "quality_units", ["outcome"])

    if not inspector.has_table("quality_events"):
        op.create_table(
            "quality_events",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("cycle_id", sa.String, nullable=False),
            sa.Column("kind", sa.String, nullable=False),
            sa.Column("created", sa.Float, nullable=False),
            sa.Column("data", sa.JSON, nullable=False),
        )
        op.create_index("ix_quality_events_cycle_id", "quality_events", ["cycle_id"])


def downgrade():
    raise RuntimeError("Destructive schema downgrade is unsupported; restore an application backup")
