# Encar API field map (ground truth from `output/_details.json`)

Generated 2026-06-18 from 30 BMW X5 (G05) records (`vehicleId` 40480671–42188994).
Source: `https://api.encar.com/v1/readside/vehicle/{vehicleId}`.
Files: raw responses in `output/_details.json`, CSV export in `output/encar_export.csv`.

All paths are real. Counts shown are `distinct value count × occurrences` in the
sample, e.g. `{False: 30}` means every car in the sample has this value.
`None` is reported as `None × N`.

## Top-level structure

```text
advertisement       (price, priceBenefit, etc.)
category            (model identity, grade, warranty)
condition           (accident, inspection, seizing; insurance=null in our sample)
contact             (dealer info)
contents            (long dealer narrative — free text)
manage              (timestamps, view counts)
options             (option codes — NOT decoded)
partnership         (dealer / brand / lease / rent / testdrive / certifiedBrand)
photos[]            (image metadata)
spec                (vehicle physical attributes)
view                (counters: views, photo+ flag, EV battery info)
vehicleId           int — canonical Encar ID
vehicleNo           masked plate
vehicleType         "CAR" (other values exist for RV / 트럭 etc)
vin                 VIN string or null
```

## `category.*` — model identity (23 keys, all 30 sample)

| Path | Type | Sample value(s) | Notes |
|---|---|---|---|
| `category.manufacturerCd` | str | `'012' × 30` | Stable code per OEM |
| `category.manufacturerName` | str | `'BMW' × 30` | Korean display (Latin here) |
| `category.manufacturerEnglishName` | str | `'BMW' × 30` | English name (imports only populated?) |
| `category.modelCd` | str | `'066' × 30` | Stable per model |
| `category.modelName` | str | `'X5 (G05)' × 30` | Full name with gen code |
| `category.modelGroupCd` | str | `'008' × 30` | Stable per family (X5) |
| `category.modelGroupName` | str | `'X5' × 30` | Korean/Latin family name |
| `category.modelGroupEnglishName` | str | `'X5' × 30` | English family name |
| `category.gradeCd` | str | 10 distinct, top: `'002' ×8, '007' ×7, '001' ×4` | Trim code |
| `category.gradeName` | str | 10 distinct, top: `'xDrive 30d M 스포츠' ×8, 'xDrive 40i M 스포츠' ×7, 'xDrive 30d xLine' ×4` | **Korean trim name — PRIMARY source for engine_code** (e.g. '30d' / '40i' / '50e' / 'M50i') |
| `category.gradeEnglishName` | str | same 10, e.g. `'xDrive 30d M Sport' ×8` | English trim name |
| `category.gradeDetailCd` | str or null | `None × 30` | Sub-grade code (not used in our sample) |
| `category.gradeDetailName` | str or null | `None × 30` | Sub-grade name |
| `category.gradeDetailEnglishName` | str or null | `None × 30` | Sub-grade English |
| `category.yearMonth` | str | 21 distinct, e.g. `'202202' ×3, `'202102' ×2` | YYYYMM — **registration date** (first registration) |
| `category.formYear` | str | 8 distinct, top: `'2025' ×8, '2022' ×6, '2021' ×6` | Model year (e.g. car labelled 2023 model year can be registered 2024) |
| `category.importType` | str | `'REGULAR_IMPORT' ×27, 'NONE_IMPORT_TYPE' ×3` | Import vs domestic. **Critical for translation table** (REGULAR_IMPORT → Официальный, PARALLEL_IMPORT → Параллельный, NONE_IMPORT_TYPE → ?) |
| `category.domestic` | bool | `False × 30` | True for Korean-market cars (Hyundai/Kia/Genesis etc.) |
| `category.originPrice` | int (만원) | 18 distinct, top: `13050 ×5, 11720 ×3, 10170 ×3` | **Original MSRP when new** in 만원 (so 13050 = 130 500 000 KRW) |
| `category.jatoVehicleId` | int | 20 distinct, e.g. `742661520200301 ×3` | JATO classification ID — useful for joining with HP catalog |
| `category.type` | str | `'CAR' × 30` | Same as top-level `vehicleType` |
| `category.warranty` | dict | 16 distinct | See below |
| `category.warranty.userDefined` | bool | True/False | Whether dealer-claimed |
| `category.warranty.companyName` | str\|null | 14 distinct values | **NEEDS NORMALIZATION** — see Warranty below |
| `category.warranty.bodyMonth` | int | 24, 36 etc. | Body coverage in months |
| `category.warranty.bodyMileage` | int | 9999999, 200000 etc. | Body coverage in km |
| `category.warranty.transmissionMonth` | int | 36 etc. | Transmission coverage months |
| `category.warranty.transmissionMileage` | int | 60000 etc. | Transmission coverage km |

### Warranty.companyName values seen (14 distinct, 30 cars)

```text
None × 13           → normalize to null
'BMW' × 1
'BMW 코리아' × 2
'BMW코리아' × 4
'bmw코리아' × 3
'비엠더블유 코리아' × 1   (5 variants of same brand)
'한독' × 1
'한독모터스' × 1          (2 variants of same)
'현대해상' × 1
'신한ez손해보험' × 1
'가능' × 1              (string anomaly — should be object)
'불가능' × 1            (string anomaly)
```

Normalizer contract (Phase 2):
- case-insensitive ASCII fold
- known aliases → canonical name (`'BMW 코리아' / 'BMW코리아' / 'bmw코리아' / '비엠더블유 코리아' / 'BMW' → 'BMW'`, `'한독' / '한독모터스' → '한독모터스'`)
- `'가능' / '불가능' / '' / None / 'none'` → `null` (with `warranty_anomaly: bool` flag for the cases that should have been objects)

## `spec.*` — vehicle physical attributes (13 keys, all 30 sample)

| Path | Type | Sample value(s) | Notes |
|---|---|---|---|
| `spec.type` | str | `'CAR' × 30` | Same as `vehicleType` — redundant |
| `spec.mileage` | int (km) | min 600, max 119 685, median 47 668 | Odometer reading |
| `spec.displacement` | int (cc) | `2998 × 15, 2993 × 14, 4395 × 1` | Engine displacement |
| `spec.fuelCd` | str | `'001' × 9, '002' × 14, '006' × 7` | **Code — use this for translation, not fuelName** |
| `spec.fuelName` | str | `'가솔린' × 9, '디젤' × 14, '가솔린+전기' × 7` | Korean display name (mapped to codes above) |
| `spec.transmissionName` | str | `'오토' × 30` | Korean display (no code in spec — see `category.transmission` which is null in this sample) |
| `spec.bodyName` | str | `'SUV' × 30` | Korean body name |
| `spec.colorName` | str | `'흰색' × 10, '검정색' × 9, '청색' × 5, '쥐색' × 4, '갈대색' × 2` | Korean color name |
| `spec.customColor` | str or null | `None × 30` | If non-null, dealer-painted special color |
| `spec.seatCount` | int or null | `5 × 21, 7 × 5, None × 4` | Number of seats |
| `spec.tradeType` | str or null | `'D' × 25, 'S' × 2, 'B' × 1, None × 2` | **Code — seller type** (`D`=Dealer, `S`=?, `B`=?, see partnership.dealer) |
| `spec.tradeOwnerType` | str or null | `None × 29, 'C' × 1` | Sub-classification (only 1 non-null — `'C'`) |
| `spec.tradeCompanyName` | str or null | `None × 29, '주식회사하나오토컴퍼니' × 1` | Company name when tradeType=D and ownership is corporate |
| `spec.inspections` | null | `None × 30` | Encars inspection results (empty in this sample) |

### Fuel code mapping (verified)

```text
'001' → 가솔린   (gasoline)
'002' → 디젤      (diesel)
'006' → 가솔린+전기 (gasoline + electric, hybrid)
```

Phase 3: build full `FUEL_CD_TO_RU` map. Log unknown codes at parse time.

## `condition.*` — accident / insurance / inspection (3 keys always present)

| Path | Type | Sample value(s) | Notes |
|---|---|---|---|
| `condition.accident.recordView` | bool | `True × 29, False × 1` | **MISLEADING** — 29/30 is not "29 cars had accidents" |
| `condition.accident.resumeView` | bool | `True × 30` | Always True in our sample |
| `condition.insurance` | object or null | **`null × 30`** | **Insurance history is NOT in this endpoint** |
| `condition.inspection.formats` | list[str] | `['TABLE'] × 1, [] × 29` | Inspection report format marker |
| `condition.seizing.pledgeCount` | int or null | `0 × 28, null × 2` | Pledge / lien count |
| `condition.seizing.seizingCount` | int or null | `0 × 28, null × 2` | Seizure count |

### ⚠️ Phase 1 hypothesis correction

The original spec was: "real ДТП are in `condition.insurance`". **Wrong**:
`condition.insurance` is `null` for every car in our 30-car sample, including
the 29 with `recordView=True`.

What `recordView` / `resumeView` actually mean (best guess from the data):
- `recordView` = "an accident/insurance record report is available to view
  on the listing page" (i.e. the dealer has a history report).
- `resumeView` = "the report is summarized / previewable".

That makes them **flags of report availability**, not accident history. A car
with `recordView=True` could be "no accidents, report available" or
"3 prior accidents, report available" — we can't tell without scraping the
listing page or finding a different API.

### Where real accident data might live

Not visible in the API response, but mentioned in the dealer narrative
(`contents.text`):
- `'단순교환 전혀 없는 완전 무사고'` — "no accidents, only minor part swaps"
- `'사고유무 : 단순교환 전혀 없는'` — repeated in many listings
- `'무조건... 전손 차량 등의 이유'` — mentions total loss / flood

**Conclusion for Phase 1**: the data the spec describes (`insurance_accident_my`,
`insurance_accident_other`, `insurance_total_loss`, `insurance_flood`,
`insurance_theft`, `owner_changes`) is **not available** from the JSON API
endpoint we have. The options are:
1. **Drop the schema** (don't add columns we can't fill) — recommend.
2. **Scrape the listing page** (encar.com/dc/dc_carsearchview.do) for the
   detail tabs that show insurance history. Out of scope for this round.
3. **Parse `contents.text` for keywords** like "무사고" / "전손" / "침수"
   (very noisy, not recommended as a primary source).

Recommended action: replace `accident_record` (0/1) with
`accident_report_available` (bool, from `recordView` honestly renamed),
and drop the planned `insurance_*` columns. Document the limitation in
`encar-open-questions.md` so future work knows to scrape the listing page.

## `manage.*` — listing metadata (9 keys)

| Path | Type | Sample value(s) | Notes |
|---|---|---|---|
| `manage.dummy` | bool | `True × 30` | (Probably a stub flag — every car in sample) |
| `manage.dummyVehicleId` | int | matches `vehicleId` | |
| `manage.reRegistered` | bool | `False × 30` | Re-registration flag |
| `manage.webReserved` | bool | `True × 30` | Reserved via web? |
| `manage.registDateTime` | ISO datetime | e.g. `'2026-05-22T10:52:31'` | **First time this listing appeared on Encar** |
| `manage.firstAdvertisedDateTime` | ISO datetime | e.g. `'2026-05-22T17:47:16'` | First advertised |
| `manage.modifyDateTime` | ISO datetime | e.g. `'2026-05-22T21:47:03'` | **Last update — use for Tier 2 detail skip logic** |
| `manage.subscribeCount` | int | e.g. `6` | Subscription count |
| `manage.viewCount` | int | e.g. `748` | Total page views |

## `partnership.*` — dealer/brand relationships (7 keys)

| Path | Type | Sample value(s) | Notes |
|---|---|---|---|
| `partnership.isPartneredVehicle` | bool | `True × 30` | Encar-certified dealer |
| `partnership.brand` | object or null | `None × 30` | OEM partnership |
| `partnership.certifiedBrand` | object or null | `None × 30` | |
| `partnership.lease` | object or null | `None × 30` | Leasing info |
| `partnership.rent` | object or null | `None × 30` | Rental info |
| `partnership.testdrive.active` | bool | `False × 30` | Test drive available? |
| `partnership.dealer` | object | Always present | **Rich dealer info** — see below |
| `partnership.dealer.userId` | str | e.g. `'song1003'` | |
| `partnership.dealer.name` | str | e.g. `'송종배'` | Dealer contact name |
| `partnership.dealer.firm.code` | str | e.g. `'4441'` | Firm code |
| `partnership.dealer.firm.name` | str | e.g. `'무결점카모터스'` | Firm name |
| `partnership.dealer.firm.diag2Partnered` | bool | `True/False` | |
| `partnership.dealer.firm.diagnosisCenters[]` | list[object] | 1–2 entries | Inspection center info |

## `contact.*` — listing contact (7 keys)

| Path | Type | Sample value(s) | Notes |
|---|---|---|---|
| `contact.userId` | str | e.g. `'song1003'` | Dealer login id |
| `contact.userType` | str | `'DEALER' × 30` | DEALER vs private seller |
| `contact.no` | str | e.g. `'05062180788'` | Phone (often 0504 virtual number) |
| `contact.address` | str | e.g. `'경기 수원시 권선구 권선로 308-5'` | |
| `contact.contactType` | str | `'MOBILE' × 30` | |
| `contact.isVerifyOwner` | bool | `False × 30` | Ownership verified? |
| `contact.isOwnerPartner` | bool | `True × 30` | Partner status |

## `options.*` — option codes (4 keys)

```text
options.type:     'CAR' × 30
options.standard: list[str] — 51 codes in this sample, e.g. ['001', '002', '004', '005', ..., '097']
options.etc:      list[str] — always [] in sample
options.choice:   list[str] — always [] in sample
options.tuning:   list[str] — 0–3 entries (added after Phase 0 read)
```

**Open question (carried from `encar-open-questions.md` item 12)**: the codes
have no public dictionary. Need a ground-truth scrape or a hand-built table.

## `photos[]` — image metadata (5 keys per photo)

```text
photos[].code:          str — e.g. '042'
photos[].path:          str — e.g. '/carpicture06/pic4206/42063010_042.jpg'  (relative, prepend https://img.encar.com)
photos[].type:          str — 'OPTION' / 'OUTER' / 'INNER' / 'THUMBNAIL' / 'DIAG2'  (5 types seen in sample)
photos[].updateDateTime: ISO datetime
photos[].desc:          str or null
```

Type counts in 30 cars:

```text
OPTION:    347
OUTER:     207
INNER:     150
THUMBNAIL:  90
DIAG2:       3   (Encar inspection photos)
```

Note: no `uploadOrder` key (user's spec mentioned it — does not exist).
The ordering is implicit in array order.

## `vin` / `vehicleNo` / `vehicleId` (top level)

| Path | Type | Sample | Notes |
|---|---|---|---|
| `vehicleId` | int | unique per car | **Canonical Encar ID** — use for dedup |
| `vin` | str or null | `None × 7`, otherwise unique VIN | Some cars have masked/null VIN (private sellers) |
| `vehicleNo` | str | unique plate per car | Korean plate format (e.g. `'143도6369'`) |
| `vehicleType` | str | `'CAR' × 30` | Top-level + `spec.type` + `category.type` — pick `vehicleType` (top) |

## What's NOT in the response

- `category.driveType` — always null. Drive type is **not exposed** in this endpoint.
- `category.transmission` — always null. Transmission type is in `spec.transmissionName` only.
- `category.fuel` — always null. Fuel is in `spec.fuelName` / `spec.fuelCd`.
- `category.origin` — always null. Import type is in `category.importType` instead.
- `category.vehicleType` — always null. Use top-level `vehicleType`.

These fields are listed in the spec but the Encar API does not return them.
They should not be added to the CarData schema.

## Dedup key finding (Phase 5)

The 30-car sample contains what looks like a duplicate pair
(`41811360` and `41814518` in the JSON keys), but inspecting the bodies shows
they have **the same `vehicleId = 41811360`** and identical VIN/plate/mileage/
price/everything. So Encar emits the same car under different "search result"
ids.

**Phase 5 dedup key is `vehicleId`** (the int top-level field), not the JSON
key. The user's proposed VIN/photo Jaccard logic should be a fallback for
cases where `vehicleId` matches aren't enough (e.g. cross-listing dealer
relisting a sold car under a new id).

## Real example of a unique trim (for HP join)

The `category.gradeName` field is the cleanest signal for engine matching.
In our sample, 10 distinct trims — these are the codes we need to match
against the HP catalog (`catalog_test.xlsx`):

```text
'xDrive 30d M 스포츠' × 8     → engine: '30d' (3.0L diesel)
'xDrive 40i M 스포츠' × 7     → engine: '40i' (3.0L petrol)
'xDrive 30d xLine' × 4        → engine: '30d'
'xDrive 50e M 스포츠' × 1     → engine: '50e' (3.0L PHEV)
'xDrive 40i xLine' × 1        → engine: '40i'
'M50i' × 1                    → engine: 'M50i' (4.4L V8)
+ 4 more × 1
```

The `gradeEnglishName` is the same data in English. The `engine_code` for
the HP join should be extracted from the **first token after the drive prefix**
(e.g. `xDrive 30d ...` → `30d`, `M50i` → `M50i`).
