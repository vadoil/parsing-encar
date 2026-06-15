"""Decide which models to process today based on a 3-day rotation."""

from __future__ import annotations

from datetime import date

from encar_parser.db.models import SearchModel


def models_for_today(
    models: list[SearchModel], today: date
) -> list[SearchModel]:
    """Return the subset of enabled models assigned to today.

    Models are sorted by (priority, slug) for determinism. Then split into
    3 buckets based on their sorted index. The bucket for `today` is
    `(today.isoweekday() - 1) % 3`.
    """
    sorted_models = sorted(models, key=lambda m: (m.priority, m.slug))
    bucket = (today.isoweekday() - 1) % 3
    return [m for i, m in enumerate(sorted_models) if i % 3 == bucket]
