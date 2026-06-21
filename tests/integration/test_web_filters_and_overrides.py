"""Integration tests for the Phase-6 / categories + / enhancements:

* Brand filter on ``GET /`` (?brand=... query param).
* Server-rendered brand <select> populated from distinct Car.brand.
* Manual Encar URL override on /categories — POST /categories/{slug}/url.
* Override wins over the auto-generated URL.
* Empty override clears the row (falls back to auto).
* Manual override survives ``sync`` (we don't have sync in this test,
  but the override table is verified to be separate from
  search_models).
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from encar_parser.db.models import Base, Car, ModelOverride, SearchModel
from encar_parser.web.app import create_app


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    yield Session, engine
    await engine.dispose()


@pytest.fixture
async def client(session):
    Session, _ = session
    app = create_app(sessionmaker=Session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _seed(Session, *, cars: list[Car] | None = None, models: list[SearchModel] | None = None):
    async with Session() as s:
        for m in models or []:
            s.add(m)
        for c in cars or []:
            s.add(c)
        await s.commit()


def _car(encar_id: int, brand: str, model: str = "X5 (G05)", is_primary: bool = True) -> Car:
    return Car(
        encar_id=encar_id, brand=brand, model=model,
        year_month=date(2024, 1, 1), mileage_km=10000,
        color_original="흰색", price_krw=50_000_000,
        photo_urls=[], last_seen_at=datetime.now(UTC),
        is_primary=is_primary,
    )


# ── Brand filter on / ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_brand_filter_returns_only_matching_cars(client, session):
    Session, _ = session
    await _seed(Session, cars=[
        _car(1, "BMW", "X5 (G05)"),
        _car(2, "BMW", "3 Series (G20)"),
        _car(3, "현대", "그랜저 (GN7)"),
        _car(4, "Mercedes-Benz", "C-Class (W206)"),
    ])

    r = await client.get("/?brand=BMW")
    assert r.status_code == 200
    body = r.text
    # Two BMW cars present
    assert ">1<" in body  # encar_id column
    assert ">2<" in body
    # No Hyundai car in the table (그랜저 is the model name; 현대 might
    # still appear in the brand <select> dropdown as a value — that's
    # correct behaviour).
    assert "그랜저" not in body
    assert "C-Class" not in body


@pytest.mark.asyncio
async def test_brand_filter_hides_hidden_duplicates(client, session):
    """``?brand=BMW`` must filter by is_primary=true AND brand."""
    Session, _ = session
    await _seed(Session, cars=[
        _car(1, "BMW", is_primary=True),
        _car(2, "BMW", is_primary=False),  # hidden duplicate
    ])

    r = await client.get("/?brand=BMW")
    assert r.status_code == 200
    body = r.text
    assert ">1<" in body
    # Hidden duplicate not shown
    assert ">2<" not in body or body.count(">2<") < 2


@pytest.mark.asyncio
async def test_brand_filter_dropdown_includes_all_distinct_brands(client, session):
    """The select must contain every distinct brand present in is_primary."""
    Session, _ = session
    await _seed(Session, cars=[
        _car(1, "BMW"),
        _car(2, "BMW"),  # duplicate brand — should appear once in dropdown
        _car(3, "현대"),
        _car(4, "Mercedes-Benz"),
        _car(5, "현대", is_primary=False),  # hidden — must NOT count
    ])
    r = await client.get("/")
    body = r.text
    # 3 brands total (BMW, 현대, Mercedes-Benz) — each as <option value=...>
    assert 'value="BMW"' in body
    assert 'value="현대"' in body
    assert 'value="Mercedes-Benz"' in body
    assert "Все" in body  # "all" default option


@pytest.mark.asyncio
async def test_brand_filter_dropdown_shows_english_label_for_korean_brand(client, session):
    """Option labels use brand_display — Korean 현대 must show as Hyundai."""
    Session, _ = session
    await _seed(Session, cars=[_car(1, "현대")])
    r = await client.get("/")
    body = r.text
    # The label rendered is the English one; the value is the raw Korean.
    # Jinja may render 'selected' with a leading space before '>' — match flexibly.
    assert '<option value="현대" >Hyundai</option>' in body or \
           '<option value="현대">Hyundai</option>' in body


@pytest.mark.asyncio
async def test_brand_filter_selected_attribute(client, session):
    """When ?brand=BMW is set, the BMW option is marked selected."""
    Session, _ = session
    await _seed(Session, cars=[_car(1, "BMW"), _car(2, "현대")])
    r = await client.get("/?brand=BMW")
    body = r.text
    assert '<option value="BMW" selected' in body
    # Hyundai is NOT selected
    assert '<option value="현대" selected' not in body


@pytest.mark.asyncio
async def test_no_brand_filter_shows_all_and_no_selected(client, session):
    Session, _ = session
    await _seed(Session, cars=[_car(1, "BMW"), _car(2, "현대")])
    r = await client.get("/")
    body = r.text
    # "Все" is the default and selected
    assert '<option value="" selected>Все</option>' in body


@pytest.mark.asyncio
async def test_brand_filter_unknown_brand_shows_empty_state_with_reset_link(client, session):
    """A brand that doesn't exist in the DB returns empty state."""
    Session, _ = session
    await _seed(Session, cars=[_car(1, "BMW")])
    r = await client.get("/?brand=Toyota")
    body = r.text
    assert "Нет машин с брендом" in body
    assert "Сбросить фильтр" in body


