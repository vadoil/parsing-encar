"""Build models.yaml pool from catalog_test.xlsx.

Pipeline per (brand_label, family_label):
1. Lookup brand-specific Encar strings (English for imports, Korean for domestic).
2. Try multiple candidate model_name translations (different token orderings).
3. Probe each candidate via count-API; first one with Count > 0 wins.
4. Save raw_q + metadata; if nothing works, mark as unresolved.

Usage:
    uv run python -c "from encar_parser.build_pool import build_pool; ..."
"""
from __future__ import annotations

import asyncio
import re
import urllib.parse
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import openpyxl
import yaml

from encar_parser.fetchers.api import ApiFetcher
from encar_parser.fetchers.base import FetcherError

DOMESTIC_BRANDS = {"Hyundai", "Kia", "Genesis", "Ssangyong"}
DOMESTIC_BRAND_KR = {
    "Hyundai": "현대",
    "Kia": "기아",
    "Genesis": "제네시스",
    "Ssangyong": "쌍용",
}
# Kept for backward compatibility with imports like
# `from encar_parser.build_pool import DOMESTIC_BRANDS`. New code should
# import from :mod:`encar_parser.car_type` instead — that's the single
# source of truth. The English-name set above is a strict subset of
# ``car_type.DOMESTIC_BRANDS_EN_TO_KR`` (which also covers KG Mobility,
# Renault Korea, GM Korea, etc.).
# For imports, Encar sometimes uses the Korean transliteration of the brand
# as the Manufacturer cell. Verified against the live API in 2026-06-18
# session. Brands not in this map (e.g. 'BMW', 'Land Rover') fall back to
# the English label as-is.
IMPORT_MANUFACTURER_NAME = {
    "Audi": "아우디",
    "Porsche": "포르쉐",
    "Jaguar": "재규어",
    "Infiniti": "인피니티",
}
# Base family names (without tokens). Keys are English Excel labels, values are
# canonical Korean ModelGroup strings.
FAMILY_BASE_KR = {
    # Hyundai
    "Accent": "엑센트",
    "i30": "i30",
    "Avante": "아반떼",
    "Grandeur": "그랜저",
    "Sonata": "쏘나타",
    "Tucson": "투싼",
    "Santa Fe": "싼타페",
    "Palisade": "팰리세이드",
    "Kona": "코나",
    "Veloster": "벨로스터",
    # Genesis (kept Latin per user)
    "G80": "G80",
    "G90": "G90",
    "GV60": "GV60",
    "GV70": "GV70",
    "GV80": "GV80",
    # Kia
    "Morning": "모닝",
    "Sportage": "스포티지",
    "Sorento": "소렌토",
    "Carnival": "카니발",
    "K5": "K5",
    "K3": "K3",
    "K9": "K9",
    "Niro": "니로",
    "EV6": "EV6",
    "EV9": "EV9",
    # Ssangyong
    "Musso": "무쏘",
    "Rexton": "렉스턴",
    "Torres": "토레스",
    "Tivoli": "티볼리",
}
# Token translations. Keys are English tokens that appear in family_label;
# values are the canonical Korean equivalents.
TOKEN_KR = {
    "Hybrid": "하이브리드",
    "D Edge": "디 엣지",
    "The New": "더 뉴",
    "All New": "올 뉴",
    "Sports": "스포츠",
    "New type": "뉴 타입",
    "Electrified": "일렉트리파이드",
    "Urban": "어반",
    "Cannes": "칸",
    "GT": "GT",
    "EV": "EV",
}

# BMW model names use English for the chassis letters (X3, M2, Z4) but Korean
# transliteration for "N Series" → "N시리즈". Active Tourer / Gran Coupe get
# their own Korean forms. Verified against the live API in 2026-06-18.
BMW_SUBMODEL_KR = {
    "1 Series": "1시리즈",
    "2 Series": "2시리즈",
    "3 Series": "3시리즈",
    "4 Series": "4시리즈",
    "5 Series": "5시리즈",
    "6 Series": "6시리즈",
    "7 Series": "7시리즈",
    "8 Series": "8시리즈",
    "2 Series Active Tourer": "2시리즈 액티브 투어러",
    "2 Series Gran Coupe": "2시리즈 그란 쿠페",
}


def _extract_gen_code(family_label: str) -> str | None:
    """Pull the generation code in parens, e.g. 'Sonata (DN8)' → 'DN8'."""
    m = re.search(r"\(([^)]+)\)\s*$", family_label)
    return m.group(1) if m else None


def _strip_gen_code(family_label: str) -> str:
    """'Sonata (DN8)' → 'Sonata'."""
    return re.sub(r"\s*\([^)]+\)\s*$", "", family_label).strip()


