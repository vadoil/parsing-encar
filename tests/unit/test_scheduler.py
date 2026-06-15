from datetime import date

import pytest

from encar_parser.encar_url import ModelConfig
from encar_parser.db.models import SearchModel
from encar_parser.scheduler import models_for_today


def _mk(slug: str, priority: int = 100) -> SearchModel:
    cfg = ModelConfig(slug=slug, name=slug, priority=priority)
    return SearchModel(
        slug=slug, name=slug, encar_url="", encar_action={}, priority=priority
    )


def test_models_for_today_divides_by_three_days():
    models = [_mk(f"m{i:02d}") for i in range(6)]
    # 2026-06-15 is a Monday (isoweekday=1, bucket=0)
    # bucket = (isoweekday - 1) % 3
    day1 = date(2026, 6, 15)  # Monday, bucket 0
    day2 = date(2026, 6, 16)  # Tuesday, bucket 1
    day3 = date(2026, 6, 17)  # Wednesday, bucket 2

    d1 = [m.slug for m in models_for_today(models, day1)]
    d2 = [m.slug for m in models_for_today(models, day2)]
    d3 = [m.slug for m in models_for_today(models, day3)]

    # Each model appears in exactly one bucket; coverage is full
    assert set(d1) | set(d2) | set(d3) == {m.slug for m in models}
    assert len(set(d1) & set(d2)) == 0
    assert len(set(d2) & set(d3)) == 0
    assert len(set(d1) & set(d3)) == 0


def test_models_for_today_deterministic():
    models = [_mk(f"m{i:02d}") for i in range(9)]
    day = date(2026, 6, 15)
    result1 = [m.slug for m in models_for_today(models, day)]
    result2 = [m.slug for m in models_for_today(models, day)]
    assert result1 == result2


def test_models_for_today_respects_priority():
    models = [_mk("z_high", priority=10), _mk("a_low", priority=99)]
    day = date(2026, 6, 15)  # bucket 0
    result = [m.slug for m in models_for_today(models, day)]
    # z_high has priority 10, a_low 99, so z_high comes first within bucket
    # bucket 0 contains even-indexed models after sort
    # after sort by (priority, slug): z_high (10), a_low (99)
    # index 0 -> bucket 0, index 1 -> bucket 1
    assert result == ["z_high"]
