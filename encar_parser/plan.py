"""Dry-run plan: estimate what a daily incremental or backfill run will do.

The planner is read-only — it never fetches from Encar by default. It uses
cached per-model Counts when available (loaded from a JSON file written by
``scripts/probe_counts.py`` or similar) and falls back to a configurable
average when no count is known.

Why no live probing here? The CLI ``plan --probe`` flag exists for users
who want a fresh count, but by default the planner runs in <100ms against
just the local DB. Run ``plan --probe`` once after editing ``models.yaml``;
after that, every dry-run is instant.

Time model
----------
For a model with ``count`` cars and a per-car delay of ``d`` seconds, the
sequential fetch time is ``count * (d + network_overhead)``. Empirically
``d ≈ 4s`` for the EncAr detail API from this dev shell. Network overhead
is folded into ``d``; we don't try to model it separately.
"""
from __future__ import annotations

import asyncio
import json
import urllib.parse
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from encar_parser.config import get_settings
from encar_parser.db.models import SearchModel
from encar_parser.fetchers.api import ApiFetcher
from encar_parser.fetchers.base import FetcherError
from encar_parser.scheduler import (
    bucket_for,
    filter_by_cooldown,
    plan_today,
)
from encar_parser.utils.log import get_logger

log = get_logger(__name__)

# Default per-car fetch time when no measured value is available.
# 4 seconds was measured empirically on the dev shell (Phase 3 OOM-fix
# verification). The real network path on the prod VPS is closer to 3s;
# adjust ``--per-car-sec`` to taste.
DEFAULT_PER_CAR_SEC = 4.0

# Fallback per-model Count when no measurement is available.
# 469 was the average across 105 probed models (Phase 4 scheduler work).
DEFAULT_AVG_COUNT = 469


@dataclass(frozen=True)
class ModelEstimate:
    """Per-model budget for a plan."""

    slug: str
    priority: int
    count: int  # 0 if not measured; the avg is used instead
    estimated_seconds: float

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PlanDay:
    """One day's plan: which models run, total cost, expected duration."""

    day: str  # ISO date
    bucket_index: int
    bucket_count: int
    cooldown_hours: int
    models: list[ModelEstimate]
    skipped_cooldown: list[str]
    total_seconds: float

    @property
    def total_cars(self) -> int:
        return sum(m.count for m in self.models)

    def as_dict(self) -> dict:
        return {
            "day": self.day,
            "bucket_index": self.bucket_index,
            "bucket_count": self.bucket_count,
            "cooldown_hours": self.cooldown_hours,
            "models": [m.as_dict() for m in self.models],
            "models_count": len(self.models),
            "skipped_cooldown": self.skipped_cooldown,
            "total_cars": self.total_cars,
            "total_seconds": round(self.total_seconds, 1),
            "total_hours": round(self.total_seconds / 3600, 2),
        }


@dataclass(frozen=True)
class PlanRotation:
    """A full rotation's worth of plans — what runs each day for N days."""

    plans: list[PlanDay]
    bucket_count: int
    cooldown_hours: int
    per_car_sec: float
    measured_models: int  # how many models have a real Count
    fallback_models: int  # how many fall back to the average

    def as_dict(self) -> dict:
        return {
            "bucket_count": self.bucket_count,
            "cooldown_hours": self.cooldown_hours,
            "per_car_sec": self.per_car_sec,
            "measured_models": self.measured_models,
            "fallback_models": self.fallback_models,
            "days": [p.as_dict() for p in self.plans],
        }


# ── count cache ────────────────────────────────────────────────────────


def load_counts_cache(path: Path) -> dict[str, int]:
    """Load ``{slug: count}`` from a JSON file. Returns {} on missing/invalid."""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): int(v) for k, v in raw.items() if isinstance(v, (int, float))}
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        log.warning("plan_count_cache_unreadable", path=str(path), error=str(e))
        return {}


