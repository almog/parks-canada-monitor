from parks_monitor.resolver import (
    id_to_name,
    name_to_id,
    resolve_ids,
    resolve_name,
    locations,
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
