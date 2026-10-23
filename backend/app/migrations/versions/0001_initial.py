# Copyright © HCGameLoc
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    # Adopt only the same application's pre-release local schema, if present.
    tables = {
        "users": [
            sa.Column("username", sa.String, primary_key=True),
            sa.Column("password_hash", sa.String, nullable=False),
            sa.Column("is_admin", sa.Integer, nullable=False),
        ],
        "sessions": [
            sa.Column("id", sa.String, primary_key=True),
            sa.Column("username", sa.String, nullable=False),
            sa.Column("csrf", sa.String, nullable=False),
            sa.Column("expires", sa.Float, nullable=False),
        ],
        "records": [
            sa.Column("id", sa.String, primary_key=True),
            sa.Column("kind", sa.String, nullable=False),
            sa.Column("owner", sa.String, nullable=False),
            sa.Column("revision", sa.String, nullable=False),
            sa.Column("data", sa.JSON, nullable=False),
        ],
        "jobs": [
            sa.Column("id", sa.String, primary_key=True),
            sa.Column("owner", sa.String, nullable=False),
            sa.Column("idempotency_key", sa.String, nullable=False, unique=True),
            sa.Column("lease_owner", sa.String, nullable=False),
            sa.Column("lease_until", sa.Float, nullable=False),
            sa.Column("status", sa.String, nullable=False),
            sa.Column("data", sa.JSON, nullable=False),
        ],
    }
    for name, columns in tables.items():
        if not inspector.has_table(name):
            op.create_table(name, *columns)
            if name == "records":
                op.create_index("ix_records_kind", name, ["kind"])
                op.create_index("ix_records_owner", name, ["owner"])
            if name == "jobs":
                op.create_index("ix_jobs_status", name, ["status"])


def downgrade():
    raise RuntimeError("Destructive schema downgrade is unsupported; restore an application backup")
