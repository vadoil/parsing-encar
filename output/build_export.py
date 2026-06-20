"""Build encar_export.csv + encar_export.html from cached data.

Steps:
1. Load 30 sampled listings from _raw_3pages.json (deterministic seed).
2. Load detail responses from _details.json (already fetched).
3. Emit CSV with all photo URLs (semicolon-separated) + encar view URL.
4. Emit HTML with main photo + 3-thumb strip + "View on EncAr" button.

Photo handling:
- Photos live on `ci.encar.com` (the actual photo CDN). `img.encar.com` is
  filtered on some networks and is NOT a working host — we hit that bug on
  2026-06-20; see encar-open-questions.md / encar-progress.md.
- We embed remote URLs from ci.encar.com directly. They load in a browser.
- `download_photos.py` in the same folder mirrors them locally into
  `output/photos/{carid}/`; re-running build_export.py then auto-picks up
  the local copies and uses relative `photos/...` paths in CSV/HTML.
"""
from __future__ import annotations

import csv
import html
import json
import random
from pathlib import Path

HERE = Path(__file__).parent
RAW_PATH = HERE / "_raw_3pages.json"
DETAILS_PATH = HERE / "_details.json"
PHOTOS_DIR = HERE / "photos"
CSV_PATH = HERE / "encar_export.csv"
HTML_PATH = HERE / "encar_export.html"
JSONLD_PATH = HERE / "encar_export.jsonld.json"

IMG_BASE = "https://ci.encar.com"  # photo CDN (img.encar.com is filtered)
ENCAR_VIEW_URL = "https://www.encar.com/dc/dc_carsearchview.do?carid={carid}"
ENCAR_DETAIL_URL = "https://fem.encar.com/cars/detail/{carid}"

SAMPLE_SIZE = 30
SAMPLE_SEED = 42


def fmt_year_month(year) -> str:
    if not year:
        return ""
    s = f"{int(year):06d}"
    return f"{s[:4]}-{s[4:]}"


def normalize_photo(p: str) -> str:
    """Return local path if the file is already mirrored, else the remote URL."""
    if not p:
        return ""
    if p.startswith("http"):
        return p
    # p is a path like "/carpicture03/pic4213/42131435_001.jpg"
    local = PHOTOS_DIR / Path(p).name
    # path doesn't include carid directory — find it
    for carid_dir in PHOTOS_DIR.glob("*/" + Path(p).name):
        return carid_dir.relative_to(HERE).as_posix()
    if local.exists():
        return local.relative_to(HERE).as_posix()
    return f"{IMG_BASE}{p}"


def sample_listings() -> list[dict]:
    listings = json.loads(RAW_PATH.read_text())
    random.seed(SAMPLE_SEED)
    return random.sample(listings, SAMPLE_SIZE)


