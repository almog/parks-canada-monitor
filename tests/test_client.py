import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from parks_monitor.client import DailyAvailability, GoingToCampClient


def test_is_bookable_basic():
    assert DailyAvailability(availability=1, processed_availability=5).is_bookable
    assert not DailyAvailability(availability=0, processed_availability=5).is_bookable


def test_is_bookable_processed_3_overrides():
    """processed_availability=3 means not open for this category."""
    assert not DailyAvailability(availability=1, processed_availability=3).is_bookable
    assert not DailyAvailability(availability=0, processed_availability=3).is_bookable

FIXTURES = Path(__file__).parent / "fixtures"
BASE_URL = "https://reservation.pc.gc.ca"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def client():
    http = httpx.AsyncClient()
    return GoingToCampClient(http, BASE_URL)


# --- list_backcountry_locations ---


async def test_list_backcountry_locations(client: GoingToCampClient):
    with respx.mock:
        respx.get(f"{BASE_URL}/api/resourcelocation").mock(
            return_value=httpx.Response(200, json=_load("resource_locations.json"))
        )
        locations = await client.list_backcountry_locations()

    names = [loc.name for loc in locations]
    assert "Banff, Kootenay and Yoho - Backcountry" in names
    assert "Jasper - Backcountry" in names
    assert "Waterton Lakes - Backcountry" in names
    # Non-backcountry entries should be filtered out
    assert all("backcountry" in n.lower() for n in names)


async def test_list_backcountry_locations_ids(client: GoingToCampClient):
    with respx.mock:
        respx.get(f"{BASE_URL}/api/resourcelocation").mock(
            return_value=httpx.Response(200, json=_load("resource_locations.json"))
        )
        locations = await client.list_backcountry_locations()

    by_name = {loc.name: loc for loc in locations}
    assert by_name["Banff, Kootenay and Yoho - Backcountry"].resource_location_id == -2147483638
    assert by_name["Jasper - Backcountry"].resource_location_id == -2147483592
    assert by_name["Waterton Lakes - Backcountry"].resource_location_id == -2147483528


# --- list_zones ---


async def test_list_zones_flat(client: GoingToCampClient):
    """Banff backcountry maps have no hierarchy — each map is a zone."""
    with respx.mock:
        respx.get(f"{BASE_URL}/api/maps").mock(
            return_value=httpx.Response(200, json=_load("maps_banff_bc.json"))
        )
        zones = await client.list_zones(-2147483638)

    assert len(zones) > 0
    # Each zone should have resource IDs
    for z in zones:
        assert len(z.resource_ids) > 0
    # Spot-check a known zone name from the fixture
    names = [z.name for z in zones]
    assert "Banff Random Camping" in names


async def test_list_zones_hierarchical(client: GoingToCampClient):
    """Jasper backcountry maps have a root map with mapLinks to child maps."""
    with respx.mock:
        respx.get(f"{BASE_URL}/api/maps").mock(
            return_value=httpx.Response(200, json=_load("maps_jasper_bc.json"))
        )
        zones = await client.list_zones(-2147483592)

    assert len(zones) > 0
    names = [z.name for z in zones]
    assert "Athabasca Pass" in names


# --- check_daily_availability ---


async def test_daily_availability_bookable(client: GoingToCampClient):
    with respx.mock:
        respx.get(f"{BASE_URL}/api/availability/resourcedailyavailability").mock(
            return_value=httpx.Response(
                200, json=_load("daily_availability_jasper_available.json")
            )
        )
        days = await client.check_daily_availability(
            resource_id=-2147483256,
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 10),
        )

    assert len(days) == 10
    # First entry is bookable (availability=1), second is booked (availability=0)
    assert days[0].availability == 1
    assert days[1].availability == 0
    # Most are bookable
    bookable = [d for d in days if d.availability == 1]
    assert len(bookable) >= 8


async def test_daily_availability_all_booked(client: GoingToCampClient):
    with respx.mock:
        respx.get(f"{BASE_URL}/api/availability/resourcedailyavailability").mock(
            return_value=httpx.Response(
                200, json=_load("daily_availability_banff_booked.json")
            )
        )
        days = await client.check_daily_availability(
            resource_id=-2147483137,
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 10),
        )

    assert len(days) == 10
    assert all(d.availability == 0 for d in days)


async def test_daily_availability_party_size(client: GoingToCampClient):
    """Verify party_size is passed through to the API."""
    with respx.mock:
        route = respx.get(f"{BASE_URL}/api/availability/resourcedailyavailability").mock(
            return_value=httpx.Response(
                200, json=_load("daily_availability_banff_booked.json")
            )
        )
        await client.check_daily_availability(
            resource_id=-2147483137,
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 10),
            party_size=4,
        )

    assert route.called
    request = route.calls[0].request
    assert "partySize=4" in str(request.url)