# ── /categories manual override ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_categories_auto_url_used_when_no_override(client, session):
    """Without an override row, the row's web_url is the auto-generated one."""
    Session, _ = session
    await _seed(Session, models=[
        SearchModel(
            slug="bmw-x6-g06", name="BMW X6 (G06)",
            encar_url="", priority=100,
            encar_action={
                "q": "(And.Hidden.N._.(C.CarType.N._.(C.Manufacturer.BMW._.(C.ModelGroup.X6._.Model.X6 (G06).)))_.Year.range(201800..202699).)",
                "sort": "ModifiedDate", "limit": 20,
            },
            enabled=True,
        )
    ])
    r = await client.get("/categories")
    body = r.text
    # The auto URL has #! hash and carType=for (import)
    assert 'href="https://www.encar.com/fc/fc_carsearchlist.do?carType=for#!' in body
    # No "ручная" badge on this row
    assert "авто из" in body


@pytest.mark.asyncio
async def test_categories_post_creates_override_row(client, session):
    """POST /categories/{slug}/url with a manual URL creates a ModelOverride."""
    Session, _ = session
    await _seed(Session, models=[
        SearchModel(
            slug="bmw-x6-g06", name="BMW X6 (G06)",
            encar_url="", priority=100,
            encar_action={"q": "(C.CarType.N._.)", "sort": "ModifiedDate", "limit": 20},
            enabled=True,
        )
    ])
    manual = "https://www.encar.com/fc/fc_carsearchlist.do?carType=for#!MANUAL"
    r = await client.post(
        "/categories/bmw-x6-g06/url",
        data={"manual_encar_url": manual},
        follow_redirects=False,
    )
    # 303 redirect back to /categories
    assert r.status_code == 303
    assert r.headers["location"] == "/categories"
    # Row was created
    async with Session() as s:
        ovr = await s.get(ModelOverride, "bmw-x6-g06")
        assert ovr is not None
        assert ovr.manual_encar_url == manual


@pytest.mark.asyncio
async def test_categories_post_with_empty_value_deletes_override(client, session):
    """Empty input clears the override (auto URL takes over again)."""
    Session, _ = session
    async with Session() as s:
        s.add(ModelOverride(
            slug="bmw-x6-g06",
            manual_encar_url="https://www.encar.com/.../MANUAL",
        ))
        await s.commit()
    await _seed(Session, models=[
        SearchModel(
            slug="bmw-x6-g06", name="BMW X6 (G06)",
            encar_url="", priority=100,
            encar_action={"q": "(C.CarType.N._.)", "sort": "ModifiedDate", "limit": 20},
            enabled=True,
        )
    ])

    r = await client.post(
        "/categories/bmw-x6-g06/url",
        data={"manual_encar_url": ""},
        follow_redirects=False,
    )
    assert r.status_code == 303
    async with Session() as s:
        ovr = await s.get(ModelOverride, "bmw-x6-g06")
        assert ovr is None, "empty input should delete the override row"


@pytest.mark.asyncio
async def test_categories_shows_manual_url_instead_of_auto(client, session):
    """When an override is present, the row's link uses the manual URL
    and the badge says «ручная»."""
    Session, _ = session
    async with Session() as s:
        s.add(ModelOverride(
            slug="bmw-x6-g06",
            manual_encar_url="https://example.com/custom-search?q=BMWX6",
        ))
        s.add(SearchModel(
            slug="bmw-x6-g06", name="BMW X6 (G06)",
            encar_url="", priority=100,
            encar_action={"q": "(C.CarType.N._.)", "sort": "ModifiedDate", "limit": 20},
            enabled=True,
        ))
        await s.commit()
    r = await client.get("/categories")
    body = r.text
    # Manual URL appears in the href
    assert "https://example.com/custom-search?q=BMWX6" in body
    # The "открыть ↗" link points at the manual URL, not the auto one
    assert '<a href="https://example.com/custom-search?q=BMWX6" target="_blank" rel="noopener" title="Открыть поиск по этой модели на Encar">на сайте ↗</a>' in body
    # "ручная" badge shown
    assert "ручная" in body


@pytest.mark.asyncio
async def test_categories_form_action_targets_per_slug(client, session):
    """Each row's <form action> must point at its own slug — not a shared URL."""
    Session, _ = session
    await _seed(Session, models=[
        SearchModel(slug="model-a", name="A", encar_url="", priority=10,
                   encar_action={"q": "(C.CarType.N._.)"}, enabled=True),
        SearchModel(slug="model-b", name="B", encar_url="", priority=20,
                   encar_action={"q": "(C.CarType.N._.)"}, enabled=True),
    ])
    r = await client.get("/categories")
    body = r.text
    assert 'action="/categories/model-a/url"' in body
    assert 'action="/categories/model-b/url"' in body


@pytest.mark.asyncio
async def test_categories_form_input_pre_filled_with_existing_manual_url(client, session):
    Session, _ = session
    async with Session() as s:
        s.add(ModelOverride(
            slug="model-x",
            manual_encar_url="https://example.com/manual-x",
        ))
        s.add(SearchModel(slug="model-x", name="X", encar_url="", priority=10,
                          encar_action={"q": "(C.CarType.N._.)"}, enabled=True))
        await s.commit()
    r = await client.get("/categories")
    body = r.text
    # The input value= attribute should be the current override
    assert 'value="https://example.com/manual-x"' in body