def _strip_tokens(family_label: str) -> tuple[list[str], str]:
    """Split 'The New Sonata Hybrid (DN8)' into (['The New', 'Hybrid'], 'Sonata')."""
    base = _strip_gen_code(family_label)
    tokens: list[str] = []
    for token in sorted(TOKEN_KR.keys(), key=len, reverse=True):
        if re.search(r"\b" + re.escape(token) + r"\b", base):
            tokens.append(token)
            base = re.sub(r"\b" + re.escape(token) + r"\b", "", base)
    base = re.sub(r"\s+", " ", base).strip()
    return tokens, base


def _candidate_kr_models(family_label: str, base_kr: str) -> list[str]:
    """Generate candidate Korean Model strings to try (token permutations)."""
    from itertools import permutations

    gen = _extract_gen_code(family_label)
    tokens, _ = _strip_tokens(family_label)
    token_kr = [TOKEN_KR[t] for t in tokens]
    if not gen:
        return [" ".join([base_kr] + token_kr).strip()]
    candidates = []
    seen: set[str] = set()

    def _add(c: str) -> None:
        c = c.strip()
        if c and c not in seen:
            seen.add(c)
            candidates.append(c)

    # Token + base combinations — single space join, no leading/trailing junk.
    def _join(parts: list[str]) -> str:
        return " ".join(p for p in parts if p).strip()

    _add(_join([base_kr, f"({gen})"]))
    _add(_join([base_kr, *token_kr, f"({gen})"]))
    _add(_join([*token_kr, base_kr, f"({gen})"]))
    for perm in set(permutations(token_kr)):
        _add(_join([*perm, base_kr, f"({gen})"]))
        _add(_join([base_kr, *perm, f"({gen})"]))
    return candidates


def build_q_for(
    car_type: str,
    manufacturer: str,
    model_group: str,
    model: str,
    year_from: int = 2018,
    year_to: int = 2026,
) -> str:
    """Build the 4-level q expression with canonical Encar structure."""
    inner = f"Model.{model}."
    mid = f"C.ModelGroup.{model_group}._.{inner}"
    cell = f"(C.Manufacturer.{manufacturer}._.({mid}))"
    return (
        f"(And.Hidden.N._.(C.CarType.{car_type}._.{cell})"
        f"_.Year.range({year_from * 100:06d}..{year_to * 100 + 99:06d}).)"
    )


def _split_import_model(family_label: str, brand_label: str) -> tuple[str, str]:
    """Strip the brand prefix from a family_label and split off the gen code.

    Returns (model_with_gen, model_base). Examples:
        ('BMW', 'X5 (G05)')        -> ('X5 (G05)', 'X5')
        ('Porsche', '911 (992)')   -> ('911 (992)', '911')
        ('Audi', 'A6 (C8)')        -> ('A6 (C8)', 'A6')
        ('BMW', 'iX')              -> ('iX', 'iX')   # no gen code
    """
    rest = family_label
    for prefix in (brand_label + " ", brand_label):
        if rest.startswith(prefix):
            rest = rest[len(prefix):].strip()
            break
    gen = _extract_gen_code(rest)
    if gen:
        model_base = re.sub(r"\s*\([^)]+\)\s*$", "", rest).strip()
        return rest, model_base
    return rest, rest


def _candidate_qs_for_import(
    brand_label: str,
    family_label: str,
    car_type: str,
    year_from: int,
    year_to: int,
) -> list[tuple[str, str, str, str]]:
    """Build an ordered list of (manufacturer, model_group, model, q) tuples.

    Priority per (manufacturer, model_name_variant):
      1. model_base as ModelGroup + model_with_gen as Model (most common)
      2. model_with_gen as both
      3. model_base as both (no gen code anywhere)

    The first manufacturer tried is the known one (Korean or English per
    IMPORT_MANUFACTURER_NAME); the English label is added as a fallback.

    For BMW, the model name is also tried in Korean form (e.g. '1 Series' →
    '1시리즈') because Encar stores some BMW names transliterated.
    """
    model_with_gen, model_base = _split_import_model(family_label, brand_label)
    known_manuf = IMPORT_MANUFACTURER_NAME.get(brand_label, brand_label)

    manuf_candidates = [known_manuf]
    if brand_label != known_manuf and brand_label not in manuf_candidates:
        manuf_candidates.append(brand_label)

    # Build the set of (mg, m) pairs to try per manufacturer.
    base_variants = [
        (model_base, model_with_gen),       # MG=base, M=with_gen
        (model_with_gen, model_with_gen),   # MG=with_gen, M=with_gen
        (model_base, model_base),           # MG=base, M=base
    ]
    # For BMW, also try the Korean transliteration of the model name.
    bmw_variants: list[tuple[str, tuple[str, str]]] = []
    if brand_label == "BMW":
        # Sort keys longest-first so "2 Series Active Tourer" matches before
        # "2 Series" (the prefix would otherwise shadow the more specific key).
        for en in sorted(BMW_SUBMODEL_KR.keys(), key=len, reverse=True):
            if model_with_gen.startswith(en):
                kr = BMW_SUBMODEL_KR[en]
                gen = _extract_gen_code(model_with_gen)
                kr_with_gen = f"{kr} ({gen})" if gen else kr
                bmw_variants.append(("kr", (
                    (kr, kr_with_gen),              # MG=kr_base, M=kr_with_gen
                    (kr_with_gen, kr_with_gen),     # MG=kr_with_gen, M=kr_with_gen
                    (kr, kr),                       # MG=kr_base, M=kr_base
                )))
                break

    out: list[tuple[str, str, str, str]] = []
    seen: set[str] = set()

    def _add(manuf: str, mg: str, m: str) -> None:
        q = build_q_for(car_type, manuf, mg, m,
                        year_from=year_from, year_to=year_to)
        if q not in seen:
            seen.add(q)
            out.append((manuf, mg, m, q))

    for manuf in manuf_candidates:
        # English model variants
        for mg, m in base_variants:
            _add(manuf, mg, m)
        # Korean transliteration (BMW only)
        for _label, variants in bmw_variants:
            for mg, m in variants:
                _add(manuf, mg, m)
    return out


