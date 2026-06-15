import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from encar_parser.db.models import Car, CarModelMatch, Run, SearchModel


@pytest.mark.asyncio
async def test_can_create_all_tables():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Car.metadata.create_all)
    assert True  # no exception = success


@pytest.mark.asyncio
async def test_search_model_roundtrip():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Car.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        sm = SearchModel(
            slug="bmw-x5-g05",
            name="BMW X5 (G05)",
            encar_url="https://example.com",
            encar_action={"action": "(And.Hidden.N._.Manufacturer.BMW.)"},
            enabled=True,
            priority=10,
        )
        session.add(sm)
        await session.commit()
        await session.refresh(sm)
        assert sm.id is not None
        assert sm.created_at is not None


@pytest.mark.asyncio
async def test_car_and_match_linkage():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Car.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        sm = SearchModel(
            slug="x", name="X", encar_url="u", encar_action={}
        )
        car = Car(encar_id=42131435, brand="BMW", model="X5 (G05)")
        session.add_all([sm, car])
        await session.flush()
        match = CarModelMatch(search_model_id=sm.id, encar_id=car.encar_id)
        session.add(match)
        await session.commit()
        await session.refresh(match)
        assert match.first_matched_at is not None
