"""model_overrides — operator edits to the generated catalogue

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-21

Phase 6 of the encar-parser CRM work. The /categories page exposes
``encar_web_url(sm)`` (Phase 5) which builds the front-end search URL
from the stored ``sm.encar_action["q"]``. Sometimes the operator wants
to override that — either because Encar's q-filter doesn't capture
the exact slice they care about, or because they want to deep-link
into a saved search from another tool.

The override lives in its own table — NOT on ``search_models`` — for
two reasons:

1. ``sync`` re-imports ``search_models`` from ``models.yaml`` on every
   run. If we stored the override on the same row, sync would clobber
   it (or we'd have to teach sync about a separate "preserve manual"
   flag). Two tables is simpler: sync only touches ``search_models``.
2. The override is a UI concern, not a parser concern. Keeping it
   separate means parser code stays unaware of it.

Schema
------
- ``slug TEXT PRIMARY KEY`` — no FK to ``search_models`` so a model
  can be deleted from the YAML (and from ``search_models``) without
  losing the operator's manual override. The CRM still works even
  if sync hasn't been re-run.
- ``manual_encar_url TEXT NULL`` — the override URL. ``NULL`` means
  "no override" — the auto-generated URL is used.
- ``updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`` — for the audit
  column on /categories.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_overrides",
        sa.Column("slug", sa.Text(), primary_key=True, nullable=False),
        sa.Column("manual_encar_url", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("model_overrides")