@dataclass
class PoolEntry:
    brand_label: str
    family_label: str
    car_type_code: str
    manufacturer: str
    model_group: str
    model: str
    raw_q: str
    count: int | None = None
    enabled: bool = True
    note: str = ""


async def probe_count(q: str) -> int | None:
    """Probe the count-API; return Count (or None on error)."""
    encoded = urllib.parse.quote(q, safe="()._,")
    url = (
        f"https://api.encar.com/search/car/list/general?"
        f"count=true&q={encoded}&sr=%7CModifiedDate%7C0%7C5"
    )
    async with ApiFetcher() as api:
        try:
            resp = await api.get(url, referer="https://www.encar.com/")
            payload = resp.json()
            cnt = payload.get("Count")
            return int(cnt) if isinstance(cnt, int) else None
        except FetcherError:
            return None
        except Exception:
            return None


async def resolve_pair(brand_label: str, family_label: str) -> PoolEntry:
    """Probe candidates for one (brand, family); return best PoolEntry."""
    is_domestic = brand_label in DOMESTIC_BRANDS
    car_type = "Y" if is_domestic else "N"

    if is_domestic:
        _, base_en = _strip_tokens(family_label)
        base_kr = FAMILY_BASE_KR.get(base_en)
        if not base_kr:
            return PoolEntry(
                brand_label=brand_label,
                family_label=family_label,
                car_type_code=car_type,
                manufacturer=DOMESTIC_BRAND_KR.get(brand_label, brand_label),
                model_group="",
                model="",
                raw_q="",
                enabled=False,
                note="no Korean family mapping — manual raw_q needed",
            )
        # Domestic brands: manufacturer is the Korean brand name.
        manufacturer = DOMESTIC_BRAND_KR[brand_label]
        model_group = base_kr
        # Candidates: Korean model names (token permutations around the base).
        kr_candidates = _candidate_kr_models(family_label, base_kr)
        candidate_triples = [
            (manufacturer, model_group, name) for name in kr_candidates
        ]
    else:
        # Imports: try a structured candidate list (korean manuf + several
        # model_group/model spellings). _candidate_qs_for_import already
        # encodes the manufacturer in the tuple.
        candidate_triples = [
            (manuf, mg, m)
            for manuf, mg, m, _q in _candidate_qs_for_import(
                brand_label, family_label, car_type, year_from=2018, year_to=2026,
            )
        ]
        # Use the first candidate's manufacturer as the "default" if nothing
        # resolves — it'll show up in the unresolved PoolEntry for context.
        manufacturer = candidate_triples[0][0] if candidate_triples else brand_label
        model_group = candidate_triples[0][1] if candidate_triples else ""

    for manuf, mg, m in candidate_triples:
        q = build_q_for(car_type, manuf, mg, m)
        cnt = await probe_count(q)
        if cnt is not None and cnt > 0:
            return PoolEntry(
                brand_label=brand_label,
                family_label=family_label,
                car_type_code=car_type,
                manufacturer=manuf,
                model_group=mg,
                model=m,
                raw_q=q,
                count=cnt,
                enabled=True,
            )

    return PoolEntry(
        brand_label=brand_label,
        family_label=family_label,
        car_type_code=car_type,
        manufacturer=manufacturer,
        model_group=model_group,
        model="",
        raw_q="",
        enabled=False,
        note="no candidate with Count > 0 — manual raw_q needed",
    )


