"""SQLAlchemy ORM models for the encar parser."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class SearchModel(Base):
    __tablename__ = "search_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    encar_url: Mapped[str] = mapped_column(Text, nullable=False)
    encar_action: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    matches: Mapped[list[CarModelMatch]] = relationship(
        back_populates="search_model", cascade="all, delete-orphan"
    )


class Car(Base):
    __tablename__ = "cars"

    encar_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    brand: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    year_month: Mapped[date | None] = mapped_column(Date)
    mileage_km: Mapped[int | None] = mapped_column(Integer)
    displacement_cc: Mapped[int | None] = mapped_column(Integer)

    fuel_ru: Mapped[str | None] = mapped_column(Text)
    fuel_original: Mapped[str | None] = mapped_column(Text)
    transmission_ru: Mapped[str | None] = mapped_column(Text)
    transmission_orig: Mapped[str | None] = mapped_column(Text)
    body_type: Mapped[str | None] = mapped_column(Text)

    color_ru: Mapped[str | None] = mapped_column(Text)
    color_original: Mapped[str | None] = mapped_column(Text)

    seats: Mapped[int | None] = mapped_column(Integer)
    import_type_ru: Mapped[str | None] = mapped_column(Text)
    manufacturer_warranty: Mapped[str | None] = mapped_column(Text)

    liens_seizures: Mapped[str | None] = mapped_column(Text)
    # True when `condition.accident.recordView` is true — i.e. a vehicle
    # history report is available for this listing on the Encar page.
    # Not an accident count. See encar-open-questions.md for why this is
    # boolean and why real insurance history is not in this API.
    accident_report_available: Mapped[bool | None] = mapped_column(Boolean)
    plate_number: Mapped[str | None] = mapped_column(Text)

    price_krw: Mapped[int | None] = mapped_column(BigInteger)
    photo_urls: Mapped[list[str] | None] = mapped_column(JSON)
    encar_detail_url: Mapped[str | None] = mapped_column(Text)

    # True iff this row is the freshest listing of its physical car
    # (largest ``encar_id`` within its duplicate group). The vitrine,
    # ``/catalog`` and the CRM filter on this. See :mod:`encar_parser.dedup`.
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true", default=True,
    )

    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    matches: Mapped[list[CarModelMatch]] = relationship(
        back_populates="car", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_cars_brand_model", "brand", "model"),
        Index("idx_cars_year_month", "year_month"),
        Index("idx_cars_price_krw", "price_krw"),
        # Partial index keeps the vitrine query fast: the web view only
        # reads is_primary=True rows, and only the latest last_seen_at.
        # Declared here so the model matches what the migration created
        # (alembic stays the source of truth for the partial WHERE).
        Index("idx_cars_is_primary", "is_primary", "last_seen_at"),
    )


class CarModelMatch(Base):
    __tablename__ = "car_model_matches"

    search_model_id: Mapped[int] = mapped_column(
        ForeignKey("search_models.id", ondelete="CASCADE"), primary_key=True
    )
    encar_id: Mapped[int] = mapped_column(
        ForeignKey("cars.encar_id", ondelete="CASCADE"), primary_key=True
    )
    first_matched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    search_model: Mapped[SearchModel] = relationship(back_populates="matches")
    car: Mapped[Car] = relationship(back_populates="matches")

    __table_args__ = (Index("idx_matches_model", "search_model_id"),)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    models_planned: Mapped[int] = mapped_column(Integer, default=0)
    models_done: Mapped[int] = mapped_column(Integer, default=0)
    cars_fetched: Mapped[int] = mapped_column(Integer, default=0)
    cars_failed: Mapped[int] = mapped_column(Integer, default=0)
    error_log: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
