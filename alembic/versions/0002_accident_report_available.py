"""accident_records → accident_report_available (Phase 1)

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-18

Phase 1 of the encar-parser pre-backfill series: the old int column
`accident_records` (0/1) was misleadingly named — 96.7% of the BMW X5
sample had value 1, suggesting that 96.7% of cars had an accident. In
reality, `condition.accident.recordView` is a boolean "vehicle history
report available" flag, not an accident count. Encar's API does not
expose real insurance history through this endpoint
(`condition.insurance` is null).

The migration:
1. Renames `accident_records` → `accident_report_available`.
2. Changes the type from Integer to Boolean.
3. Coerces any existing non-zero integer to TRUE, zero to FALSE, NULL stays NULL.

No data is lost — every value 0/1 maps to False/True.

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Step 1: create a new boolean column and populate it from the int column.
    # Avoids the integer↔boolean coercion issue on a single column.
    op.add_column(
        "cars",
        sa.Column(
            "accident_report_available_new",
            sa.Boolean(),
            nullable=True,
        ),
    )
    op.execute(
        "UPDATE cars SET accident_report_available_new = "
        "(accident_records <> 0) WHERE accident_records IS NOT NULL"
    )
    op.drop_column("cars", "accident_records")
    op.alter_column(
        "cars",
        "accident_report_available_new",
        new_column_name="accident_report_available",
        existing_type=sa.Boolean(),
        existing_nullable=True,
    )


def downgrade() -> None:
    # Reverse the column-swap dance: create int, copy from bool (cast), drop bool.
    op.add_column(
        "cars",
        sa.Column("accident_records_new", sa.Integer(), nullable=True),
    )
    op.execute(
        "UPDATE cars SET accident_records_new = "
        "CASE WHEN accident_report_available IS NULL THEN NULL "
        "ELSE CASE WHEN accident_report_available THEN 1 ELSE 0 END END"
    )
    op.drop_column("cars", "accident_report_available")
    op.alter_column(
        "cars",
        "accident_records_new",
        new_column_name="accident_records",
        existing_type=sa.Integer(),
        existing_nullable=True,
    )
