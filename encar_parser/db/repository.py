"""Repository functions for the encar parser. Thin wrappers over SQLAlchemy."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from encar_parser.db.models import Car, CarModelMatch, SearchModel


async def upsert_search_model(
    session: AsyncSession,
    *,
    slug: str,
    name: str,
    encar_url: str,
    encar_action: dict[str, Any],
    enabled: bool = True,
    priority: int = 100,
) -> SearchModel:
    """Create or update a SearchModel identified by slug. Returns the model."""
    existing = await session.scalar(select(SearchModel).where(SearchModel.slug == slug))
    if existing is None:
        sm = SearchModel(
            slug=slug,
            name=name,
            encar_url=encar_url,
            encar_action=encar_action,
            enabled=enabled,
            priority=priority,
        )
        session.add(sm)
    else:
        existing.name = name
        existing.encar_url = encar_url
        existing.encar_action = encar_action
        existing.enabled = enabled
        existing.priority = priority
        sm = existing
    await session.commit()
    await session.refresh(sm)
    return sm


async def upsert_car(
    session: AsyncSession,
    *,
    encar_id: int,
    brand: str,
    model: str,
    **fields: Any,
) -> Car:
    """Create or update a Car. Pass any column name as a kwarg.

    On BOTH insert and update, ``last_seen_at`` is set to now(). This is what
    makes the "sold" detection work: a car that disappears from the API will
    keep its last_seen_at from the last successful run; we can then query for
    cars where last_seen_at < (today - N days) to find listings that are no
    longer being returned by encar.
    """
    now = datetime.now(timezone.utc)
    existing = await session.scalar(select(Car).where(Car.encar_id == encar_id))
    if existing is None:
        car = Car(
            encar_id=encar_id,
            brand=brand,
            model=model,
            last_seen_at=now,
            **fields,
        )
        session.add(car)
    else:
        existing.brand = brand
        existing.model = model
        for key, value in fields.items():
            setattr(existing, key, value)
        existing.last_seen_at = now
        car = existing
    await session.commit()
    await session.refresh(car)
    return car


async def link_car_to_model(
    session: AsyncSession, *, search_model_id: int, encar_id: int
) -> None:
    """Create a (model, car) match if it does not exist; update last_matched_at."""
    existing = await session.scalar(
        select(CarModelMatch).where(
            CarModelMatch.search_model_id == search_model_id,
            CarModelMatch.encar_id == encar_id,
        )
    )
    now = datetime.now(timezone.utc)
    if existing is None:
        session.add(
            CarModelMatch(
                search_model_id=search_model_id,
                encar_id=encar_id,
                first_matched_at=now,
                last_matched_at=now,
            )
        )
    else:
        existing.last_matched_at = now
    await session.commit()


async def get_enabled_models(session: AsyncSession) -> list[SearchModel]:
    """Return all enabled search models sorted by (priority, slug)."""
    result = await session.scalars(
        select(SearchModel)
        .where(SearchModel.enabled.is_(True))
        .order_by(SearchModel.priority, SearchModel.slug)
    )
    return list(result.all())


class Repository:
    """Convenience wrapper that bundles session + operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def upsert_search_model(self, **kwargs: Any) -> SearchModel:
        return await upsert_search_model(self.session, **kwargs)

    async def upsert_car(self, **kwargs: Any) -> Car:
        return await upsert_car(self.session, **kwargs)

    async def link_car_to_model(self, **kwargs: Any) -> None:
        await link_car_to_model(self.session, **kwargs)

    async def get_enabled_models(self) -> list[SearchModel]:
        return await get_enabled_models(self.session)
