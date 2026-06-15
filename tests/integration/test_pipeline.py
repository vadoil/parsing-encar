import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from encar_parser.db.models import Base, Car, CarModelMatch, SearchModel
from encar_parser.encar_url import ModelConfig
from encar_parser.fetchers.api import ApiFetcher
from encar_parser.pipeline import run_model


def _list_payload() -> dict:
    return {
        "SearchResults": {
            "EncarSearchResults": [
                {"Id": 42131435, "Manufacturer": "BMW", "Model": "X5 (G05)"},
            ]
        }
    }


def _detail_payload() -> dict:
    return {
        "car": {
            "manufacturer": "BMW",
            "model": "X5 (G05)",
            "vehicleNo": "158바6820",
            "year": "2025-11",
            "mileage": "4,027",
            "displacement": "2998",
            "fuel": {"name": "가솔린"},
            "transmission": {"name": "오토"},
            "bodyType": "SUV",
            "color": {"name": "검정색"},
            "seats": "5",
            "importType": {"name": "정식수입"},
            "manufacturerWarranty": "BMW",
            "liens": "0건",
            "seizures": "0건",
            "accidentRecords": 0,
            "price": "128500000",
            "photos": ["https://img.encar.com/x.jpg"],
        }
    }


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        yield s


@pytest.mark.asyncio
@respx.mock
async def test_run_model_inserts_car_and_link(session):
    sm = SearchModel(
        id=1, slug="bmw-x5-g05", name="BMW X5 (G05)",
        encar_url="https://example.com", encar_action={}
    )
    session.add(sm)
    await session.commit()

    # First call returns the list, second call returns the detail
    respx.get("https://api.encar.com/search/list").mock(
        return_value=httpx.Response(200, json=_list_payload())
    )
    respx.get("https://fem.encar.com/cars/detail/42131435").mock(
        return_value=httpx.Response(200, json=_detail_payload())
    )

    fetcher = ApiFetcher()
    await fetcher.__aenter__()
    try:
        cars_count = await run_model(
            sm, fetcher=fetcher, session=session,
            list_url="https://api.encar.com/search/list",
            detail_url_template="https://fem.encar.com/cars/detail/{encar_id}",
        )
    finally:
        await fetcher.__aexit__(None, None, None)

    assert cars_count == 1
    cars = (await session.execute(select(Car))).scalars().all()
    assert len(cars) == 1
    matches = (await session.execute(select(CarModelMatch))).scalars().all()
    assert len(matches) == 1
