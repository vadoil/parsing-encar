"""Parse the JSON list response from encar search API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SearchListItem:
    """A minimal representation of a car in the search results."""

    encar_id: int
    brand: str
    model: str


def parse_search_list(payload: Any) -> list[SearchListItem]:
    """Extract list of (encar_id, brand, model) from the search API JSON.

    Defensive: returns [] on missing keys or unexpected structure.
    """
    try:
        results = payload["SearchResults"]["EncarSearchResults"]
    except (KeyError, TypeError):
        return []

    items: list[SearchListItem] = []
    for entry in results:
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