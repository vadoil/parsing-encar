"""Decide which models to process today based on a configurable rotation.

Two scheduling concepts coexist:

* **Rotation bucket** — splits all enabled models into N buckets by
  sorted index. The bucket chosen for today is
  ``(today.isoweekday() - 1) % N``. With N=14 the daily slice is
  small enough to fit comfortably in a 12-hour window even on a
  full backfill. With N=3 (legacy) it took 8-10 hours per day.
* **Per-model cooldown** — once a model has been parsed, leave it
  alone for ``cooldown_hours`` hours regardless of which bucket
  today is. EncAr's ModifiedDate window for new listings is ~24h;
  a 12h cooldown is a sweet spot for incremental runs.

The functions here are pure (no I/O except for the SearchModel list);
the CLI layer combines them with the actual run logic.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from encar_parser.db.models import SearchModel


@dataclass(frozen=True)
class TodayPlan:
    """What a daily run is supposed to do.

    Attributes
    ----------
    today_models : list[SearchModel]
        The bucket-of-the-day models whose ``last_run_at`` is older
        than ``cooldown_hours`` (i.e. ready to run).
    deferred_models : list[SearchModel]
        Same bucket, but recently parsed — skipped this run.
    skipped_due_to_cooldown : int
        Convenience count of deferred models.
    bucket_index : int
        The bucket chosen for today (``(isoweekday-1) % bucket_count``).
    bucket_count : int
        Total number of buckets in the rotation.
    total_enabled : int
        Total enabled models in the DB.
    cooldown_hours : int
        The threshold used for the cooldown filter.
    """

    today_models: list[SearchModel]
    deferred_models: list[SearchModel]
    skipped_due_to_cooldown: int
    bucket_index: int
    bucket_count: int
    total_enabled: int
    cooldown_hours: int

    def as_dict(self) -> dict:
        return {
            "bucket_index": self.bucket_index,
            "bucket_count": self.bucket_count,
            "total_enabled": self.total_enabled,
            "cooldown_hours": self.cooldown_hours,
            "models_to_run": len(self.today_models),
            "models_deferred": len(self.deferred_models),
        }


def _sort_key(m: SearchModel) -> tuple[int, str]:
    return (m.priority, m.slug)


def bucket_for(
    models: Iterable[SearchModel], today: date, bucket_count: int
) -> list[SearchModel]:
    """Return the subset of models in today's bucket.

    Bucket selection: sort by ``(priority, slug)``, then index ``i % bucket_count``.
    The bucket for ``today`` is ``(today.timetuple().tm_yday - 1) % bucket_count``.
    Using day-of-year (not weekday) lets ``bucket_count`` be any positive
    number — weekday would max out at 7.
    With 105 enabled models and ``bucket_count=14`` that yields 7-8 models
    per day; with ``bucket_count=7`` it yields ~15 (heavier days).
    """
    if bucket_count <= 0:
        raise ValueError(f"bucket_count must be positive, got {bucket_count}")
    sorted_models = sorted(models, key=_sort_key)
    bucket = (today.timetuple().tm_yday - 1) % bucket_count
    return [m for i, m in enumerate(sorted_models) if i % bucket_count == bucket]


def filter_by_cooldown(
    models: Iterable[SearchModel],
    *,
    cooldown_hours: int,
    now: datetime | None = None,
) -> tuple[list[SearchModel], list[SearchModel]]:
    """Split ``models`` into (ready, deferred) by ``last_run_at`` + cooldown.

    Models whose ``last_run_at`` is None (never parsed) are always ready.
    Models whose ``last_run_at`` is older than ``cooldown_hours`` are ready.
    The rest are deferred.
    """
    if cooldown_hours < 0:
        raise ValueError(f"cooldown_hours must be >= 0, got {cooldown_hours}")
    now = now or datetime.now(timezone.utc)
    threshold = now - timedelta(hours=cooldown_hours)
    ready: list[SearchModel] = []
    deferred: list[SearchModel] = []
    for m in models:
        if m.last_run_at is None or m.last_run_at < threshold:
            ready.append(m)
        else:
            deferred.append(m)
    return ready, deferred


def plan_today(
    enabled_models: Iterable[SearchModel],
    today: date,
    *,
    bucket_count: int = 14,
    cooldown_hours: int = 12,
    now: datetime | None = None,
) -> TodayPlan:
    """Compute today's plan: bucket + cooldown filter applied together.

    The full rotation has ``total_enabled`` models split across
    ``bucket_count`` buckets; today picks one bucket and the cooldown
    filter shrinks it further to ``today_models``.
    """
    enabled_list = list(enabled_models)
    bucket = bucket_for(enabled_list, today, bucket_count=bucket_count)
    bucket_idx = (today.timetuple().tm_yday - 1) % bucket_count
    ready, deferred = filter_by_cooldown(bucket, cooldown_hours=cooldown_hours, now=now)
    return TodayPlan(
        today_models=ready,
        deferred_models=deferred,
        skipped_due_to_cooldown=len(deferred),
        bucket_index=bucket_idx,
        bucket_count=bucket_count,
        total_enabled=len(enabled_list),
        cooldown_hours=cooldown_hours,
    )


# Legacy alias for backward compatibility with the old 3-day rotation.
# ``models_for_today`` predates the bucket-count / cooldown knobs and is
# still used by a couple of tests.
def models_for_today(
    models: list[SearchModel], today: date
) -> list[SearchModel]:
    """3-bucket rotation, no cooldown. Prefer :func:`plan_today`."""
    sorted_models = sorted(models, key=_sort_key)
    bucket = (today.isoweekday() - 1) % 3
    return [m for i, m in enumerate(sorted_models) if i % 3 == bucket]