def _load_pairs(excel_path: Path) -> set[tuple[str, str]]:
    wb = openpyxl.load_workbook(excel_path, read_only=True, data_only=True)
    ws = wb["Listings"]
    pairs: set[tuple[str, str]] = set()
    for row in ws.iter_rows(values_only=True):
        if row[0] == "brand_label":
            continue
        brand, family = row[0], row[1]
        if brand and family:
            pairs.add((brand, family))
    return pairs


def _slugify(family_label: str) -> str:
    slug = family_label.lower()
    for ch in " ()/":
        slug = slug.replace(ch, "-")
    return re.sub(r"-+", "-", slug).strip("-")


def _to_models_yaml(entries: list[PoolEntry]) -> dict[str, Any]:
    """Convert entries to the structure written to models.yaml."""
    by_brand: dict[str, list[PoolEntry]] = defaultdict(list)
    for e in entries:
        by_brand[e.brand_label].append(e)
    domestic_brands = sorted(b for b in by_brand if b in DOMESTIC_BRANDS)
    import_brands = sorted(b for b in by_brand if b not in DOMESTIC_BRANDS)

    models_list: list[dict[str, Any]] = []
    priority_counter = 10
    for brand in domestic_brands + import_brands:
        for e in sorted(by_brand[brand], key=lambda x: x.family_label):
            record: dict[str, Any] = {
                "slug": _slugify(e.family_label),
                "name": f"{e.brand_label} {e.family_label}",
                "enabled": e.enabled,
                "priority": priority_counter,
                "car_type_code": e.car_type_code,
                "manufacturer": e.manufacturer,
                "model_group": e.model_group,
                "model": e.model,
                "raw_q": e.raw_q,
                "hp_brand_label": e.brand_label,
                "hp_family_label": e.family_label,
            }
            if not e.enabled and e.note:
                record["note"] = e.note
            models_list.append(record)
            priority_counter += 10
    return {"models": models_list}


def _write_report(entries: list[PoolEntry], path: Path) -> None:
    resolved = [e for e in entries if e.enabled]
    unresolved = [e for e in entries if not e.enabled]

    # Split unresolved into two buckets:
    #   - "no candidate matched" — the brand probably exists in Encar but our
    #     candidate generator couldn't find the right spelling. These need
    #     human help (paste a real `q` from DevTools).
    #   - "brand not in Encar" — verified via probe that the brand returns 0
    #     for every model. No amount of tweaking will help; skip the model.
    brands_with_listings = _BRANDS_WITH_LISTINGS_CACHE
    no_candidate = [e for e in unresolved if e.brand_label in brands_with_listings]
    no_listings = [e for e in unresolved if e.brand_label not in brands_with_listings]

    lines = [
        "## Build pool report",
        f"Total pairs: {len(entries)}",
        f"Resolved (Count > 0): {len(resolved)}",
        f"Unresolved — no candidate matched: {len(no_candidate)}",
        f"Unresolved — brand not in Encar catalog: {len(no_listings)}",
        "",
        "### No candidate matched (manual raw_q from DevTools may help)",
    ]
    for e in no_candidate:
        lines.append(f"  - {e.brand_label} {e.family_label}: {e.note}")
    lines.append("")
    lines.append("### Brand not in Encar (skip these — no listings exist)")
    for e in no_listings:
        lines.append(f"  - {e.brand_label} {e.family_label}")
    path.write_text("\n".join(lines), encoding="utf-8")


# Filled lazily by the brand-discovery pass in `build_pool`. Used by
# _write_report to bucket unresolved entries into "needs human" vs "skip".
_BRANDS_WITH_LISTINGS_CACHE: set[str] = set()


async def build_pool(
    excel_path: Path,
    output: Path,
    report: Path,
    max_concurrency: int = 4,
    probe_delay_sec: float = 0.5,
) -> None:
    pairs = _load_pairs(excel_path)
    print(f"Found {len(pairs)} distinct (brand, family) pairs in {excel_path.name}")

    sem = asyncio.Semaphore(max_concurrency)

    async def run(pair: tuple[str, str]) -> PoolEntry:
        async with sem:
            if probe_delay_sec > 0:
                await asyncio.sleep(probe_delay_sec)
            return await resolve_pair(*pair)

    tasks = [run(p) for p in sorted(pairs)]
    entries: list[PoolEntry] = await asyncio.gather(*tasks)

    # Build a brand → has-listings cache for the report bucketing. A brand is
    # considered "has listings" if at least one of its models resolved.
    global _BRANDS_WITH_LISTINGS_CACHE
    _BRANDS_WITH_LISTINGS_CACHE = {
        e.brand_label for e in entries if e.enabled
    }

    output.write_text(
        yaml.safe_dump(
            _to_models_yaml(entries), allow_unicode=True, sort_keys=False, width=120
        ),
        encoding="utf-8",
    )
    print(f"Wrote {len(entries)} entries to {output}")

    _write_report(entries, report)
    print(f"Wrote report to {report}")
