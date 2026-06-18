import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from encar_parser.db.models import Base, Car, CarModelMatch, SearchModel
from encar_parser.db.repository import (
    Repository,
    upsert_car,
    upsert_search_model,
    link_car_to_model,
    get_enabled_models,
)


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        yield s


@pytest.mark.asyncio
async def test_upsert_search_model_creates(session):
    sm = await upsert_search_model(
        session,
        slug="bmw-x5-g05",
        name="BMW X5 (G05)",
        encar_url="https://example.com",
        encar_action={"action": "x"},
    )
    assert sm.id is not None
    assert sm.slug == "bmw-x5-g05"


@pytest.mark.asyncio
async def test_upsert_search_model_updates(session):
    sm1 = await upsert_search_model(session, slug="x", name="X", encar_url="u1", encar_action={})
    sm2 = await upsert_search_model(session, slug="x", name="X (renamed)", encar_url="u2", encar_action={})
    assert sm1.id == sm2.id
    assert sm2.name == "X (renamed)"


@pytest.mark.asyncio
async def test_upsert_car_creates(session):
    car = await upsert_car(session, encar_id=42131435, brand="BMW", model="X5 (G05)")
    assert car.encar_id == 42131435
    # last_seen_at must be set on insert (not just left to first_seen_at).
    assert car.last_seen_at is not None


@pytest.mark.asyncio
async def test_upsert_car_updates_existing(session):
    await upsert_car(session, encar_id=1, brand="BMW", model="X5")
    car = await upsert_car(session, encar_id=1, brand="BMW", model="X5 (G05)", price_krw=100000)
    assert car.model == "X5 (G05)"
    assert car.price_krw == 100000


@pytest.mark.asyncio
async def test_upsert_car_updates_last_seen_at_on_each_call(session):
    """The whole point of last_seen_at: detect sold cars.

    Cars that disappear from the API will keep their old last_seen_at; the
    difference between 'seen in last run' and 'seen N days ago' lets us
    surface listings that have been quietly delisted.
    """
    import asyncio
    await upsert_car(session, encar_id=42, brand="BMW", model="X5 (G05)")
    first = (await session.get(Car, 42)).last_seen_at
    assert first is not None

    # Sleep a hair so datetime.now() ticks.
    await asyncio.sleep(0.01)

    await upsert_car(session, encar_id=42, brand="BMW", model="X5 (G05)")
    second = (await session.get(Car, 42)).last_seen_at
    assert second is not None
    assert second > first, f"last_seen_at should advance on update; first={first} second={second}"


@pytest.mark.asyncio
async def test_link_car_to_model_idempotent(session):
    sm = await upsert_search_model(session, slug="x", name="X", encar_url="u", encar_action={})
    await upsert_car(session, encar_id=1, brand="B", model="M")
    await link_car_to_model(session, search_model_id=sm.id, encar_id=1)
    await link_car_to_model(session, search_model_id=sm.id, encar_id=1)  # second time
    from sqlalchemy import select, func
    result = await session.execute(
        select(func.count()).select_from(CarModelMatch)
    )
    assert result.scalar() == 1


@pytest.mark.asyncio
async def test_get_enabled_models_returns_sorted(session):
    await upsert_search_model(session, slug="b", name="B", encar_url="u", encar_action={}, priority=20)
    await upsert_search_model(session, slug="a", name="A", encar_url="u", encar_action={}, priority=10)
    await upsert_search_model(session, slug="c", name="C", encar_url="u", encar_action={}, enabled=False, priority=5)
    models = await get_enabled_models(session)
    assert [m.slug for m in models] == ["a", "b"]  # c is disabled
