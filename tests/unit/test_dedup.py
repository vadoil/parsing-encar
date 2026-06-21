"""Unit tests for the dedup module.

The dedup module groups car listings by (brand, model, year_month,
mileage_km, color_original) and marks the freshest listing in each group
(is_primary=true). Older duplicate listings stay in the DB but get
hidden from the vitrine. The photo-URL check is a secondary signal for
cars that miss one of the key fields.

Why these tests matter
----------------------
- grouping: the key must catch real duplicates (42209462 / 42213576 in
  the live DB) and never collapse two distinct cars.
- max(encar_id): primary is always the freshest listing.
- idempotency: running dedup twice in a row produces the same result.
- photo URL fallback: catches cars with missing key fields (e.g.
  year_month NULL) that nonetheless share every photo.
- vitrine filter: ``/`` and ``/catalog`` only show is_primary=true.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from encar_parser.db.models import Base, Car
from encar_parser.dedup import (
    DedupReport,
    group_by_key,
    make_key,
    photo_groups,
    run_dedup,
    select_primary,
)


# ── helpers ────────────────────────────────────────────────────────────


def _mkcar(
    encar_id: int,
    *,
    brand: str = "BMW",
    model: str = "X5 (G05)",
    year_month: date | None = date(2024, 1, 1),
    mileage_km: int | None = 10000,
    color_original: str | None = "흰색",
    photo_urls: list[str] | None = None,
) -> Car:
    """Build a Car with only the columns dedup cares about."""
    return Car(
        encar_id=encar_id,
        brand=brand,
        model=model,
        year_month=year_month,
        mileage_km=mileage_km,
        color_original=color_original,
        photo_urls=photo_urls,
        last_seen_at=datetime.now(UTC),
    )


async def _session_with(cars: list[Car]):
    """In-memory aiosqlite sessionmaker preloaded with `cars`."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        for c in cars:
            s.add(c)
        await s.commit()
    return engine, Session


# ── pure helpers ───────────────────────────────────────────────────────


def test_make_key_all_fields_present():
    c = _mkcar(1)
    assert make_key(c) == ("BMW", "X5 (G05)", date(2024, 1, 1), 10000, "흰색")


@pytest.mark.parametrize(
    "year_month,mileage_km,color_original",
    [
        (None, 10000, "흰색"),
        (date(2024, 1, 1), None, "흰색"),
        (date(2024, 1, 1), 10000, None),
    ],
)
def test_make_key_returns_none_when_any_field_null(year_month, mileage_km, color_original):
    c = _mkcar(1, year_month=year_month, mileage_km=mileage_km, color_original=color_original)
    assert make_key(c) is None


def test_select_primary_returns_max_encar_id():
    a = _mkcar(100)
    b = _mkcar(300)
    c = _mkcar(200)
    assert select_primary([a, b, c]).encar_id == 300


def test_select_primary_singleton_returns_that_car():
    a = _mkcar(42)
    assert select_primary([a]).encar_id == 42


def test_group_by_key_collapses_duplicates():
    a = _mkcar(100, mileage_km=5000, color_original="흰색")
    b = _mkcar(200, mileage_km=5000, color_original="흰색")
    c = _mkcar(300, mileage_km=9999, color_original="흰색")  # different mileage
    groups = group_by_key([a, b, c])
    # a and b share a key; c is unique.
    assert sorted(g.encar_id for g in groups[make_key(a)]) == [100, 200]
    assert groups[make_key(c)] == [c]


def test_group_by_key_skips_cars_with_null_fields():
    """A car missing any key field must NOT be grouped — the photo-URL pass handles it."""
    a = _mkcar(100, year_month=None)
    b = _mkcar(200, year_month=None)
    groups = group_by_key([a, b])
    assert groups == {}


def test_photo_groups_finds_identical_photo_sets():
    urls = ["https://x/a.jpg", "https://x/b.jpg", "https://x/c.jpg"]
    a = _mkcar(100, photo_urls=urls)
    b = _mkcar(200, photo_urls=list(urls))  # identical set
    c = _mkcar(300, photo_urls=["https://x/d.jpg"])
    groups = photo_groups([a, b, c])
    assert sorted(groups[frozenset(urls)], key=lambda x: x.encar_id) == [a, b]
    assert groups[frozenset(["https://x/d.jpg"])] == [c]


def test_photo_groups_skips_empty_or_null_photos():
    a = _mkcar(100, photo_urls=[])
    b = _mkcar(200, photo_urls=None)
    c = _mkcar(300, photo_urls=["https://x/a.jpg"])
    groups = photo_groups([a, b, c])
    # Empty / None photo sets are excluded — they would all collide on
    # frozenset() and falsely merge every car with no photos.
    assert groups == {frozenset({"https://x/a.jpg"}): [c]}


# ── run_dedup (integration with an in-memory DB) ───────────────────────


@pytest.mark.asyncio
async def test_run_dedup_marks_older_as_hidden():
    """Two cars sharing the 5-tuple → newer stays primary, older is hidden."""
    older = _mkcar(42071592, mileage_km=41173)
    newer = _mkcar(42083155, mileage_km=41173)
    engine, Session = await _session_with([older, newer])

    async with Session() as s:
        report = await run_dedup(s)
        await s.commit()

    assert report.duplicate_groups == 1
    assert report.rows_hidden == 1
    assert report.rows_primary == 1

    async with Session() as s:
        from sqlalchemy import select
        cars = (await s.scalars(select(Car).order_by(Car.encar_id))).all()
    assert [(c.encar_id, c.is_primary) for c in cars] == [
        (42071592, False),  # older — hidden
        (42083155, True),   # newer — primary
    ]
    await engine.dispose()


