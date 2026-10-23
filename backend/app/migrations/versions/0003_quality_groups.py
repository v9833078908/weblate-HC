# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "quality_groups",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("cycle_id", sa.String, nullable=False),
        sa.Column("remote_group_id", sa.String, nullable=False),
        sa.Column("revision", sa.String, nullable=False),
        sa.Column("round", sa.Integer, nullable=False),
        sa.Column("outcome", sa.String, nullable=False),
        sa.Column("data", sa.JSON, nullable=False),
        sa.UniqueConstraint("cycle_id", "remote_group_id", name="uq_quality_cycle_group"),
    )
    op.create_index("ix_quality_groups_cycle_id", "quality_groups", ["cycle_id"])


def downgrade():
    raise RuntimeError("Destructive schema downgrade is unsupported; restore an application backup")
