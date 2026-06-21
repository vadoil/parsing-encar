"""Car-level deduplication: collapse Encar duplicate listings into one row.

Background
----------
Encar often lists the same physical car under several ``encar_id`` values
(different listing pages, identical photos, identical specs). The vitrine
and the future CRM must show each physical car exactly once, but the DB
must keep every listing — analytics and price-history queries still want
them all.

Primary key
-----------
``(brand, model, year_month, mileage_km, color_original)`` with all five
non-NULL. Mileage rounded to the km is the strongest signal: the live DB
showed every key-match group also shares ``price_krw`` exactly, which
strongly suggests real duplicates (re-listings of the same vehicle).

Secondary signal
----------------
If two cars have NULL key fields but identical ``photo_urls`` sets, treat
them as duplicates too. This catches edge cases where Encar returns
partial data for one listing of an otherwise-known car.

Primary selection
-----------------
Inside a group, the row with the largest ``encar_id`` wins
(``is_primary = True``). Encar issues higher IDs to newer listings, so
"max id" ≈ "most recently posted".

Idempotency
-----------
``run_dedup`` always recomputes from current data — it does not read the
existing ``is_primary`` flags. This way, a newcomer with a higher
``encar_id`` correctly steals the primary slot from a previously-hidden
row. Re-running ``dedup`` after every parse is the supported pattern.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from encar_parser.db.models import Car


@dataclass(frozen=True)
class DedupReport:
    """Summary of a single ``run_dedup`` pass.

    ``duplicate_groups`` is the number of distinct groups that contained
    more than one car. ``rows_hidden`` is the count of cars the pass
    marked ``is_primary = False`` (i.e. the older duplicates).
    ``rows_primary`` is the count of cars left (or marked) ``is_primary =
    True`` — the number of unique cars visible in the vitrine.
    """

    duplicate_groups: int
    rows_hidden: int
    rows_primary: int

    def as_dict(self) -> dict[str, int]:
        return {
            "duplicate_groups": self.duplicate_groups,
            "rows_hidden": self.rows_hidden,
            "rows_primary": self.rows_primary,
        }


def make_key(car: Car) -> tuple[Any, ...] | None:
    """Return the 5-tuple dedup key for a car, or None if any field is missing.

    The key is the *only* thing the primary dedup pass compares on — keep
    it cheap to hash and order-insensitive. Brand/model are always
    non-NULL on real rows (DB NOT NULL), so we don't check them here.
    """
    if (
        car.year_month is None
        or car.mileage_km is None
        or car.color_original is None
    ):
        return None
    return (
        car.brand,
        car.model,
        car.year_month,
        car.mileage_km,
        car.color_original,
    )


def select_primary(group: list[Car]) -> Car:
    """Return the car with the largest ``encar_id`` in ``group``.

    Ties on encar_id are impossible — it's the primary key. If the group
    is empty the caller has a bug; we raise rather than silently return
    None to surface it.
    """
    if not group:
        raise ValueError("select_primary called with empty group")
    return max(group, key=lambda c: c.encar_id)


def group_by_key(cars: Iterable[Car]) -> Mapping[tuple[Any, ...], list[Car]]:
    """Bucket cars by their 5-tuple dedup key; skip cars with NULL fields."""
    groups: dict[tuple[Any, ...], list[Car]] = defaultdict(list)
    for c in cars:
        key = make_key(c)
        if key is not None:
            groups[key].append(c)
    return groups


def photo_groups(
    cars: Iterable[Car],
) -> Mapping[frozenset[str], list[Car]]:
    """Bucket cars by their ``photo_urls`` set; skip empty/None photos.

    An empty frozenset would collide with every other car that has no
    photos — that's a false-merge bug. We exclude those up front.
    """
    groups: dict[frozenset[str], list[Car]] = defaultdict(list)
    for c in cars:
        if not c.photo_urls:
            continue
        pset = frozenset(c.photo_urls)
        if not pset:
            continue
        groups[pset].append(c)
    return groups


def _collect_duplicates(
    cars: list[Car],
) -> tuple[set[int], set[int], int]:
    """Return (primary_ids, hidden_ids, group_count) for the whole DB.

    Walks every duplicate group (key-based + photo-based) and splits the
    members into winners (max encar_id) and losers (everyone else).
    Cars that don't end up in any duplicate group are NOT in either
    set — they remain primary by default.
    """
    primary_ids: set[int] = set()
    hidden_ids: set[int] = set()
    group_count = 0

    key_buckets = group_by_key(cars)
    key_car_ids: set[int] = set()
    for group in key_buckets.values():
        if len(group) > 1:
            group_count += 1
            winner = select_primary(group)
            primary_ids.add(winner.encar_id)
            for c in group:
                if c is not winner:
                    hidden_ids.add(c.encar_id)
                key_car_ids.add(c.encar_id)

    # Photo fallback: only consider cars the key pass already saw as
    # singletons (i.e. missed by the key) — never re-classify a row we
    # already decided on.
    unassigned = [c for c in cars if c.encar_id not in key_car_ids]
    for group in photo_groups(unassigned).values():
        if len(group) > 1:
            group_count += 1
            winner = select_primary(group)
            primary_ids.add(winner.encar_id)
            for c in group:
                if c is not winner:
                    hidden_ids.add(c.encar_id)

    return primary_ids, hidden_ids, group_count


async def run_dedup(session: AsyncSession) -> DedupReport:
    """Recompute ``is_primary`` for every car in the DB.

    Returns a :class:`DedupReport` summarising the pass. The DB is left
    in a state where every ``is_primary`` flag matches the current data
    — primary = True iff the row is the freshest listing in its
    duplicate group, False otherwise.

    Idempotent: re-running yields the same report and the same flags
    (assuming no rows changed in between).
    """
    cars = list((await session.scalars(select(Car))).all())
    all_ids = {c.encar_id for c in cars}
    winner_ids, hidden_ids, group_count = _collect_duplicates(cars)
    # Every non-hidden car is primary: winners + every singleton.
    primary_ids = all_ids - hidden_ids

    # Apply. Two bulk updates: reset everything, then mark primaries.
    # Recomputing from scratch every run is what makes this idempotent
    # and newcomer-safe.
    await session.execute(update(Car).values(is_primary=False))
    if primary_ids:
        await session.execute(
            update(Car).where(Car.encar_id.in_(primary_ids)).values(is_primary=True)
        )

    return DedupReport(
        duplicate_groups=group_count,
        rows_hidden=len(hidden_ids),
        rows_primary=len(all_ids) - len(hidden_ids),
    )
