"""Unit tests for the resumable backfill state machine.

We exercise the JSON state file (load / save / reset / version check) and
``walk_backfill`` (resume semantics, atomic writes, interrupt handling)
without touching the network or the DB.
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from encar_parser.backfill import (
    STATE_VERSION,
    BackfillState,
    filter_remaining,
    load_state,
    reset_state,
    save_state,
    walk_backfill,
)
from encar_parser.db.models import SearchModel


def _mk(slug: str, priority: int = 100) -> SearchModel:
    return SearchModel(
        slug=slug,
        name=slug,
        encar_url="",
        encar_action={},
        priority=priority,
    )


def _tmp_state_path(tmp_path: Path) -> Path:
    return tmp_path / "backfill_state.json"


# ── JSON state file ────────────────────────────────────────────────────


def test_save_and_load_roundtrip(tmp_path):
    path = _tmp_state_path(tmp_path)
    state = BackfillState(
        started_at=datetime(2026, 6, 21, 10, 0, tzinfo=UTC),
        last_updated_at=datetime(2026, 6, 21, 11, 0, tzinfo=UTC),
        completed_slugs=["a", "b"],
        current_slug="c",
        models_total=10,
        chunk_size=5,
        status="running",
    )
    save_state(path, state)
    loaded = load_state(path)
    assert loaded is not None
    assert loaded.started_at == state.started_at
    assert loaded.last_updated_at == state.last_updated_at
    assert loaded.completed_slugs == ["a", "b"]
    assert loaded.current_slug == "c"
    assert loaded.models_total == 10
    assert loaded.chunk_size == 5
    assert loaded.status == "running"


def test_load_state_returns_none_for_missing_file(tmp_path):
    assert load_state(_tmp_state_path(tmp_path)) is None


def test_load_state_rejects_wrong_version(tmp_path):
    path = _tmp_state_path(tmp_path)
    path.write_text(json.dumps({"version": 999, "started_at": "x"}), encoding="utf-8")
    assert load_state(path) is None


def test_load_state_rejects_corrupt_json(tmp_path):
    path = _tmp_state_path(tmp_path)
    path.write_text("{not json", encoding="utf-8")
    assert load_state(path) is None


def test_save_state_is_atomic(tmp_path):
    """A crash mid-write must not leave a half-written file that bricks resume."""
    path = _tmp_state_path(tmp_path)
    state = BackfillState(
        started_at=datetime.now(UTC), last_updated_at=datetime.now(UTC),
        completed_slugs=["x"], models_total=1, status="done",
    )
    save_state(path, state)
    # No leftover *.tmp files in the directory.
    leftover = [p for p in tmp_path.iterdir() if p.name.startswith(".backfill_state") and p.name.endswith(".tmp")]
    assert leftover == []
    # And the file parses.
    assert load_state(path) is not None


def test_reset_state_removes_file(tmp_path):
    path = _tmp_state_path(tmp_path)
    path.write_text("{}", encoding="utf-8")
    assert reset_state(path) is True
    assert not path.exists()
    # Second call is a no-op returning False.
    assert reset_state(path) is False


def test_as_dict_round_trip_includes_version():
    state = BackfillState(
        started_at=datetime(2026, 6, 21, 10, 0, tzinfo=UTC),
        last_updated_at=datetime(2026, 6, 21, 10, 5, tzinfo=UTC),
    )
    d = state.as_dict()
    assert d["version"] == STATE_VERSION
    # Round-trip via load_state (via a tmp file).
    import tempfile, os
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(d, f)
        path = f.name
    try:
        loaded = load_state(Path(path))
        assert loaded is not None
    finally:
        os.unlink(path)


# ── filter_remaining ───────────────────────────────────────────────────


def test_filter_remaining_keeps_priority_order():
    """filter_remaining drops completed slugs but preserves model order."""
    models = [_mk(slug=f"m{i}", priority=i * 10) for i in range(5)]
    state = BackfillState(
        started_at=datetime.now(UTC), last_updated_at=datetime.now(UTC),
        completed_slugs=["m0", "m2"],
    )
    remaining = filter_remaining(models, state)
    assert [m.slug for m in remaining] == ["m1", "m3", "m4"]


def test_filter_remaining_with_no_state_returns_everything():
    models = [_mk(slug=f"m{i}") for i in range(3)]
    assert [m.slug for m in filter_remaining(models, None)] == ["m0", "m1", "m2"]


def test_filter_remaining_with_empty_completed_set():
    models = [_mk(slug=f"m{i}") for i in range(3)]
    state = BackfillState(
        started_at=datetime.now(UTC), last_updated_at=datetime.now(UTC),
        completed_slugs=[],
    )
    assert [m.slug for m in filter_remaining(models, state)] == ["m0", "m1", "m2"]


# ── walk_backfill (no network) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_walk_backfill_processes_every_model(tmp_path):
    path = _tmp_state_path(tmp_path)
    models = [_mk(slug=f"m{i}") for i in range(3)]

    async def fake_run_one(m):
        return 5  # pretend each model produced 5 cars

    summary = await walk_backfill(
        models, path, run_one=fake_run_one, chunk_size=3
    )
    assert summary["status"] == "done"
    assert summary["completed"] == 3
    assert summary["skipped_resume"] == 0
    assert summary["total_cars"] == 15

    loaded = load_state(path)
    assert loaded is not None
    assert loaded.status == "done"
    assert loaded.completed_slugs == ["m0", "m1", "m2"]


@pytest.mark.asyncio
async def test_walk_backfill_resumes_from_completed(tmp_path):
    """If state already has m0 done, walk_backfill skips it and continues."""
    path = _tmp_state_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    initial = BackfillState(
        started_at=datetime(2026, 6, 21, 10, 0, tzinfo=UTC),
        last_updated_at=datetime(2026, 6, 21, 10, 1, tzinfo=UTC),
        completed_slugs=["m0"],
        models_total=3,
    )
    save_state(path, initial)

    processed: list[str] = []

    async def fake_run_one(m):
        processed.append(m.slug)
        return 1

    models = [_mk(slug=f"m{i}") for i in range(3)]
    summary = await walk_backfill(models, path, run_one=fake_run_one)
    assert processed == ["m1", "m2"], "m0 was already done; only m1+m2 should run"
    assert summary["skipped_resume"] == 1
    assert summary["completed"] == 3


@pytest.mark.asyncio
async def test_walk_backfill_marks_interrupted_on_failure(tmp_path):
    """A model that raises stops the walk and saves status=interrupted."""
    path = _tmp_state_path(tmp_path)
    models = [_mk(slug=f"m{i}") for i in range(3)]

    async def fake_run_one(m):
        if m.slug == "m1":
            raise RuntimeError("encar is down")
        return 1

    summary = await walk_backfill(models, path, run_one=fake_run_one)
    assert summary["status"] == "interrupted"
    assert summary["failed_slug"] == "m1"
    assert summary["completed"] == 1  # m0 finished before m1 failed

    loaded = load_state(path)
    assert loaded is not None
    assert loaded.status == "interrupted"
    assert loaded.completed_slugs == ["m0"]
    # m1 is NOT in completed_slugs — on resume, it will be tried again.
    assert "m1" not in loaded.completed_slugs
    assert loaded.current_slug == "m1"


@pytest.mark.asyncio
async def test_walk_backfill_resume_after_interrupt_picks_up_failed_model(tmp_path):
    """After an interrupted run, resuming must retry the failed model."""
    path = _tmp_state_path(tmp_path)

    async def fail_then_succeed(m):
        # First pass: m1 fails. Second pass: m1 succeeds.
        if m.slug == "m1":
            state = load_state(path)
            attempts = state.last_updated_at  # use last_updated_at as a counter? No.
            return 2
        return 1

    # Simpler: m1 raises the first time, succeeds the second.
    attempts: dict[str, int] = {"m1": 0}

    async def run_with_retry(m):
        if m.slug == "m1":
            attempts["m1"] += 1
            if attempts["m1"] == 1:
                raise RuntimeError("flake")
        return 1

    models = [_mk(slug=f"m{i}") for i in range(3)]

    # First pass — fails on m1.
    summary1 = await walk_backfill(models, path, run_one=run_with_retry)
    assert summary1["status"] == "interrupted"
    assert summary1["completed"] == 1

    # Second pass — must retry m1 and finish cleanly.
    summary2 = await walk_backfill(models, path, run_one=run_with_retry)
    assert summary2["status"] == "done"
    assert summary2["completed"] == 3
    assert attempts["m1"] == 2


@pytest.mark.asyncio
async def test_walk_backfill_invokes_state_change_callback(tmp_path):
    path = _tmp_state_path(tmp_path)
    models = [_mk(slug=f"m{i}") for i in range(2)]
    callback_calls: list[int] = []

    def cb(state):
        callback_calls.append(len(state.completed_slugs))

    async def fake_run_one(m):
        return 1

    await walk_backfill(models, path, run_one=fake_run_one, on_state_change=cb)
    assert callback_calls == [1, 2]
