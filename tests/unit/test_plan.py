"""Unit tests for the dry-run planner.

The planner is pure: given a list of SearchModel and a counts cache, it
produces deterministic per-day and per-rotation estimates. No network,
no DB.
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from encar_parser.db.models import SearchModel
from encar_parser.plan import (
    DEFAULT_AVG_COUNT,
    DEFAULT_PER_CAR_SEC,
    ModelEstimate,
    PlanDay,
    PlanRotation,
    build_day_plan,
    build_rotation_plan,
    estimate_count,
    estimate_seconds,
    load_counts_cache,
    render_plan_text,
    render_rotation_text,
    save_counts_cache,
)


def _mk(slug: str, priority: int = 100) -> SearchModel:
    return SearchModel(
        slug=slug, name=slug, encar_url="", encar_action={}, priority=priority,
    )


# ── estimate_count ─────────────────────────────────────────────────────


def test_estimate_count_prefers_measured_value():
    assert estimate_count("a", counts={"a": 999}, fallback=100) == 999


def test_estimate_count_falls_back_to_average():
    assert estimate_count("missing", counts={}, fallback=300) == 300


def test_estimate_count_zero_is_not_replaced_by_fallback():
    """If a model was probed and got 0, return 0 — that's a real signal."""
    assert estimate_count("empty", counts={"empty": 0}, fallback=300) == 0


# ── estimate_seconds ───────────────────────────────────────────────────


def test_estimate_seconds_basic():
    # 10 cars * 4s = 40s + 30s overhead = 70s
    assert estimate_seconds(10, per_car_sec=4.0) == 70.0


def test_estimate_seconds_zero_cars():
    """A model with 0 measured cars still pays the per-model overhead."""
    assert estimate_seconds(0, per_car_sec=4.0) == 30.0


# ── counts cache ───────────────────────────────────────────────────────


def test_counts_cache_roundtrip(tmp_path):
    path = tmp_path / "counts.json"
    save_counts_cache(path, {"a": 10, "b": 20, "c": 0})
    loaded = load_counts_cache(path)
    assert loaded == {"a": 10, "b": 20, "c": 0}


def test_counts_cache_missing_returns_empty(tmp_path):
    assert load_counts_cache(tmp_path / "missing.json") == {}


def test_counts_cache_invalid_returns_empty(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_counts_cache(path) == {}


# ── build_day_plan ─────────────────────────────────────────────────────


def test_build_day_plan_picks_today_bucket():
    models = [_mk(f"m{i:02d}", priority=i * 10) for i in range(7)]
    today = date(2026, 6, 15)
    plan = build_day_plan(
        models, today=today, bucket_count=7, cooldown_hours=12,
        per_car_sec=DEFAULT_PER_CAR_SEC, counts={}, avg_count=DEFAULT_AVG_COUNT,
    )
    assert isinstance(plan, PlanDay)
    assert plan.day == "2026-06-15"
    assert plan.bucket_count == 7
    assert plan.cooldown_hours == 12
    # One of 7 buckets selected → exactly one model.
    assert len(plan.models) == 1


def test_build_day_plan_respects_cooldown():
    """Models with recent last_run_at are deferred (not in plan.models)."""
    models = [_mk(f"m{i:02d}", priority=i * 10) for i in range(7)]
    today = date(2026, 6, 15)
    now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)

    # Figure out which model lands in today's bucket, then mark it as
    # recently run on the SearchModel (not on the plan, which is frozen).
    probe = build_day_plan(
        models, today=today, bucket_count=7, cooldown_hours=12,
        per_car_sec=DEFAULT_PER_CAR_SEC, counts={}, avg_count=DEFAULT_AVG_COUNT,
        now=now,
    )
    in_bucket_slug = probe.models[0].slug
    in_bucket_model = next(m for m in models if m.slug == in_bucket_slug)
    in_bucket_model.last_run_at = now - timedelta(hours=1)

    plan2 = build_day_plan(
        models, today=today, bucket_count=7, cooldown_hours=12,
        per_car_sec=DEFAULT_PER_CAR_SEC, counts={}, avg_count=DEFAULT_AVG_COUNT,
        now=now,
    )
    assert in_bucket_slug in plan2.skipped_cooldown
    assert in_bucket_slug not in {m.slug for m in plan2.models}


def test_build_day_plan_sums_seconds_across_models():
    """Total time is the sum of per-model estimates."""
    models = [_mk(f"m{i}", priority=i * 10) for i in range(3)]
    plan = build_day_plan(
        models, today=date(2026, 6, 15), bucket_count=3, cooldown_hours=12,
        per_car_sec=2.0, counts={}, avg_count=10,
    )
    expected = sum(m.estimated_seconds for m in plan.models)
    assert abs(plan.total_seconds - expected) < 0.01


# ── build_rotation_plan ────────────────────────────────────────────────


def test_build_rotation_plan_full_coverage():
    """Across N days, every model appears exactly once."""
    models = [_mk(f"m{i:02d}", priority=i * 10) for i in range(14)]
    rotation = build_rotation_plan(
        models, start=date(2026, 6, 15), bucket_count=14, cooldown_hours=12,
        per_car_sec=DEFAULT_PER_CAR_SEC, counts={}, avg_count=DEFAULT_AVG_COUNT,
    )
    seen: set[str] = set()
    for plan in rotation.plans:
        for m in plan.models:
            assert m.slug not in seen, f"{m.slug} appeared twice"
            seen.add(m.slug)
    assert seen == {m.slug for m in models}


def test_build_rotation_plan_reports_measured_and_fallback_counts():
    models = [_mk(f"m{i}") for i in range(4)]
    counts = {"m0": 100, "m1": 200}
    rotation = build_rotation_plan(
        models, start=date(2026, 6, 15), bucket_count=4, cooldown_hours=12,
        per_car_sec=DEFAULT_PER_CAR_SEC, counts=counts, avg_count=DEFAULT_AVG_COUNT,
    )
    assert rotation.measured_models == 2
    assert rotation.fallback_models == 2


# ── render_plan_text ───────────────────────────────────────────────────


def test_render_plan_text_contains_key_fields():
    plan = PlanDay(
        day="2026-06-15", bucket_index=0, bucket_count=14,
        cooldown_hours=12, models=[
            ModelEstimate(slug="x", priority=10, count=100, estimated_seconds=400.0),
        ],
        skipped_cooldown=["y"],
        total_seconds=400.0,
    )
    text = render_plan_text(plan)
    assert "2026-06-15" in text
    assert "x" in text
    assert "y" in text  # cooldown skip listed
    assert "100 cars" in text


def test_render_rotation_text_summarises_all_days():
    rotation = PlanRotation(
        plans=[
            PlanDay(
                day=f"2026-06-{15 + i:02d}", bucket_index=i, bucket_count=3,
                cooldown_hours=12, models=[],
                skipped_cooldown=[], total_seconds=0.0,
            )
            for i in range(3)
        ],
        bucket_count=3, cooldown_hours=12, per_car_sec=4.0,
        measured_models=0, fallback_models=0,
    )
    text = render_rotation_text(rotation)
    for plan in rotation.plans:
        assert plan.day in text
    assert "total rotation" in text
