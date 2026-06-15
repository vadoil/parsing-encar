"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-06-15

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # search_models
    op.create_table(
        "search_models",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("encar_url", sa.Text(), nullable=False),
        sa.Column("encar_action", postgresql.JSON(astext_type=sa.Text()), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )

    # cars
    op.create_table(
        "cars",
        sa.Column("encar_id", sa.BigInteger(), nullable=False),
        sa.Column("brand", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("year_month", sa.Date(), nullable=True),
        sa.Column("mileage_km", sa.Integer(), nullable=True),
        sa.Column("displacement_cc", sa.Integer(), nullable=True),
        sa.Column("fuel_ru", sa.Text(), nullable=True),
        sa.Column("fuel_original", sa.Text(), nullable=True),
        sa.Column("transmission_ru", sa.Text(), nullable=True),
        sa.Column("transmission_orig", sa.Text(), nullable=True),
        sa.Column("body_type", sa.Text(), nullable=True),
        sa.Column("color_ru", sa.Text(), nullable=True),
        sa.Column("color_original", sa.Text(), nullable=True),
        sa.Column("seats", sa.Integer(), nullable=True),
        sa.Column("import_type_ru", sa.Text(), nullable=True),
        sa.Column("manufacturer_warranty", sa.Text(), nullable=True),
        sa.Column("liens_seizures", sa.Text(), nullable=True),
        sa.Column("accident_records", sa.Integer(), nullable=True),
        sa.Column("plate_number", sa.Text(), nullable=True),
        sa.Column("price_krw", sa.BigInteger(), nullable=True),
        sa.Column("photo_urls", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column("encar_detail_url", sa.Text(), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_data", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("encar_id"),
    )
    op.create_index("idx_cars_brand_model", "cars", ["brand", "model"], unique=False)
    op.create_index("idx_cars_year_month", "cars", ["year_month"], unique=False)
    op.create_index("idx_cars_price_krw", "cars", ["price_krw"], unique=False)

    # car_model_matches
    op.create_table(
        "car_model_matches",
        sa.Column(
            "search_model_id", sa.Integer(), nullable=False
        ),
        sa.Column("encar_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "first_matched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_matched_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["encar_id"], ["cars.encar_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["search_model_id"], ["search_models.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("search_model_id", "encar_id"),
    )
    op.create_index("idx_matches_model", "car_model_matches", ["search_model_id"], unique=False)

    # runs
    op.create_table(
        "runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("models_planned", sa.Integer(), nullable=False),
        sa.Column("models_done", sa.Integer(), nullable=False),
        sa.Column("cars_fetched", sa.Integer(), nullable=False),
        sa.Column("cars_failed", sa.Integer(), nullable=False),
        sa.Column("error_log", postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("runs")
    op.drop_index("idx_matches_model", table_name="car_model_matches")
    op.drop_table("car_model_matches")
    op.drop_index("idx_cars_price_krw", table_name="cars")
    op.drop_index("idx_cars_year_month", table_name="cars")
    op.drop_index("idx_cars_brand_model", table_name="cars")
    op.drop_table("cars")
    op.drop_table("search_models")
