"""Integration tests against the live Parks Canada GoingToCamp API.

These tests make real HTTP requests to reservation.pc.gc.ca.
They verify that the API contract hasn't changed and our client
parses responses correctly.

Run with:  uv run pytest tests/test_live_api.py -v
Skip with: uv run pytest -m 'not live'
"""

from datetime import date, timedelta

import httpx
import pytest

from parks_monitor.client import DailyAvailability, GoingToCampClient

BASE_URL = "https://reservation.pc.gc.ca"

# Known backcountry location IDs
BANFF_BC = -2147483638
JASPER_BC = -2147483592
WATERTON_BC = -2147483528

# Known resource IDs for spot-checks
ATHABASCA_PASS = -2147483256   # 61 - Athabasca Pass (Jasper)
EGYPT_LAKE_E13 = -2147483054   # Egypt Lake - E13 (Banff)
SWITCHBACK = -2147483289       # 43 - Switchback (Jasper, Tonquin)
ALDERSON_LAKE = -2147471815    # Alderson Lake (Waterton)

pytestmark = pytest.mark.live


@pytest.fixture
async def client():
    async with httpx.AsyncClient(timeout=30) as http:
        yield GoingToCampClient(http, BASE_URL)


# ── User-Agent enforcement ──────────────────────────────────────────────────


async def test_rejects_missing_user_agent():
    """API returns 403 without a User-Agent header."""
    async with httpx.AsyncClient(timeout=15) as http:
        resp = await http.get(
            f"{BASE_URL}/api/resourcelocation",
            headers={"User-Agent": ""},
        )
    assert resp.status_code == 403


async def test_accepts_browser_user_agent(client: GoingToCampClient):
    """API returns 200 with a realistic User-Agent (our client sets one)."""
    locations = await client.list_backcountry_locations()
    assert len(locations) > 0


# ── /api/resourcelocation ──────────────────────────────────────────────────


async def test_resourcelocation_returns_backcountry(client: GoingToCampClient):
    """All three GDT backcountry locations are present."""
    locations = await client.list_backcountry_locations()
    ids = {loc.resource_location_id for loc in locations}
    assert BANFF_BC in ids
    assert JASPER_BC in ids
    assert WATERTON_BC in ids


async def test_resourcelocation_filters_non_backcountry(client: GoingToCampClient):
    """Only backcountry locations are returned."""
    locations = await client.list_backcountry_locations()
    for loc in locations:
        assert "backcountry" in loc.name.lower()


async def test_resourcelocation_has_root_map_ids(client: GoingToCampClient):
    """Each location has a non-zero rootMapId for map hierarchy."""
    locations = await client.list_backcountry_locations()
    for loc in locations:
        assert loc.root_map_id != 0


# ── /api/maps ───────────────────────────────────────────────────────────────


async def test_maps_jasper_has_named_zones(client: GoingToCampClient):
    """Jasper has hierarchical zones (Tonquin, Athabasca Pass, etc.)."""
    zones = await client.list_zones(JASPER_BC)
    names = {z.name for z in zones}
    assert "Athabasca Pass" in names
    assert "Tonquin Valley" in names
    assert "North Boundary and Celestine Lake" in names


async def test_maps_jasper_zones_have_resources(client: GoingToCampClient):
    """Each Jasper zone has at least one resource ID."""
    zones = await client.list_zones(JASPER_BC)
    for zone in zones:
        assert len(zone.resource_ids) > 0, f"Zone '{zone.name}' has no resources"


async def test_maps_banff_has_zones(client: GoingToCampClient):
    """Banff/Kootenay/Yoho returns map zones with resources."""
    zones = await client.list_zones(BANFF_BC)
    assert len(zones) > 0
    total_resources = sum(len(z.resource_ids) for z in zones)
    assert total_resources > 50  # Banff has 200+ resources across categories


async def test_maps_waterton_has_resources(client: GoingToCampClient):
    """Waterton has a small number of backcountry resources."""
    zones = await client.list_zones(WATERTON_BC)
    total_resources = sum(len(z.resource_ids) for z in zones)
    assert total_resources >= 9  # 9 known backcountry campsites


async def test_maps_known_resource_exists_in_zones(client: GoingToCampClient):
    """A known Jasper resource ID appears in the zone data."""
    zones = await client.list_zones(JASPER_BC)
    all_rids = set()
    for zone in zones:
        all_rids.update(zone.resource_ids)
    assert ATHABASCA_PASS in all_rids


# ── /api/availability/resourcedailyavailability ─────────────────────────────


async def test_daily_availability_returns_correct_day_count(client: GoingToCampClient):
    """Response array length matches the number of days requested.

    The date range is INCLUSIVE on both ends: startDate=Jul 15, endDate=Jul 20
    returns 6 entries (15, 16, 17, 18, 19, 20).
    """
    start = date(2026, 7, 15)
    end = date(2026, 7, 20)
    days = await client.check_daily_availability(ATHABASCA_PASS, start, end)
    assert len(days) == (end - start).days + 1


async def test_daily_availability_single_day(client: GoingToCampClient):
    """A same-day range (start==end) returns exactly 1 result."""
    d = date(2026, 7, 15)
    days = await client.check_daily_availability(ATHABASCA_PASS, d, d)
    assert len(days) == 1


async def test_daily_availability_response_shape(client: GoingToCampClient):
    """Each day has the expected fields with valid values."""
    start = date(2026, 7, 15)
    end = date(2026, 7, 20)
    days = await client.check_daily_availability(ATHABASCA_PASS, start, end)
    for d in days:
        assert isinstance(d, DailyAvailability)
        assert d.availability in (0, 1)
        assert isinstance(d.processed_availability, int)


