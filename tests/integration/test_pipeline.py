import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from encar_parser.db.models import Base, Car, CarModelMatch, SearchModel
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
            list_url_for_page=lambda page: f"https://api.encar.com/search/list?page={page}",
            detail_url_template="https://fem.encar.com/cars/detail/{encar_id}",
        )
    finally:
        await fetcher.__aexit__(None, None, None)

    assert cars_count == 1
    cars = (await session.execute(select(Car))).scalars().all()
    assert len(cars) == 1
    matches = (await session.execute(select(CarModelMatch))).scalars().all()
    assert len(matches) == 1


def _page_payload(start_id: int, count: int, total: int | None = None) -> dict:
    """Build a list-page response with `count` items starting at id `start_id`."""
    return {
        "Count": total if total is not None else count,
        "SearchResults": [
            {"Id": str(start_id + i), "Manufacturer": "BMW", "Model": "X5 (G05)"}
            for i in range(count)
        ],
    }


def _detail_for_id(encar_id: int) -> dict:
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


@pytest.mark.asyncio
@respx.mock
async def test_run_model_paginates_through_pages(session):
    """Two full pages (20 each) + one short page (5) → all 45 cars collected."""
    sm = SearchModel(
        id=1, slug="bmw-x5-g05", name="BMW X5 (G05)",
        encar_url="https://example.com", encar_action={}
    )
    session.add(sm)
    await session.commit()

    # Page 1: 20 items (ids 1..20), Page 2: 20 items (ids 21..40),
    # Page 3: 5 items (ids 41..45). Reported Count=45.
    respx.get("https://api.encar.com/search/list", params={"page": "1"}).mock(
        return_value=httpx.Response(200, json=_page_payload(1, 20, total=45))
    )
    respx.get("https://api.encar.com/search/list", params={"page": "2"}).mock(
        return_value=httpx.Response(200, json=_page_payload(21, 20, total=45))
    )
    respx.get("https://api.encar.com/search/list", params={"page": "3"}).mock(
        return_value=httpx.Response(200, json=_page_payload(41, 5, total=45))
    )
    respx.get("https://api.encar.com/search/list", params={"page": "4"}).mock(
        return_value=httpx.Response(200, json=_page_payload(100, 0, total=45))
    )

    # Mock all 45 detail endpoints.
    for cid in range(1, 46):
        respx.get(f"https://fem.encar.com/cars/detail/{cid}").mock(
            return_value=httpx.Response(200, json=_detail_for_id(cid))
        )

    fetcher = ApiFetcher()
    await fetcher.__aenter__()
    try:
        cars_count = await run_model(
            sm, fetcher=fetcher, session=session,
            list_url_for_page=lambda page: f"https://api.encar.com/search/list?page={page}",
            detail_url_template="https://fem.encar.com/cars/detail/{encar_id}",
            max_pages=10,
        )
    finally:
        await fetcher.__aexit__(None, None, None)

    assert cars_count == 45
    cars = (await session.execute(select(Car))).scalars().all()
    assert len(cars) == 45
    matches = (await session.execute(select(CarModelMatch))).scalars().all()
    assert len(matches) == 45


@pytest.mark.asyncio
@respx.mock
async def test_run_model_respects_max_pages_when_count_unknown(session):
    """When encar returns no Count (or non-stopping short pages), max_pages caps."""
    sm = SearchModel(
        id=1, slug="bmw-x5-g05", name="BMW X5 (G05)",
        encar_url="https://example.com", encar_action={}
    )
    session.add(sm)
    await session.commit()

    # Every page returns 20 items with NO Count field. The pipeline should stop
    # after max_pages=2 even though the API never signals "end of results".
    # IDs: page 1 → 1..20, page 2 → 21..40.
    for page in range(1, 10):
        start = (page - 1) * 20 + 1
        respx.get("https://api.encar.com/search/list", params={"page": str(page)}).mock(
            return_value=httpx.Response(200, json={
                "SearchResults": [
                    {"Id": str(start + i), "Manufacturer": "BMW", "Model": "X5 (G05)"}
                    for i in range(20)
                ],
            })
        )

    for cid in range(1, 41):
        respx.get(f"https://fem.encar.com/cars/detail/{cid}").mock(
            return_value=httpx.Response(200, json=_detail_for_id(cid))
        )

    fetcher = ApiFetcher()
    await fetcher.__aenter__()
    try:
        cars_count = await run_model(
            sm, fetcher=fetcher, session=session,
            list_url_for_page=lambda page: f"https://api.encar.com/search/list?page={page}",
            detail_url_template="https://fem.encar.com/cars/detail/{encar_id}",
            max_pages=2,
        )
    finally:
        await fetcher.__aexit__(None, None, None)

    # 2 pages × 20 items = 40
    assert cars_count == 40
    cars = (await session.execute(select(Car))).scalars().all()
    assert len(cars) == 40


@pytest.mark.asyncio
@respx.mock
async def test_run_model_stops_on_empty_page(session):
    """EncAr caps results at ~1000 and returns an empty page past it."""
    sm = SearchModel(
        id=1, slug="bmw-x5-g05", name="BMW X5 (G05)",
        encar_url="https://example.com", encar_action={}
    )
    session.add(sm)
    await session.commit()

    respx.get("https://api.encar.com/search/list", params={"page": "1"}).mock(
        return_value=httpx.Response(200, json=_page_payload(1, 20, total=1005))
    )
    respx.get("https://api.encar.com/search/list", params={"page": "2"}).mock(
        return_value=httpx.Response(200, json={"Count": 1005, "SearchResults": []})
    )

    for cid in range(1, 21):
        respx.get(f"https://fem.encar.com/cars/detail/{cid}").mock(
            return_value=httpx.Response(200, json=_detail_for_id(cid))
        )

    fetcher = ApiFetcher()
    await fetcher.__aenter__()
    try:
        cars_count = await run_model(
            sm, fetcher=fetcher, session=session,
            list_url_for_page=lambda page: f"https://api.encar.com/search/list?page={page}",
            detail_url_template="https://fem.encar.com/cars/detail/{encar_id}",
            max_pages=10,
        )
    finally:
        await fetcher.__aexit__(None, None, None)

    assert cars_count == 20
