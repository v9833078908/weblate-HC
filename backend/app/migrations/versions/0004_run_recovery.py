# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "run_unit_progress",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("job_id", sa.String, nullable=False, index=True),
        sa.Column("unit_id", sa.Integer, nullable=False),
        sa.Column("purpose", sa.String, nullable=False),
        sa.Column("disposition", sa.String, nullable=False),
        sa.Column("receipt_key", sa.String, nullable=False),
        sa.Column("remote_revision", sa.String, nullable=False),
        sa.Column("updated", sa.Float, nullable=False),
        sa.UniqueConstraint("job_id", "unit_id", "purpose", name="uq_run_unit_purpose"),
    )


def downgrade():
    raise RuntimeError("Destructive schema downgrade is unsupported; restore an application backup")