@pytest.mark.asyncio
async def test_run_dedup_no_duplicates_all_primary():
    """Three distinct cars (different keys) → all three stay primary."""
    a = _mkcar(1, mileage_km=1000)
    b = _mkcar(2, mileage_km=2000)
    c = _mkcar(3, mileage_km=3000)
    engine, Session = await _session_with([a, b, c])

    async with Session() as s:
        report = await run_dedup(s)
        await s.commit()

    assert report.duplicate_groups == 0
    assert report.rows_hidden == 0
    assert report.rows_primary == 3
    async with Session() as s:
        from sqlalchemy import select
        cars = (await s.scalars(select(Car))).all()
    assert all(c.is_primary for c in cars)
    await engine.dispose()


@pytest.mark.asyncio
async def test_run_dedup_is_idempotent():
    """Running dedup twice yields the same state and the same report."""
    older = _mkcar(100, mileage_km=5000)
    newer = _mkcar(200, mileage_km=5000)
    engine, Session = await _session_with([older, newer])

    async with Session() as s:
        report1 = await run_dedup(s)
        await s.commit()
    async with Session() as s:
        report2 = await run_dedup(s)
        await s.commit()

    assert report1.duplicate_groups == report2.duplicate_groups == 1
    assert report1.rows_hidden == report2.rows_hidden == 1
    assert report1.rows_primary == report2.rows_primary == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_run_dedup_handles_null_year_month_via_photos():
    """Two cars with NULL year_month but identical photo URLs → still merged."""
    urls = ["https://x/1.jpg", "https://x/2.jpg", "https://x/3.jpg"]
    a = _mkcar(100, year_month=None, photo_urls=list(urls))
    b = _mkcar(200, year_month=None, photo_urls=list(urls))
    engine, Session = await _session_with([a, b])

    async with Session() as s:
        report = await run_dedup(s)
        await s.commit()

    assert report.duplicate_groups == 1
    assert report.rows_hidden == 1
    async with Session() as s:
        from sqlalchemy import select
        cars = (await s.scalars(select(Car).order_by(Car.encar_id))).all()
    assert [(c.encar_id, c.is_primary) for c in cars] == [(100, False), (200, True)]
    await engine.dispose()


@pytest.mark.asyncio
async def test_run_dedup_real_example_42209462_42213576():
    """The exact duplicate pair from the user's prompt.

    Both rows are BMW X5 (G05), year_month 2025-12-01, mileage 4645 km,
    color 청색, identical photo sets. 42213576 (the larger ID) must
    win the primary slot.
    """
    photos = [f"https://img.encar.com/x/{i}.jpg" for i in range(3)]
    older = _mkcar(
        42209462, brand="BMW", model="X5 (G05)",
        year_month=date(2025, 12, 1), mileage_km=4645, color_original="청색",
        photo_urls=list(photos),
    )
    newer = _mkcar(
        42213576, brand="BMW", model="X5 (G05)",
        year_month=date(2025, 12, 1), mileage_km=4645, color_original="청색",
        photo_urls=list(photos),
    )
    engine, Session = await _session_with([older, newer])

    async with Session() as s:
        report = await run_dedup(s)
        await s.commit()

    assert report.duplicate_groups == 1
    assert report.rows_hidden == 1
    async with Session() as s:
        from sqlalchemy import select
        cars = (await s.scalars(select(Car).order_by(Car.encar_id))).all()
    assert [(c.encar_id, c.is_primary) for c in cars] == [
        (42209462, False),
        (42213576, True),
    ]
    await engine.dispose()


@pytest.mark.asyncio
async def test_run_dedup_does_not_merge_similar_cars():
    """Cars differing only in mileage are NOT duplicates."""
    a = _mkcar(1, mileage_km=10000)
    b = _mkcar(2, mileage_km=10001)  # one km off
    engine, Session = await _session_with([a, b])

    async with Session() as s:
        report = await run_dedup(s)
        await s.commit()

    assert report.duplicate_groups == 0
    async with Session() as s:
        from sqlalchemy import select
        cars = (await s.scalars(select(Car))).all()
    assert all(c.is_primary for c in cars)
    await engine.dispose()


@pytest.mark.asyncio
async def test_run_dedup_newcomer_flips_old_primary_to_hidden():
    """If a new (higher-id) duplicate arrives, re-running dedup must
    hand the primary slot to the newcomer and hide the older row.

    This is the idempotency-with-mutations property: dedup must
    recompute primary from current data, not from previous is_primary
    flags.
    """
    engine, Session = await _session_with([
        _mkcar(100, mileage_km=5000),
        _mkcar(200, mileage_km=5000),
    ])

    # First pass: 200 wins.
    async with Session() as s:
        await run_dedup(s)
        await s.commit()
    async with Session() as s:
        from sqlalchemy import select
        cars = (await s.scalars(select(Car).order_by(Car.encar_id))).all()
    assert [(c.encar_id, c.is_primary) for c in cars] == [(100, False), (200, True)]

    # Newcomer 300 arrives in the same group.
    async with Session() as s:
        s.add(_mkcar(300, mileage_km=5000))
        await s.commit()
        await run_dedup(s)
        await s.commit()

    async with Session() as s:
        from sqlalchemy import select
        cars = (await s.scalars(select(Car).order_by(Car.encar_id))).all()
    # 300 is now primary, 200 falls back to hidden.
    assert [(c.encar_id, c.is_primary) for c in cars] == [
        (100, False), (200, False), (300, True),
    ]
    await engine.dispose()