def save_counts_cache(path: Path, counts: dict[str, int]) -> None:
    """Persist a counts cache to disk for future dry-runs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(counts, ensure_ascii=False, indent=2), encoding="utf-8")


# ── live probing (used only when --probe is passed) ─────────────────────


async def probe_live_counts(
    enabled_models: Iterable[SearchModel],
    *,
    referer: str | None = None,
) -> dict[str, int]:
    """Hit EncAr's list API for every enabled model and capture Count.

    Slow: ~3 sec per model. Use only when ``--probe`` is requested.
    """
    settings = get_settings()
    referer = referer or settings.encar_referer
    counts: dict[str, int] = {}
    models = list(enabled_models)
    async with ApiFetcher() as api:
        for m in models:
            url = m.encar_action.get("api_url") if m.encar_action else None
            if not url:
                counts[m.slug] = 0
                continue
            # Shrink limit to 5 — we only need Count, not the actual rows.
            url = _force_small_limit(url)
            try:
                resp = await api.get(url, referer=referer)
                payload = resp.json()
                cnt = payload.get("Count")
                counts[m.slug] = int(cnt) if isinstance(cnt, int) else 0
            except FetcherError as e:
                log.warning("plan_probe_failed", slug=m.slug, error=str(e))
                counts[m.slug] = 0
    return counts


def _force_small_limit(url: str) -> str:
    """Replace ``sr=|ModifiedDate|<offset>|<limit>`` with limit=5.

    We don't care about actual rows — only the Count header — so a tiny
    page size keeps the probe cheap.

    Pass the RAW ``|ModifiedDate|0|5`` to ``urlencode`` so the ``|``
    is percent-encoded exactly once. The earlier version pre-quoted
    with ``urllib.parse.quote`` then ran the result through
    ``urlencode`` again, producing ``%257C`` (double-encoded) — which
    Encar rejects with HTTP 400.
    """
    parsed = urllib.parse.urlparse(url)
    pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    new_pairs = [
        (k, "|ModifiedDate|0|5") if k == "sr" else (k, v)
        for k, v in pairs
    ]
    new_query = urllib.parse.urlencode(new_pairs, safe="()._")
    return urllib.parse.urlunparse(parsed._replace(query=new_query))


# ── planner ────────────────────────────────────────────────────────────


def estimate_count(slug: str, *, counts: dict[str, int], fallback: int) -> int:
    """Pick a Count for a slug — measured value or fallback average."""
    if slug in counts:
        return counts[slug]
    return fallback


def estimate_seconds(count: int, *, per_car_sec: float, per_model_overhead_sec: float = 30.0) -> float:
    """Sequential fetch time: count * per_car_sec + per-model overhead.

    The overhead covers pagination list calls + the post-model delay
    (settings.max_model_delay_sec max, but average is lower).
    """
    return count * per_car_sec + per_model_overhead_sec


def build_rotation_plan(
    enabled_models: list[SearchModel],
    *,
    start: date,
    bucket_count: int,
    cooldown_hours: int,
    per_car_sec: float,
    counts: dict[str, int],
    avg_count: int,
) -> PlanRotation:
    """Plan one full rotation: ``bucket_count`` consecutive days starting at ``start``."""
    plans: list[PlanDay] = []
    measured = 0
    fallback = 0
    for day_offset in range(bucket_count):
        today = start + timedelta(days=day_offset)
        plan = build_day_plan(
            enabled_models,
            today=today,
            bucket_count=bucket_count,
            cooldown_hours=cooldown_hours,
            per_car_sec=per_car_sec,
            counts=counts,
            avg_count=avg_count,
        )
        plans.append(plan)
        measured += sum(1 for m in plan.models if m.slug in counts)
        fallback += sum(1 for m in plan.models if m.slug not in counts)
    return PlanRotation(
        plans=plans,
        bucket_count=bucket_count,
        cooldown_hours=cooldown_hours,
        per_car_sec=per_car_sec,
        measured_models=measured,
        fallback_models=fallback,
    )


def build_day_plan(
    enabled_models: list[SearchModel],
    *,
    today: date,
    bucket_count: int,
    cooldown_hours: int,
    per_car_sec: float,
    counts: dict[str, int],
    avg_count: int,
    now: datetime | None = None,
) -> PlanDay:
    """Plan a single day's run."""
    bucket = bucket_for(enabled_models, today, bucket_count=bucket_count)
    bucket_idx = (today.timetuple().tm_yday - 1) % bucket_count
    ready, deferred = filter_by_cooldown(bucket, cooldown_hours=cooldown_hours, now=now)

    models: list[ModelEstimate] = []
    total_seconds = 0.0
    for m in ready:
        count = estimate_count(m.slug, counts=counts, fallback=avg_count)
        seconds = estimate_seconds(count, per_car_sec=per_car_sec)
        total_seconds += seconds
        models.append(ModelEstimate(
            slug=m.slug,
            priority=m.priority,
            count=count,
            estimated_seconds=seconds,
        ))
    # Order by priority for human readability.
    models.sort(key=lambda e: (e.priority, e.slug))
    return PlanDay(
        day=today.isoformat(),
        bucket_index=bucket_idx,
        bucket_count=bucket_count,
        cooldown_hours=cooldown_hours,
        models=models,
        skipped_cooldown=[m.slug for m in deferred],
        total_seconds=total_seconds,
    )


