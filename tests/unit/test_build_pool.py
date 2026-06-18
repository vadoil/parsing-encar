"""Unit tests for encar_parser.build_pool.

These tests cover the pure-string parts of pool generation:
- Brand prefix stripping
- Generation-code extraction
- Token translation (The New / Hybrid / etc.)
- Candidate-list generation for both domestic and import brands

No live API calls. The probe_count function is monkey-patched in
integration tests instead.
"""
from __future__ import annotations

import pytest

from encar_parser.build_pool import (
    DOMESTIC_BRAND_KR,
    DOMESTIC_BRANDS,
    FAMILY_BASE_KR,
    IMPORT_MANUFACTURER_NAME,
    TOKEN_KR,
    _candidate_kr_models,
    _candidate_qs_for_import,
    _extract_gen_code,
    _strip_gen_code,
    _strip_tokens,
    build_q_for,
)

# --- _extract_gen_code / _strip_gen_code ----------------------------------


def test_extract_gen_code_simple():
    assert _extract_gen_code("Sonata (DN8)") == "DN8"


def test_extract_gen_code_no_code():
    assert _extract_gen_code("Sonata") is None


def test_extract_gen_code_with_space_inside():
    # Edge case: gen code may contain a space (rare, but possible)
    assert _extract_gen_code("Q5 (FY)") == "FY"


def test_strip_gen_code_removes_trailing_paren():
    assert _strip_gen_code("Sonata (DN8)") == "Sonata"


def test_strip_gen_code_preserves_inner_parens():
    # We only strip the trailing generation code, not arbitrary inner parens.
    assert _strip_gen_code("Cayenne (PO536)") == "Cayenne"


def test_strip_gen_code_no_change_when_no_code():
    assert _strip_gen_code("Sonata") == "Sonata"


# --- _strip_tokens ---------------------------------------------------------


def test_strip_tokens_hybrid():
    tokens, base = _strip_tokens("Avante Hybrid (CN7)")
    assert base == "Avante"
    assert "Hybrid" in tokens


def test_strip_tokens_multiple_tokens():
    tokens, base = _strip_tokens("The New Sonata Hybrid (DN8)")
    assert base == "Sonata"
    assert set(tokens) == {"The New", "Hybrid"}


def test_strip_tokens_no_gen_code():
    tokens, base = _strip_tokens("G80")
    assert base == "G80"
    assert tokens == []


def test_strip_tokens_longest_token_first():
    # "All New" must match before "New" (greedy by length)
    tokens, _ = _strip_tokens("All New Tucson (NX4)")
    assert "All New" in tokens
    assert "New" not in tokens


# --- _candidate_kr_models --------------------------------------------------


def test_candidate_kr_models_simple():
    candidates = _candidate_kr_models("Avante (CN7)", "아반떼")
    # The most common form for Hyundai is "<base> (<gen>)" — must be first.
    assert candidates[0] == "아반떼 (CN7)"


def test_candidate_kr_models_with_token():
    candidates = _candidate_kr_models("Avante Hybrid (CN7)", "아반떼")
    # Token orderings: "아반떼 하이브리드 (CN7)" and "하이브리드 아반떼 (CN7)"
    assert "아반떼 하이브리드 (CN7)" in candidates
    assert "하이브리드 아반떼 (CN7)" in candidates


def test_candidate_kr_models_no_gen_code():
    candidates = _candidate_kr_models("G80", "G80")
    assert candidates == ["G80"]


def test_candidate_kr_models_dedup():
    candidates = _candidate_kr_models("Avante (CN7)", "아반떼")
    # No duplicates — important because some permutations collapse.
    assert len(candidates) == len(set(candidates))


def test_candidate_kr_models_token_permutations_covered():
    # With two tokens ["The New", "Hybrid"] we should see 2! orderings —
    # both as <tokens><base>(<gen>) and as <base><tokens>(<gen>).
    candidates = _candidate_kr_models("The New Sonata Hybrid (DN8)", "쏘나타")
    assert "쏘나타 더 뉴 하이브리드 (DN8)" in candidates  # base then tokens
    assert "더 뉴 하이브리드 쏘나타 (DN8)" in candidates  # tokens then base
    assert "쏘나타 하이브리드 더 뉴 (DN8)" in candidates  # base then swapped tokens


# --- build_q_for -----------------------------------------------------------


def test_build_q_for_bare_bmw_x5():
    q = build_q_for("N", "BMW", "X5", "X5 (G05)")
    # Canonical 4-level shape with Model wrapper.
    assert q == (
        "(And.Hidden.N._.(C.CarType.N._.(C.Manufacturer.BMW._."
        "(C.ModelGroup.X5._.Model.X5 (G05).)))_.Year.range(201800..202699).)"
    )


