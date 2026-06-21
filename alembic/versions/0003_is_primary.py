"""cars.is_primary — mark the freshest listing per duplicate group (Phase 2: dedup)

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-21

Phase 2 of the encar-parser pre-backfill series: collapse duplicate
listings so the vitrine shows each physical car exactly once.

Background: Encar often lists the same physical vehicle under more than
one ``encar_id`` (different listing pages, identical photos, identical
specs). Confirmed against the live DB — 38 duplicate groups on 100 rows
keyed on (brand, model, year_month, mileage_km, color_original) with
identical price_krw in every group.

What this migration adds:

1. ``cars.is_primary BOOLEAN NOT NULL DEFAULT TRUE`` — every row starts
   as primary. The ``python -m encar_parser dedup`` command flips the
   older rows in each duplicate group to FALSE.

2. ``idx_cars_is_primary`` index — partial index that covers the vitrine
   query ``SELECT … WHERE is_primary = TRUE ORDER BY last_seen_at DESC``
   so it stays fast as the table grows.

The vitrine (web viewer), ``/catalog``, and the future CRM all filter on
``is_primary = TRUE``. Cars kept in the DB (not deleted) so we can
inspect duplicates later.

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "cars",
        sa.Column(
            "is_primary",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    # Partial index keeps the vitrine query fast without bloating writes
    # — only rows that the web/catalog/CRM will actually read.
    op.create_index(
        "idx_cars_is_primary",
        "cars",
        ["is_primary", "last_seen_at"],
        unique=False,
        postgresql_where=sa.text("is_primary = TRUE"),
    )


def downgrade() -> None:
    op.drop_index("idx_cars_is_primary", table_name="cars")
    op.drop_column("cars", "is_primary")