def build_csv(sample: list[dict], details: dict[str, dict]) -> None:
    rows: list[dict[str, str]] = []
    for lst in sample:
        carid = int(lst["Id"])
        det = details.get(str(carid), {})
        det.get("category", {})
        spec = det.get("spec", {})
        det.get("advertisement", {})
        condition = det.get("condition", {})

        photos_remote = det.get("photos", []) or []
        photo_paths: list[str] = []
        for p in photos_remote:
            path = p.get("path", "")
            if not path:
                continue
            photo_paths.append(normalize_photo(path))

        fuel = spec.get("fuelName") or lst.get("FuelType", "")
        ym = fmt_year_month(lst.get("Year"))
        price_man = int(lst.get("Price", 0) or 0)
        price_krw = price_man * 10_000

        rows.append({
            "id": str(carid),
            "brand": lst.get("Manufacturer", ""),
            "model": lst.get("Model", ""),
            "badge": lst.get("Badge", ""),
            "year_month": ym,
            "form_year": str(lst.get("FormYear", "") or ""),
            "mileage_km": str(int(lst["Mileage"])) if lst.get("Mileage") else "",
            "price_10k_krw": str(price_man),
            "price_krw": str(price_krw),
            "fuel": fuel,
            "transmission": spec.get("transmissionName", ""),
            "color": spec.get("colorName", ""),
            "body": spec.get("bodyName", ""),
            "seats": str(spec.get("seatCount", "") or ""),
            "city": lst.get("OfficeCityState", ""),
            "photo_count": str(len(photo_paths)),
            "main_photo": photo_paths[0] if photo_paths else "",
            "photo_urls": ";".join(photo_paths),
            "detail_url": ENCAR_DETAIL_URL.format(carid=carid),
            "view_on_encar": ENCAR_VIEW_URL.format(carid=carid),
            "accident_report_available": "yes" if (condition.get("accident") or {}).get("recordView") else "no",
            "pledge_count": str((condition.get("seizing") or {}).get("pledgeCount") or 0),
            "seizing_count": str((condition.get("seizing") or {}).get("seizingCount") or 0),
        })

    cols = [
        "id", "brand", "model", "badge",
        "year_month", "form_year", "mileage_km",
        "price_10k_krw", "price_krw",
        "fuel", "transmission", "color", "body", "seats",
        "city",
        "photo_count", "main_photo", "photo_urls",
        "detail_url", "view_on_encar",
        "accident_report_available", "pledge_count", "seizing_count",
    ]
    with CSV_PATH.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"  CSV: {CSV_PATH} ({CSV_PATH.stat().st_size} bytes, {len(rows)} rows)")


CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f6f7f9; color: #1a1a1a; }
header { padding: 24px 32px; background: #1a1a1a; color: #fff; }
header h1 { margin: 0 0 4px; font-size: 22px; font-weight: 600; }
header p  { margin: 0; opacity: .65; font-size: 13px; }
main { max-width: 1320px; margin: 0 auto; padding: 24px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 18px; }
.card { background: #fff; border: 1px solid #e6e8eb; border-radius: 12px; overflow: hidden; display: flex; flex-direction: column; transition: transform .12s, box-shadow .12s; }
.card:hover { transform: translateY(-2px); box-shadow: 0 10px 28px rgba(0,0,0,.08); }
.photo-main { aspect-ratio: 4/3; background: #eef0f3 center/cover no-repeat; cursor: pointer; }
.photo-thumbs { display: grid; grid-template-columns: repeat(3, 1fr); gap: 2px; padding: 2px; background: #fff; }
.photo-thumbs a { display: block; }
.photo-thumbs div { aspect-ratio: 1; background: #eef0f3 center/cover no-repeat; transition: opacity .12s; }
.photo-thumbs div:hover { opacity: .75; }
.body { padding: 12px 14px 14px; flex: 1; display: flex; flex-direction: column; gap: 6px; }
.title { font-weight: 600; font-size: 16px; margin: 0; }
.trim  { color: #6b7280; font-size: 13px; margin: 0; min-height: 1.2em; }
.meta  { display: flex; flex-wrap: wrap; gap: 4px 10px; font-size: 13px; color: #4b5563; margin: 4px 0 6px; }
.price { font-size: 19px; font-weight: 700; color: #b91c1c; }
.price small { font-weight: 400; color: #6b7280; font-size: 12px; margin-left: 4px; }
.specs { display: flex; flex-wrap: wrap; gap: 4px 6px; font-size: 12px; color: #4b5563; }
.specs span { background: #f3f4f6; padding: 2px 8px; border-radius: 999px; }
.actions { display: flex; gap: 8px; margin-top: auto; padding-top: 10px; }
.btn { display: inline-block; padding: 8px 12px; border-radius: 8px; font-size: 13px; font-weight: 600; text-decoration: none; text-align: center; flex: 1; }
.btn-primary { background: #b91c1c; color: #fff; }
.btn-primary:hover { background: #991b1b; }
.btn-ghost { background: #f3f4f6; color: #1f2937; border: 1px solid #e5e7eb; }
.btn-ghost:hover { background: #e5e7eb; }
.city  { font-size: 12px; color: #6b7280; }
.photo-count { font-size: 11px; color: #9ca3af; }
footer { padding: 24px 32px; color: #6b7280; font-size: 12px; text-align: center; }

.lb { position: fixed; inset: 0; background: rgba(0,0,0,.92); display: none; align-items: center; justify-content: center; z-index: 100; padding: 24px; }
.lb:target { display: flex; }
.lb img { max-width: 92vw; max-height: 80vh; object-fit: contain; }
.lb .close { position: absolute; top: 12px; right: 24px; color: #fff; font-size: 36px; text-decoration: none; line-height: 1; }
.lb-nav { position: absolute; top: 50%; transform: translateY(-50%); color: #fff; font-size: 48px; text-decoration: none; padding: 12px; line-height: 1; user-select: none; }
.lb-nav.prev { left: 16px; } .lb-nav.next { right: 16px; }
.lb-meta { position: absolute; bottom: 16px; left: 0; right: 0; text-align: center; color: #aaa; font-size: 12px; }
"""


def build_html(sample: list[dict], details: dict[str, dict]) -> None:
    cards = []
    lightboxes = []
    jsonld_items = []

    for lst in sample:
        carid = int(lst["Id"])
        det = details.get(str(carid), {})
        det.get("category", {})
        spec = det.get("spec", {})
        det.get("advertisement", {})
        det.get("condition", {})

        photos_remote = det.get("photos", []) or []
        paths: list[str] = []
        for p in photos_remote:
            path = p.get("path", "")
            if path:
                paths.append(normalize_photo(path))

        title = f"{lst.get('Manufacturer','')} {lst.get('Model','')}".strip()
        trim = lst.get("Badge", "")
        ym = fmt_year_month(lst.get("Year"))
        mileage = int(lst["Mileage"]) if lst.get("Mileage") else 0
        price_man = int(lst.get("Price", 0) or 0)
        price_str = f"{price_man:,}" if price_man else "—"
        fuel = spec.get("fuelName") or lst.get("FuelType", "")
        transmission = spec.get("transmissionName", "")
        color = spec.get("colorName", "")
        seats = spec.get("seatCount", "")
        body = spec.get("bodyName", "")
        city = lst.get("OfficeCityState", "")
        year_form = lst.get("FormYear", "")
        price_krw = price_man * 10_000
        view_url = ENCAR_VIEW_URL.format(carid=carid)
        detail_url = ENCAR_DETAIL_URL.format(carid=carid)

        # Card photo block: main + 3 thumbs (4 visible photos total)
        main = paths[0] if paths else ""
        thumbs = paths[1:4]

        thumb_html = "".join(
            f'<a href="#lb-{carid}"><div style="background-image:url(\'{html.escape(p)}\')"></div></a>'
            for p in thumbs
        )
        cards.append(f"""
<article class="card">
  <a class="photo-main" style="background-image:url('{html.escape(main)}')" href="#lb-{carid}" aria-label="Открыть фото {carid}"></a>
  <div class="photo-thumbs">{thumb_html}</div>
  <div class="body">
    <h3 class="title">{html.escape(title)}</h3>
    <p class="trim">{html.escape(trim)}</p>
    <div class="specs">
      {f'<span>📅 {html.escape(ym)}</span>' if ym else ''}
      {f'<span>🛣 {mileage:,} км</span>' if mileage else ''}
      {f'<span>⛽ {html.escape(fuel)}</span>' if fuel else ''}
      {f'<span>⚙ {html.escape(transmission)}</span>' if transmission else ''}
      {f'<span>🎨 {html.escape(color)}</span>' if color else ''}
      {f'<span>💺 {html.escape(str(seats))}</span>' if seats else ''}
      {f'<span>🚙 {html.escape(body)}</span>' if body else ''}
    </div>
    <div class="price">{price_str} <small>만원 · ₩{price_krw:,}</small></div>
    <div class="city">📍 {html.escape(city)} <span class="photo-count">· {len(paths)} фото · id {carid}</span></div>
    <div class="actions">
      <a class="btn btn-primary" href="{html.escape(view_url)}" target="_blank" rel="noopener">Открыть на EncAr ↗</a>
      <a class="btn btn-ghost" href="{html.escape(detail_url)}" target="_blank" rel="noopener">fem.encar</a>
    </div>
  </div>
</article>""".strip())

        # Lightbox with prev/next nav
        if paths:
            imgs = []
            for i, p in enumerate(paths):
                prev_href = f"#lb-{carid}-{i-1}" if i > 0 else f"#lb-{carid}-{len(paths)-1}"
                next_href = f"#lb-{carid}-{i+1}" if i < len(paths)-1 else f"#lb-{carid}-0"
                imgs.append(f"""
<a id="lb-{carid}-{i}"></a>
<div class="lb" id="lb-{carid}-{i}">
  <a href="#" class="close" aria-label="Закрыть">×</a>
  <a class="lb-nav prev" href="{prev_href}">‹</a>
  <img src="{html.escape(p)}" alt="Фото {carid} #{i+1}/{len(paths)}" loading="lazy">
  <a class="lb-nav next" href="{next_href}">›</a>
  <div class="lb-meta">{carid} · фото {i+1} / {len(paths)}</div>
</div>""".strip())
            # Make the card's main link open the first lightbox
            lightboxes.append("\n".join(imgs))

        jsonld_items.append({
            "@type": "Vehicle",
            "name": f"{title} {trim}".strip(),
            "brand": {"@type": "Brand", "name": lst.get("Manufacturer", "")},
            "model": lst.get("Model", ""),
            "vehicleModelDate": str(year_form),
            "mileageFromOdometer": {
                "@type": "QuantitativeValue",
                "value": mileage,
                "unitCode": "KMT",
            },
            "fuelType": fuel,
            "vehicleConfiguration": transmission,
            "color": color,
            "vehicleSeatingCapacity": seats,
            "bodyType": body,
            "offers": {
                "@type": "Offer",
                "priceCurrency": "KRW",
                "price": price_krw,
                "url": view_url,
            },
            "image": paths[:6],
        })

    page = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BMW X5 (G05) — выборка с ENCAR</title>
<meta name="description" content="30 случайных объявлений BMW X5 (G05) 2018+ с корейского сайта encar.com">
<style>{CSS}</style>
</head>
<body>
<header>
  <h1>BMW X5 (G05) — выборка</h1>
  <p>30 случайных объявлений 2018+. Источник: api.encar.com. Сгенерировано encar-parser.</p>
</header>
<main>
  <div class="grid">
{chr(10).join(cards)}
  </div>
</main>
<footer>
  Источник: <a href="https://www.encar.com">encar.com</a> ·
  API: api.encar.com/search/car/list/general · detail: /v1/readside/vehicle/{{id}} ·
  Фото: ci.encar.com (для локальной копии запустите <code>download_photos.py</code>)
</footer>
{chr(10).join(lightboxes)}
</body>
</html>
"""
    HTML_PATH.write_text(page, encoding="utf-8")
    print(f"  HTML: {HTML_PATH} ({HTML_PATH.stat().st_size} bytes)")

    jsonld = {"@context": "https://schema.org", "@graph": jsonld_items}
    JSONLD_PATH.write_text(json.dumps(jsonld, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  JSON-LD: {JSONLD_PATH} ({JSONLD_PATH.stat().st_size} bytes)")


def main() -> None:
    if not RAW_PATH.exists():
        raise SystemExit(f"Missing {RAW_PATH} — re-fetch raw listings first")
    if not DETAILS_PATH.exists():
        raise SystemExit(f"Missing {DETAILS_PATH} — re-fetch details first")

    print("Loading sample + details from disk")
    sample = sample_listings()
    details = json.loads(DETAILS_PATH.read_text())
    print(f"  {len(sample)} listings, {len(details)} detail responses")

    print("Building CSV with full photo URLs")
    build_csv(sample, details)

    print("Building HTML with gallery + lightbox")
    build_html(sample, details)


if __name__ == "__main__":
    main()
