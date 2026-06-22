from datetime import UTC, date, datetime, timedelta

import pytest

from encar_parser.db.models import SearchModel
from encar_parser.encar_url import ModelConfig
from encar_parser.scheduler import (
    bucket_for,
    filter_by_cooldown,
    models_for_today,
    plan_today,
)


def _mk(slug: str, priority: int = 100, last_run_at: datetime | None = None) -> SearchModel:
    ModelConfig(slug=slug, name=slug, priority=priority)
    return SearchModel(
        slug=slug,
        name=slug,
        encar_url="",
        encar_action={},
        priority=priority,
        last_run_at=last_run_at,
    )


# ── legacy 3-bucket rotation (kept for backward compatibility) ─────────


def test_models_for_today_divides_by_three_days():
    models = [_mk(f"m{i:02d}") for i in range(6)]
    day1 = date(2026, 6, 15)  # Monday, bucket 0
    day2 = date(2026, 6, 16)  # Tuesday, bucket 1
    day3 = date(2026, 6, 17)  # Wednesday, bucket 2

    d1 = [m.slug for m in models_for_today(models, day1)]
    d2 = [m.slug for m in models_for_today(models, day2)]
    d3 = [m.slug for m in models_for_today(models, day3)]

    assert set(d1) | set(d2) | set(d3) == {m.slug for m in models}
    assert len(set(d1) & set(d2)) == 0
    assert len(set(d2) & set(d3)) == 0
    assert len(set(d1) & set(d3)) == 0


def test_models_for_today_deterministic():
    models = [_mk(f"m{i:02d}") for i in range(9)]
    day = date(2026, 6, 15)
    assert models_for_today(models, day) == models_for_today(models, day)


def test_models_for_today_respects_priority():
    models = [_mk("z_high", priority=10), _mk("a_low", priority=99)]
    assert [m.slug for m in models_for_today(models, date(2026, 6, 15))] == ["z_high"]


# ── bucket_for — configurable rotation size ────────────────────────────


def test_bucket_for_with_14_buckets_divides_models_uniformly():
    """105 models in 14 buckets → 7-8 per bucket (some have 8, some 7)."""
    models = [_mk(f"m{i:03d}") for i in range(105)]
    seen_total = 0
    for day in (date(2026, 6, 15) + timedelta(days=d) for d in range(14)):
        bucket = bucket_for(models, day, bucket_count=14)
        # Each bucket has 7 or 8 models (105 / 14 = 7.5).
        assert 7 <= len(bucket) <= 8
        seen_total += len(bucket)
    # Across 14 days every model is visited exactly once.
    assert seen_total == 105


def test_bucket_for_full_coverage_across_rotation():
    """Every model appears in exactly one bucket per rotation cycle."""
    models = [_mk(f"m{i:03d}") for i in range(21)]  # 21 / 7 = 3 per day
    union: set[str] = set()
    intersections: list[set[str]] = []
    for day_offset in range(7):
        bucket = {m.slug for m in bucket_for(models, date(2026, 6, 15) + timedelta(days=day_offset), bucket_count=7)}
        assert bucket.isdisjoint(union), f"day {day_offset} overlaps earlier days"
        union |= bucket
        intersections.append(bucket)
    assert union == {m.slug for m in models}


def test_bucket_for_rejects_invalid_size():
    with pytest.raises(ValueError):
        bucket_for([], date(2026, 6, 15), bucket_count=0)


# ── filter_by_cooldown ─────────────────────────────────────────────────


def test_filter_by_cooldown_never_parsed_is_ready():
    models = [_mk("never_parsed", last_run_at=None)]
    ready, deferred = filter_by_cooldown(models, cooldown_hours=12)
    assert [m.slug for m in ready] == ["never_parsed"]
    assert deferred == []


def test_filter_by_cooldown_old_last_run_is_ready():
    """A model last run 13h ago passes a 12h cooldown."""
    old = datetime.now(UTC) - timedelta(hours=13)
    models = [_mk("old", last_run_at=old)]
    ready, deferred = filter_by_cooldown(models, cooldown_hours=12)
    assert [m.slug for m in ready] == ["old"]
    assert deferred == []


def test_filter_by_cooldown_recent_last_run_is_deferred():
    """A model last run 1h ago is deferred under 12h cooldown."""
    recent = datetime.now(UTC) - timedelta(hours=1)
    models = [_mk("recent", last_run_at=recent)]
    ready, deferred = filter_by_cooldown(models, cooldown_hours=12)
    assert ready == []
    assert [m.slug for m in deferred] == ["recent"]


def test_filter_by_cooldown_zero_means_run_everything():
    """cooldown_hours=0 lets a model re-run even if it just finished."""
    recent = datetime.now(UTC) - timedelta(seconds=10)
    models = [_mk("just_now", last_run_at=recent)]
    ready, deferred = filter_by_cooldown(models, cooldown_hours=0)
    assert [m.slug for m in ready] == ["just_now"]
    assert deferred == []


def test_filter_by_cooldown_rejects_negative():
    with pytest.raises(ValueError):
        filter_by_cooldown([], cooldown_hours=-1)


# ── plan_today — the daily entry point ────────────────────────────────


def test_plan_today_combines_bucket_and_cooldown():
    now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
    today = now.date()
    # Build 14 models with known priorities; mark some as recently run.
    models = [_mk(f"m{i:02d}", priority=i * 10) for i in range(14)]
    # 2026-06-15 is DOY 166 → bucket index = (166-1) % 14 = 11
    today_bucket_index = (today.timetuple().tm_yday - 1) % 14

    # Mark the model that lands in today's bucket as recently run → deferred.
    # Mark the next model as long-ago run → ready.
    in_bucket = models[today_bucket_index]
    in_bucket.last_run_at = now - timedelta(hours=1)
    if today_bucket_index + 1 < len(models):
        models[today_bucket_index + 1].last_run_at = now - timedelta(hours=24)

    plan = plan_today(models, today, bucket_count=14, cooldown_hours=12, now=now)
    assert plan.bucket_count == 14
    assert plan.bucket_index == today_bucket_index
    assert plan.total_enabled == 14
    assert plan.cooldown_hours == 12
    assert {m.slug for m in plan.deferred_models} == {in_bucket.slug}
    assert in_bucket.slug not in {m.slug for m in plan.today_models}
    assert plan.skipped_due_to_cooldown == 1


def test_plan_today_empty_when_bucket_empty():
    plan = plan_today([], date(2026, 6, 15))
    assert plan.today_models == []
    assert plan.deferred_models == []
    assert plan.total_enabled == 0


def test_plan_today_as_dict():
    plan = plan_today([_mk("a")], date(2026, 6, 15), bucket_count=7, cooldown_hours=12)
    d = plan.as_dict()
    assert d["bucket_count"] == 7
    assert d["cooldown_hours"] == 12
    # 2026-06-15 DOY=166 → (166-1) % 7 = 4 → bucket 4 contains only m[4].
    # The single model is at index 0 → bucket 0 → NOT today's bucket.
    assert d["models_to_run"] == 0
