"""Build human-facing Encar URLs from a SearchModel row.

These URLs open Encar's own front-end search results page for a given
model — useful from the CRM so the operator can verify that a model
is configured correctly without copy-pasting the API URL into a
browser.

Format (verified against the live site via DevTools):

    https://www.encar.com/fc/fc_carsearchlist.do?carType=<for|kor>#!<JSON>

* ``carType`` query param is the *front-end* code:
  ``for`` = import, ``kor`` = domestic.
* The hash payload is a JSON object whose ``action`` field is exactly
  the same S-expression we use for the API's ``q`` parameter.

Why a helper?
-------------
The same model parameters (manufacturer, model_group, year range,
car_type_code) drive TWO different URLs:

* the API URL we fetch from (stored in ``sm.encar_action["api_url"]``);
* the front-end URL we link to from the CRM (this module).

The front-end URL has a JSON hash that the API URL doesn't, but the
inner ``action`` is identical. Centralising the construction here
keeps the two in sync — change the S-expression rules in one place,
both URLs follow.

Source of truth
---------------
``SearchModel.encar_action`` is a JSONB dict with at least ``q`` (the
unencoded S-expression). We extract the CarType cell from ``q`` to
pick the ``carType=`` query param — no need to re-parse structured
fields that may have drifted from the saved S-expression.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any

from encar_parser.db.models import SearchModel


_FRONTEND_BASE = "https://www.encar.com/fc/fc_carsearchlist.do"

# Pre-compiled because we extract the same substring for every link.
_CARTYPE_RE = re.compile(r"C\.CarType\.([YN])\._")


def extract_car_type_from_action(action: str | None) -> str:
    """Return ``"Y"`` or ``"N"`` from the ``C.CarType.Y/N._`` cell in ``action``.

    Defaults to ``"N"`` (import) if the substring is missing — the
    same safer-default policy as :mod:`encar_parser.car_type`.

    Exposed at module scope because the categories page needs to
    display the CarType badge for every model row, and the column
    isn't on ``SearchModel`` yet (the CarType fix added the
    *classifier* but not a DB migration — that's a separate step).
    Until then, ``encar_action["q"]`` is the only place CarType lives.
    """
    if not action:
        return "N"
    m = _CARTYPE_RE.search(action)
    return m.group(1) if m else "N"


def _extract_car_type(action: str) -> str:
    """Backwards-compatible alias for :func:`extract_car_type_from_action`."""
    return extract_car_type_from_action(action)


def _front_car_type_param(car_type_code: str) -> str:
    """Map our Y/N car_type_code to Encar's front-end ``for``/``kor`` param.

    The front-end uses different letters than the S-expression filter:
    the API uses ``Y`` (domestic) / ``N`` (import) inside the q-filter,
    but the page-level ``carType=`` parameter is ``kor`` / ``for``.
    """
    return {"Y": "kor", "N": "for"}.get(car_type_code, "for")


def _action_string(sm: SearchModel) -> str:
    """Return the S-expression to embed in the front-end URL's ``action`` field.

    Prefers the unencoded ``encar_action["q"]`` (the user's authoritative
    value, possibly hand-tuned). Falls back to a minimal degenerate cell
    if nothing is stored — the link still works, just with a broader
    search.
    """
    action = sm.encar_action.get("q") if sm.encar_action else None
    if action:
        return action
    # Degenerate case: no q at all. Encar still accepts the URL — it
    # returns ALL cars for the brand (or all cars, if even the brand
    # is unknown). We log nothing here; this branch is only hit for
    # very old DB rows.
    car = _extract_car_type("")
    mfr = sm.encar_action.get("manufacturer", "Unknown") if sm.encar_action else "Unknown"
    return f"(C.CarType.{car}._.(C.Manufacturer.{mfr}._.))"


def encar_web_url(sm: SearchModel) -> str:
    """Return the consumer-facing Encar search URL for ``sm``.

    The URL opens Encar's own front-end search page with the same
    filter we use for the API call, plus the standard JSON hash
    payload. Operators can use this to verify a model is configured
    correctly without copy-pasting the API URL into a browser.

    Note: the existing ``sm.encar_action["frontend_url"]`` does NOT
    include the hash fragment — it was built before the JSON payload
    was required. We construct a fresh URL here with the full hash.
    """
    action = _action_string(sm)
    car_type_code = _extract_car_type(action)
    payload: dict[str, Any] = {
        "action": action,
        "toggle": {},
        "layer": "",
        "sort": "ModifiedDate",
        "page": 1,
        "limit": 20,
        "searchKey": "",
        "loginCheck": False,
    }
    hash_fragment = urllib.parse.quote(json.dumps(payload, ensure_ascii=False))
    return (
        f"{_FRONTEND_BASE}?carType={_front_car_type_param(car_type_code)}"
        f"#!{hash_fragment}"
    )


def effective_encar_url(
    sm: SearchModel,
    manual_override: str | None,
) -> tuple[str, bool]:
    """Pick the URL the /categories row should link out to.

    Returns ``(url, is_manual)``. If the operator has saved a manual
    URL via the form on /categories, ``is_manual`` is True and
    ``url`` is exactly what they typed (no validation — we trust the
    human). Otherwise the auto-generated :func:`encar_web_url` is used.
    """
    manual = (manual_override or "").strip()
    if manual:
        return manual, True
    return encar_web_url(sm), False