def test_build_q_for_korean_manufacturer():
    q = build_q_for("N", "아우디", "A6", "A6 (C8)")
    # The Korean manufacturer name is preserved verbatim.
    assert "C.Manufacturer.아우디" in q
    assert "ModelGroup.A6" in q
    assert "Model.A6 (C8)" in q


def test_build_q_for_year_range_format():
    q = build_q_for("Y", "현대", "아반떼", "아반떼 (CN7)",
                    year_from=2018, year_to=2026)
    # year_from * 100 = 201800, year_to * 100 + 99 = 202699
    assert "Year.range(201800..202699)" in q


def test_build_q_for_different_year_window():
    q = build_q_for("N", "BMW", "X5", "X5 (G05)", year_from=2020, year_to=2024)
    assert "Year.range(202000..202499)" in q


# --- Domestic / Korean brand catalog ---------------------------------------


def test_domestic_brands_contains_expected():
    # The set should match the four real Korean OEMs.
    assert {"Hyundai", "Kia", "Genesis", "Ssangyong"} == DOMESTIC_BRANDS


def test_domestic_brand_kr_complete():
    # Every domestic brand must have a Korean translation.
    for brand in DOMESTIC_BRANDS:
        assert brand in DOMESTIC_BRAND_KR
        assert DOMESTIC_BRAND_KR[brand]  # non-empty


def test_family_base_kr_keys_match_domestic_models():
    # Spot-check: the canonical family names in FAMILY_BASE_KR should align
    # with what the catalog uses (English family_label minus tokens).
    assert "아반떼" in FAMILY_BASE_KR.values()
    assert "쏘나타" in FAMILY_BASE_KR.values()
    assert "그랜저" in FAMILY_BASE_KR.values()


def test_token_kr_has_known_translations():
    # Pin translations so we notice if anyone changes them by accident.
    assert TOKEN_KR["Hybrid"] == "하이브리드"
    assert TOKEN_KR["The New"] == "더 뉴"
    assert TOKEN_KR["All New"] == "올 뉴"
    assert TOKEN_KR["Sports"] == "스포츠"


# --- Parametrized: all domestic Hyundai family_labels from the catalog -----


@pytest.mark.parametrize(("family_label", "base_en", "expected_kr"), [
    ("Avante (CN7)", "Avante", "아반떼"),
    ("Avante Hybrid (CN7)", "Avante", "아반떼"),
    ("Grandeur (GN7)", "Grandeur", "그랜저"),
    ("Sonata (DN8)", "Sonata", "쏘나타"),
    ("Tucson (NX4)", "Tucson", "투싼"),
    ("Santa Fe (TM)", "Santa Fe", "싼타페"),
    ("Palisade (LX2)", "Palisade", "팰리세이드"),
])
def test_korean_candidates_for_real_catalog_rows(family_label, base_en, expected_kr):
    candidates = _candidate_kr_models(family_label, FAMILY_BASE_KR[base_en])
    # The bare-with-gen form must be present (it works for most Hyundai models).
    gen = _extract_gen_code(family_label)
    assert f"{expected_kr} ({gen})" in candidates


# --- _candidate_qs_for_import ---------------------------------------------


def test_import_manufacturer_name_known_brands():
    # Sanity: brands whose Encar manufacturer is Korean must be in the map.
    # (Verified against live API in 2026-06-18 session.)
    assert IMPORT_MANUFACTURER_NAME["Audi"] == "아우디"
    assert IMPORT_MANUFACTURER_NAME["Porsche"] == "포르쉐"
    assert IMPORT_MANUFACTURER_NAME["Jaguar"] == "재규어"
    assert IMPORT_MANUFACTURER_NAME["Infiniti"] == "인피니티"


def test_candidate_qs_audi_uses_korean_manufacturer():
    """Audi's Encar Manufacturer is Korean; the first candidate must reflect that."""
    cands = _candidate_qs_for_import("Audi", "A3 (8Y)", "N", 2018, 2026)
    assert cands  # non-empty
    manuf, mg, m, q = cands[0]
    assert manuf == "아우디"
    assert mg == "A3"
    assert m == "A3 (8Y)"
    # And the q itself is built from those pieces.
    assert "C.Manufacturer.아우디" in q
    assert "ModelGroup.A3" in q
    assert "Model.A3 (8Y)" in q