async def test_daily_availability_past_dates_unavailable(client: GoingToCampClient):
    """Past dates should always be unavailable (availability=0)."""
    today = date.today()
    past_start = today - timedelta(days=30)
    past_end = today - timedelta(days=25)
    days = await client.check_daily_availability(EGYPT_LAKE_E13, past_start, past_end)
    expected_days = (past_end - past_start).days + 1  # inclusive
    assert len(days) == expected_days
    assert all(d.availability == 0 for d in days)


async def test_daily_availability_far_future_unavailable(client: GoingToCampClient):
    """Dates beyond the booking window should be unavailable."""
    far_start = date(2028, 7, 1)
    far_end = date(2028, 7, 5)
    days = await client.check_daily_availability(EGYPT_LAKE_E13, far_start, far_end)
    expected_days = (far_end - far_start).days + 1  # inclusive
    assert len(days) == expected_days
    assert all(d.availability == 0 for d in days)


async def test_daily_availability_across_parks(client: GoingToCampClient):
    """Availability works for resources in different parks."""
    start = date(2026, 7, 15)
    end = date(2026, 7, 20)
    expected = (end - start).days + 1  # inclusive

    # Jasper
    jasper_days = await client.check_daily_availability(ATHABASCA_PASS, start, end)
    assert len(jasper_days) == expected

    # Banff
    banff_days = await client.check_daily_availability(EGYPT_LAKE_E13, start, end)
    assert len(banff_days) == expected

    # Waterton
    waterton_days = await client.check_daily_availability(ALDERSON_LAKE, start, end)
    assert len(waterton_days) == expected


async def test_daily_availability_party_size_passed(client: GoingToCampClient):
    """Different party sizes can return different availability."""
    start = date(2026, 7, 15)
    end = date(2026, 7, 20)
    expected = (end - start).days + 1
    # Just verify both calls succeed — party size effect depends on quota
    days_1 = await client.check_daily_availability(ATHABASCA_PASS, start, end, party_size=1)
    days_4 = await client.check_daily_availability(ATHABASCA_PASS, start, end, party_size=4)
    assert len(days_1) == len(days_4) == expected


async def test_daily_availability_long_range(client: GoingToCampClient):
    """A full-summer date range returns the correct number of days."""
    start = date(2026, 7, 1)
    end = date(2026, 8, 31)
    days = await client.check_daily_availability(EGYPT_LAKE_E13, start, end)
    expected = (end - start).days + 1  # inclusive on both ends
    assert len(days) == expected


# ── Consistency checks ──────────────────────────────────────────────────────


async def test_availability_is_deterministic(client: GoingToCampClient):
    """Two consecutive calls for the same resource/dates return the same result."""
    start = date(2026, 7, 15)
    end = date(2026, 7, 20)
    days_a = await client.check_daily_availability(SWITCHBACK, start, end)
    days_b = await client.check_daily_availability(SWITCHBACK, start, end)
    assert [d.availability for d in days_a] == [d.availability for d in days_b]


async def test_processed_availability_is_consistent(client: GoingToCampClient):
    """processedAvailability is always 5 or 3 for backcountry resources."""
    start = date(2026, 7, 15)
    end = date(2026, 7, 20)
    for rid in [ATHABASCA_PASS, EGYPT_LAKE_E13, SWITCHBACK, ALDERSON_LAKE]:
        days = await client.check_daily_availability(rid, start, end)
        for d in days:
            assert d.processed_availability in (3, 5), (
                f"Unexpected processedAvailability={d.processed_availability} "
                f"for resource {rid}"
            )


# ── Error handling ──────────────────────────────────────────────────────────


async def test_invalid_resource_id_returns_400(client: GoingToCampClient):
    """A non-existent resource ID returns 400 Bad Request."""
    import httpx as _httpx

    start = date(2026, 7, 15)
    end = date(2026, 7, 20)
    with pytest.raises(_httpx.HTTPStatusError) as exc_info:
        await client.check_daily_availability(999999, start, end)
    assert exc_info.value.response.status_code == 400


async def test_map_availability_broken_for_backcountry():
    """The /api/availability/map endpoint returns 5 for all backcountry resources.

    This test documents the known bug: map-level availability is always 5
    (unavailable) for backcountry, regardless of actual booking state.
    If this test starts failing, the map endpoint may have been fixed —
    which would let us batch-check availability instead of per-resource calls.
    """
    async with httpx.AsyncClient(timeout=15) as http:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        }
        resp = await http.get(
            f"{BASE_URL}/api/availability/map",
            params={
                "mapId": -2147483422,  # Jasper root map
                "resourceLocationId": JASPER_BC,
                "bookingCategoryId": 0,
                "startDate": "2026-07-15",
                "endDate": "2026-07-20",
                "isReserving": "true",
                "partySize": 1,
                "numEquipment": 1,
                "equipmentCategoryId": -32768,
            },
            headers=headers,
        )
    assert resp.status_code == 200
    data = resp.json()
    # All backcountry resources show availability=5 (broken)
    for rid_str, avails in data["resourceAvailabilities"].items():
        for a in avails:
            assert a["availability"] == 5, (
                f"Map endpoint returned availability={a['availability']} for "
                f"resource {rid_str} — the bug may be fixed! Consider switching "
                f"to batch availability checks."
            )