def render_plan_text(plan: PlanDay) -> str:
    """Human-readable one-day plan for terminal output."""
    lines = [
        f"=== {plan.day} (bucket {plan.bucket_index}/{plan.bucket_count}, "
        f"cooldown {plan.cooldown_hours}h) ===",
        f"models to run: {len(plan.models)}   "
        f"total cars: {plan.total_cars}   "
        f"estimated time: {plan.total_seconds/3600:.2f} h "
        f"({plan.total_seconds/60:.0f} min)",
    ]
    for m in plan.models:
        marker = "M" if m.slug in {e.slug for e in plan.models if e.count > 0} else "·"
        lines.append(
            f"  [{marker}] p={m.priority:>4}  {m.count:>5} cars  "
            f"~{m.estimated_seconds/60:>5.1f} min  {m.slug}"
        )
    if plan.skipped_cooldown:
        lines.append(f"  skipped (cooldown): {', '.join(plan.skipped_cooldown)}")
    return "\n".join(lines)


def render_rotation_text(rotation: PlanRotation) -> str:
    """Multi-day plan summary."""
    header = (
        f"=== Rotation plan ({rotation.bucket_count} buckets, "
        f"cooldown {rotation.cooldown_hours}h, "
        f"{rotation.per_car_sec:.1f} sec/car) ===\n"
        f"measured Counts: {rotation.measured_models}  "
        f"fallback to avg: {rotation.fallback_models}"
    )
    days = "\n\n".join(render_plan_text(p) for p in rotation.plans)
    totals_h = sum(p.total_seconds for p in rotation.plans) / 3600
    return f"{header}\n\n{days}\n\n--- total rotation: {totals_h:.1f} hours ---"


# ── async entry point for the CLI ──────────────────────────────────────


async def run_plan_cli(
    *,
    enabled_models: list[SearchModel],
    day: date | None,
    days: int,
    bucket_count: int,
    cooldown_hours: int,
    per_car_sec: float,
    avg_count: int,
    counts_cache: Path,
    probe: bool,
) -> PlanRotation:
    """Build the rotation plan the CLI will render or print as JSON."""
    if probe:
        counts = await probe_live_counts(enabled_models)
        save_counts_cache(counts_cache, counts)
    else:
        counts = load_counts_cache(counts_cache)
    if day is None:
        day = datetime.now().date()
    if days <= 1:
        # Single-day plan
        single = build_day_plan(
            enabled_models,
            today=day,
            bucket_count=bucket_count,
            cooldown_hours=cooldown_hours,
            per_car_sec=per_car_sec,
            counts=counts,
            avg_count=avg_count,
        )
        return PlanRotation(
            plans=[single],
            bucket_count=bucket_count,
            cooldown_hours=cooldown_hours,
            per_car_sec=per_car_sec,
            measured_models=sum(1 for m in single.models if m.slug in counts),
            fallback_models=sum(1 for m in single.models if m.slug not in counts),
        )
    return build_rotation_plan(
        enabled_models,
        start=day,
        bucket_count=days,
        cooldown_hours=cooldown_hours,
        per_car_sec=per_car_sec,
        counts=counts,
        avg_count=avg_count,
    )
