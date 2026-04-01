from __future__ import annotations

import random
from datetime import date

import httpx
from pydantic import BaseModel


_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
]


class BackcountryLocation(BaseModel):
    resource_location_id: int
    name: str
    root_map_id: int


class MapZone(BaseModel):
    child_map_id: int
    name: str
    resource_ids: list[int]


class DailyAvailability(BaseModel):
    availability: int  # 1 = bookable, 0 = booked
    processed_availability: int
    remaining_quota: int | None = None


class GoingToCampClient:
    def __init__(self, http_client: httpx.AsyncClient, base_url: str):
        self._client = http_client
        self._base_url = base_url.rstrip("/")

    async def _get(self, path: str, params: dict | None = None) -> list | dict:
        url = f"{self._base_url}{path}"
        headers = {"User-Agent": random.choice(_USER_AGENTS)}
        resp = await self._client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def list_backcountry_locations(self) -> list[BackcountryLocation]:
        """GET /api/resourcelocation — returns only backcountry entries."""
        data = await self._get("/api/resourcelocation")
        results = []
        for item in data:
            en_name = ""
            for lv in item.get("localizedValues", []):
                if lv.get("cultureName") == "en-CA":
                    en_name = lv.get("shortName", "")
                    break
            if "backcountry" not in en_name.lower():
                continue
            results.append(
                BackcountryLocation(
                    resource_location_id=item["resourceLocationId"],
                    name=en_name,
                    root_map_id=item["rootMapId"],
                )
            )
        return results

    async def list_zones(self, resource_location_id: int) -> list[MapZone]:
        """GET /api/maps?resourceLocationId=X — extract zones + resource IDs."""
        data = await self._get(
            "/api/maps", params={"resourceLocationId": resource_location_id}
        )
        # Build a lookup: mapId -> list of resource IDs
        map_resources: dict[int, list[int]] = {}
        root_map = None
        for m in data:
            mid = m["mapId"]
            map_resources[mid] = [
                r["resourceId"] for r in m.get("mapResources", [])
            ]
            if m.get("mapLinks"):
                root_map = m

        if root_map is None:
            # No hierarchy — treat each map as a zone
            zones = []
            for m in data:
                name = ""
                for lv in m.get("localizedValues", []):
                    if lv.get("cultureName") == "en-CA":
                        name = lv.get("title", "") or lv.get("name", "")
                        break
                rids = map_resources.get(m["mapId"], [])
                if rids:
                    zones.append(
                        MapZone(
                            child_map_id=m["mapId"], name=name, resource_ids=rids
                        )
                    )
            return zones

        # Has hierarchy — zones are the mapLinks on the root map
        zones = []
        for link in root_map.get("mapLinks", []):
            name = ""
            for loc in link.get("localizations", []):
                if loc.get("cultureName") == "en-CA":
                    name = loc.get("title", "")
                    break
            child_id = link["childMapId"]
            rids = map_resources.get(child_id, [])
            zones.append(
                MapZone(child_map_id=child_id, name=name, resource_ids=rids)
            )
        return zones

    async def check_daily_availability(
        self,
        resource_id: int,
        start_date: date,
        end_date: date,
        party_size: int = 1,
    ) -> list[DailyAvailability]:
        """GET /api/availability/resourcedailyavailability — real backcountry availability."""
        params = {
            "resourceId": resource_id,
            "bookingCategoryId": 0,
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "isReserving": "true",
            "partySize": party_size,
            "numEquipment": 1,
            "equipmentCategoryId": -32768,
        }
        data = await self._get(
            "/api/availability/resourcedailyavailability", params=params
        )
        return [
            DailyAvailability(
                availability=d.get("availability", 0),
                processed_availability=d.get("processedAvailability", 0),
                remaining_quota=d.get("remainingQuota"),
            )
            for d in data
        ]
