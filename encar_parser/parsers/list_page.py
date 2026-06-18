"""Parse the JSON list response from the encar search API.

Real shape (api.encar.com/search/car/list/general):
    {"Count": 123, "SearchResults": [{"Id": "42131435", "Manufacturer": "BMW",
     "Model": "X5", "Badge": "...", "Price": 8900, "Year": 202111, ...}, ...]}

A legacy/mock shape is also accepted for backwards compatibility:
    {"SearchResults": {"EncarSearchResults": [{"Id": ...}, ...]}}
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SearchListItem:
    """A minimal representation of a car in the search results."""

    encar_id: int
    brand: str
    model: str


@dataclass
class SearchListResult:
    """Parsed list response: items plus the total count reported by encar."""

    items: list[SearchListItem]
    total: int | None = None


def _extract_results(payload: Any) -> list:
    """Return the list of result entries from either the real or legacy shape."""
    if not isinstance(payload, dict):
        return []
    results = payload.get("SearchResults")
    # Real API: SearchResults is a list.
    if isinstance(results, list):
        return results
    # Legacy/mock: SearchResults.EncarSearchResults is a list.
    if isinstance(results, dict):
        nested = results.get("EncarSearchResults")
        if isinstance(nested, list):
            return nested
    return []


def parse_search_list(payload: Any) -> list[SearchListItem]:
    """Extract list of (encar_id, brand, model) from the search API JSON.

    Defensive: returns [] on missing keys or unexpected structure.
    """
    items: list[SearchListItem] = []
    for entry in _extract_results(payload):
        try:
            items.append(
                SearchListItem(
                    encar_id=int(entry["Id"]),
                    brand=str(entry.get("Manufacturer", "")),
                    model=str(entry.get("Model", "")),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue  # skip malformed entries
    return items


def parse_search_list_result(payload: Any) -> SearchListResult:
    """Like parse_search_list but also returns the total count (for pagination)."""
    items = parse_search_list(payload)
    total: int | None = None
    if isinstance(payload, dict):
        raw_total = payload.get("Count")
        if isinstance(raw_total, int):
            total = raw_total
    return SearchListResult(items=items, total=total)
