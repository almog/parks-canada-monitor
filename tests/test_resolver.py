from parks_monitor.resolver import (
    campsite_names,
    classify_type,
    id_to_name,
    is_gdt_site,
    locations,
    name_to_id,
    reservation_url,
    resolve_id,
    resolve_ids,
    resolve_name,
)


def test_id_to_name_loads():
    mapping = id_to_name()
    assert len(mapping) == 415
    assert isinstance(mapping[-2147483054], str)


def test_resolve_name_known():
    assert resolve_name(-2147483054) == "Egypt Lake - E13"


def test_resolve_name_unknown():
    assert resolve_name(999999) == "999999"


def test_resolve_ids_exact():
    ids = resolve_ids("61 - Athabasca Pass")
    assert -2147483256 in ids


def test_resolve_ids_substring():
    ids = resolve_ids("Egypt Lake")
    assert len(ids) >= 1
    assert -2147483054 in ids


def test_resolve_ids_case_insensitive():
    ids = resolve_ids("egypt lake")
    assert -2147483054 in ids


def test_resolve_ids_no_match():
    assert resolve_ids("Nonexistent XYZ 12345") == []


def test_resolve_id_exact_match():
    assert resolve_id("Egypt Lake - E13") == -2147483054
    assert resolve_id("egypt lake - e13") == -2147483054  # case-insensitive


def test_resolve_id_substring_returns_none():
    """A substring (not full name) must return None — no fuzzy expansion."""
    assert resolve_id("Egypt Lake") is None


def test_resolve_id_unknown():
    assert resolve_id("Nonexistent XYZ") is None


def test_locations_returns_three():
    locs = locations()
    assert len(locs) == 3
    names = {loc["display_name"] for loc in locs}
    assert "Jasper - Backcountry" in names
    assert "Waterton Lakes - Backcountry" in names


def test_name_to_id_inverse():
    fwd = id_to_name()
    rev = name_to_id()
    # Every name in reverse should map back to the same ID
    for rid, name in fwd.items():
        assert rev[name.lower()] == rid


def test_classify_type_designated():
    assert classify_type("Egypt Lake - E13") == "designated"
    assert classify_type("61 - Athabasca Pass") == "designated"


def test_classify_type_random():
    assert classify_type("Siffleur Random") == "random"
    assert classify_type("North Boundary Random") == "random"


def test_classify_type_trailhead():
    assert classify_type("Banff Trailhead") == "trailhead"


def test_classify_type_horse():
    assert classify_type("18 - Wolverine South - Horse") == "horse"
    # Horseshoe should NOT be classified as horse
    assert classify_type("65 - Horseshoe") == "designated"


def test_classify_type_hut():
    assert classify_type("Bow Hut Random") == "hut"


def test_is_gdt_site():
    assert is_gdt_site(-2147483054)   # Egypt Lake - E13
    assert is_gdt_site(-2147483256)   # 61 - Athabasca Pass
    assert is_gdt_site(-2147471815)   # Alderson Lake (Waterton)
    assert not is_gdt_site(-2147483003)  # Baker Creek Grazing (not GDT)


def test_campsite_names_all():
    names = campsite_names()
    assert len(names) == 415
    assert "Egypt Lake - E13" in names


def test_campsite_names_gdt_only():
    names = campsite_names(gdt_only=True)
    assert "Egypt Lake - E13" in names
    assert "Baker Creek Grazing" not in names


def test_campsite_names_type_filter():
    designated = campsite_names(site_type="designated")
    for name in designated:
        assert classify_type(name) == "designated"


def test_reservation_url_banff():
    url = reservation_url(-2147483054)  # Egypt Lake - E13
    assert "resourceLocationId=-2147483638" in url


def test_reservation_url_jasper():
    url = reservation_url(-2147483256)  # 61 - Athabasca Pass
    assert "resourceLocationId=-2147483592" in url


def test_reservation_url_waterton():
    url = reservation_url(-2147471815)  # Alderson Lake
    assert "resourceLocationId=-2147483528" in url


def test_reservation_url_unknown_resource():
    url = reservation_url(999999)
    assert url == "https://reservation.pc.gc.ca/create-booking/results"
