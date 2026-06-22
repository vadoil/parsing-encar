"""Resumable full-model backfill — walks every enabled model exactly once.

Why a JSON file rather than a DB table? Backfill state is a startup concern
(parser reads it on container boot), not a query concern (no one queries
"which models were backfilled last week?"). A file under ``/var/log`` is
trivial to rsync, inspect, edit, or delete — and the parser container
already has that volume mounted.

State file schema (v1)::

    {
      "version": 1,
      "started_at": "2026-06-21T10:00:00+00:00",
      "last_updated_at": "2026-06-21T11:30:00+00:00",
      "status": "running",          # running | done | interrupted
      "completed_slugs": ["a4-b9", "a6-c8", ...],   # sorted
      "current_slug": "q5-fy",      # last in-flight when state was saved
      "chunk_size": 10,             # models per resume unit (informational)
      "models_total": 105
    }

Idempotent: re-running ``backfill --resume`` after a crash picks up at the
first slug that isn't in ``completed_slugs``. The CLI passes the saved
state to :func:`walk_backfill`, which filters models against it.

Deleting the state file is the way to start over from scratch.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from encar_parser.db.models import SearchModel
from encar_parser.utils.log import get_logger

log = get_logger(__name__)

STATE_VERSION = 1


@dataclass
class BackfillState:
    """Mutable backfill progress, persisted to disk as JSON."""

    started_at: datetime
    last_updated_at: datetime
    completed_slugs: list[str] = field(default_factory=list)
    current_slug: str | None = None
    models_total: int = 0
    chunk_size: int = 0
    status: str = "running"  # running | done | interrupted

    @property
    def version(self) -> int:
        return STATE_VERSION

    def as_dict(self) -> dict:
        d = asdict(self)
        d["version"] = self.version
        d["started_at"] = self.started_at.isoformat()
        d["last_updated_at"] = self.last_updated_at.isoformat()
        return d

    @classmethod
    def from_dict(cls, raw: dict) -> "BackfillState":
        return cls(
            started_at=datetime.fromisoformat(raw["started_at"]),
            last_updated_at=datetime.fromisoformat(raw["last_updated_at"]),
            completed_slugs=list(raw.get("completed_slugs", [])),
            current_slug=raw.get("current_slug"),
            models_total=int(raw.get("models_total", 0)),
            chunk_size=int(raw.get("chunk_size", 0)),
            status=raw.get("status", "running"),
        )


def load_state(path: Path) -> BackfillState | None:
    """Load backfill state from ``path``. Returns None if file missing/corrupt."""
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("version") != STATE_VERSION:
            log.warning(
                "backfill_state_version_mismatch",
                file_version=raw.get("version"),
                expected=STATE_VERSION,
            )
            return None
        return BackfillState.from_dict(raw)
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        log.warning("backfill_state_unreadable", path=str(path), error=str(e))
        return None


def save_state(path: Path, state: BackfillState) -> None:
    """Atomically write state to disk.

    Uses write-to-temp-then-rename so a crash mid-write never leaves a
    half-baked JSON file that would brick the next run.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = state.as_dict()
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    except Exception:
        # Best-effort cleanup of the temp file.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def filter_remaining(
    all_models: Iterable[SearchModel], state: BackfillState | None
) -> list[SearchModel]:
    """Drop models whose slug is already in ``state.completed_slugs``.

    Order is preserved. Returns the remaining work, in priority order.
    """
    if state is None or not state.completed_slugs:
        return list(all_models)
    done = set(state.completed_slugs)
    return [m for m in all_models if m.slug not in done]


async def walk_backfill(
    all_models: list[SearchModel],
    state_path: Path,
    *,
    run_one,  # callable: SearchModel -> Awaitable[int]  (returns cars_processed)
    chunk_size: int = 0,
    on_state_change=None,  # callable: BackfillState -> None
) -> dict:
    """Walk every model once, persisting resume state to ``state_path``.

    Parameters
    ----------
    all_models
        The full list of enabled models in priority order.
    state_path
        Where to read/write the resume JSON.
    run_one
        Async callable invoked per model (e.g. ``run_model``). Receives
        the ``SearchModel`` and must return the number of cars processed.
    chunk_size
        Optional; recorded in state for human reference.
    on_state_change
        Optional callback fired after every model completes (useful for
        emitting structlog events to ``encar.log``).

    Returns
    -------
    dict
        Summary: ``completed``, ``skipped_resume``, ``total_cars``,
        ``status`` (done / interrupted).
    """
    state = load_state(state_path)
    remaining = filter_remaining(all_models, state)
    skipped_resume = len(all_models) - len(remaining)

    if state is None:
        state = BackfillState(
            started_at=datetime.now(UTC),
            last_updated_at=datetime.now(UTC),
            completed_slugs=[],
            current_slug=None,
            models_total=len(all_models),
            chunk_size=chunk_size,
            status="running",
        )
        save_state(state_path, state)
        log.info(
            "backfill_started",
            path=str(state_path),
            total=len(all_models),
            chunk_size=chunk_size,
        )
    else:
        log.info(
            "backfill_resumed",
            path=str(state_path),
            skipped=skipped_resume,
            remaining=len(remaining),
            total=len(all_models),
        )

    total_cars = 0
    for m in remaining:
        state.current_slug = m.slug
        state.last_updated_at = datetime.now(UTC)
        save_state(state_path, state)
        try:
            n = await run_one(m)
        except Exception as e:
            log.error("backfill_model_failed", slug=m.slug, error=str(e))
            state.status = "interrupted"
            state.last_updated_at = datetime.now(UTC)
            save_state(state_path, state)
            return {
                "completed": len(state.completed_slugs),
                "skipped_resume": skipped_resume,
                "total_cars": total_cars,
                "status": "interrupted",
                "failed_slug": m.slug,
                "error": str(e),
            }

        total_cars += n
        if m.slug not in state.completed_slugs:
            state.completed_slugs.append(m.slug)
        state.last_updated_at = datetime.now(UTC)
        if on_state_change is not None:
            try:
                on_state_change(state)
            except Exception:  # pragma: no cover
                log.warning("backfill_state_change_callback_failed", exc_info=True)

    state.status = "done"
    state.current_slug = None
    state.last_updated_at = datetime.now(UTC)
    save_state(state_path, state)
    return {
        "completed": len(state.completed_slugs),
        "skipped_resume": skipped_resume,
        "total_cars": total_cars,
        "status": "done",
    }


def reset_state(path: Path) -> bool:
    """Delete the state file so the next run starts from scratch."""
    if path.exists():
        path.unlink()
        return True
    return False