def test_candidate_qs_bmw_keeps_english_manufacturer():
    """BMW's Encar Manufacturer is English ('BMW'), not '비엠더블유'."""
    cands = _candidate_qs_for_import("BMW", "X5 (G05)", "N", 2018, 2026)
    assert cands
    manuf, mg, m, q = cands[0]
    assert manuf == "BMW"
    assert mg == "X5"
    assert m == "X5 (G05)"
    assert "C.Manufacturer.BMW" in q


def test_candidate_qs_strips_brand_prefix():
    """'Porsche 911 (992)' should drop the 'Porsche' prefix when picking the
    model name — Encar stores it as '911 (992)'."""
    cands = _candidate_qs_for_import("Porsche", "911 (992)", "N", 2018, 2026)
    assert cands
    manuf, mg, m, q = cands[0]
    assert manuf == "포르쉐"
    assert mg == "911"
    assert m == "911 (992)"
    assert "C.Manufacturer.포르쉐" in q


def test_candidate_qs_handles_no_gen_code():
    """Family labels without a gen code (rare) should still produce a candidate."""
    cands = _candidate_qs_for_import("BMW", "iX", "N", 2018, 2026)
    assert cands
    # Both MG and M collapse to "iX" for the no-gen-code path.
    assert any(mg == "iX" and m == "iX" for _, mg, m, _ in cands)


def test_candidate_qs_are_dedup():
    cands = _candidate_qs_for_import("Audi", "A3 (8Y)", "N", 2018, 2026)
    qs = [c[3] for c in cands]
    assert len(qs) == len(set(qs))


def test_candidate_qs_are_in_priority_order():
    """First candidate uses the most-likely-correct combination."""
    cands = _candidate_qs_for_import("Audi", "A3 (8Y)", "N", 2018, 2026)
    # First = MG=base, M=with_gen
    assert cands[0][1] == "A3"
    assert cands[0][2] == "A3 (8Y)"
    # Second = MG=with_gen, M=with_gen
    assert cands[1][1] == "A3 (8Y)"
    assert cands[1][2] == "A3 (8Y)"
    # Third = MG=base, M=base (no gen code in q)
    assert cands[2][1] == "A3"
    assert cands[2][2] == "A3"


def test_candidate_qs_fall_back_to_english_for_unknown_brands():
    """For brands not in the import-manufacturer map (e.g. 'BMW'), the
    English label is tried. The known name equals the label, so no
    duplicate fallback is generated."""
    cands = _candidate_qs_for_import("BMW", "X5 (G05)", "N", 2018, 2026)
    manufs = {c[0] for c in cands}
    assert manufs == {"BMW"}


def test_candidate_qs_year_range_applied_to_every_candidate():
    cands = _candidate_qs_for_import("Audi", "A3 (8Y)", "N", 2018, 2026)
    for _, _, _, q in cands:
        assert "Year.range(201800..202699)" in q


def test_candidate_qs_bmw_series_uses_korean():
    """BMW '3 Series (G20)' is stored as '3시리즈 (G20)' in Encar.

    The English form also exists in the catalog but the Korean form is the
    one that returns the actual count; we try both.
    """
    cands = _candidate_qs_for_import("BMW", "3 Series (G20)", "N", 2018, 2026)
    # English form first
    assert any(m == "3 Series (G20)" for _, _, m, _ in cands)
    # Korean form is also present
    assert any(m == "3시리즈 (G20)" for _, _, m, _ in cands)
    assert any(mg == "3시리즈" for _, mg, _, _ in cands)


def test_candidate_qs_bmw_active_tourer_prefers_specific_match():
    """'BMW 2 Series Active Tourer (F45)' must match the Active Tourer entry,
    not the bare '2 Series' key."""
    cands = _candidate_qs_for_import("BMW", "2 Series Active Tourer (F45)", "N", 2018, 2026)
    # Should include "2시리즈 액티브 투어러 (F45)"
    assert any("2시리즈 액티브 투어러" in m for _, _, m, _ in cands)
    # And NOT include "2시리즈 (F45)" (which would happen if the bare
    # "2 Series" key matched first).
    assert not any(m == "2시리즈 (F45)" for _, _, m, _ in cands)


def test_candidate_qs_bmw_x5_stays_english():
    """BMW X5 (G05) — Encar uses the English form (X5), not X5시리즈.

    The X-series is letters+numbers with no 'Series' suffix, so the Korean
    translation rule should NOT fire.
    """
    cands = _candidate_qs_for_import("BMW", "X5 (G05)", "N", 2018, 2026)
    # All candidates must use English model name (no 'X5시리즈').
    assert all("X5시리즈" not in q for _, _, _, q in cands)
    assert any(m == "X5 (G05)" for _, _, m, _ in cands)
