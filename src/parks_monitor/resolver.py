"""Resolve resource IDs to/from human-readable campsite names.

Uses the bundled campsites.yaml mapping (fetched from the Parks Canada API).
"""

from __future__ import annotations

from importlib import resources as importlib_resources
from functools import lru_cache
from typing import Literal

import yaml


SiteType = Literal["designated", "random", "hut", "trailhead", "horse", "access", "out_of_park", "private", "other"]


def classify_type(name: str) -> SiteType:
    """Classify a campsite by its name."""
    n = name.lower()
    if "trailhead" in n:
        return "trailhead"
    if "grazing" in n or "corral" in n or n.split()[-1] == "horse":
        return "horse"
    if "access point" in n:
        return "access"
    if "(out of park)" in n or "out of park" in n:
        return "out_of_park"
    if n.startswith("private"):
        return "private"
    if "hut" in n and "random" in n:
        return "hut"
    if n.endswith("random") or "random" in n:
        return "random"
    if "day use" in n:
        return "other"
    return "designated"


# Resource IDs along the main GDT corridor and key alternates.
# Sourced from GDT Association route description + Parks Canada campsite data.
# Covers: Waterton → Banff/Kootenay/Yoho → Jasper.
_GDT_SITE_IDS: frozenset[int] = frozenset([
    # ── Waterton Lakes ──────────────────────────────────────────────────────
    -2147471815,  # Alderson Lake
    -2147471812,  # Bertha Lake
    -2147471814,  # Bertha Bay
    -2147471813,  # Boundary Bay
    -2147471811,  # Crandell Lake
    -2147471810,  # Goat Lake
    -2147471809,  # Lone Lake
    -2147471808,  # Snowshoe
    -2147471807,  # Twin Lakes

    # ── Banff – Spray/Bryant Creek corridor ─────────────────────────────────
    -2147483112,  # Bryant Creek Shelter
    -2147483072,  # Bryant Random
    -2147483051,  # Massive Random
    -2147483104,  # Marvel Lake - Br13
    -2147482978,  # McBride's Camp - Br14
    -2147483046,  # Allenby Junction - Br17

    # ── Banff – Egypt Lake / Healy / Sunshine area ──────────────────────────
    -2147483054,  # Egypt Lake - E13
    -2147483071,  # Egypt Lake Shelter
    -2147483001,  # Healy Creek - E5
    -2147483067,  # Pharaoh Creek - Re16
    -2147483110,  # Shadow Lake - Re14
    -2147483142,  # Lost Horse Creek - Re6
    -2147483014,  # Ball Pass Junction - Re21
    -2147482988,  # Sunshine/Egypt Random

    # ── Kootenay – Rockwall/Floe Lake corridor ──────────────────────────────
    -2147483103,  # Floe Lake
    -2147482997,  # Helmet Falls
    -2147483082,  # Helmet/Ochre Junction
    -2147483127,  # Numa Creek
    -2147483028,  # Tumbling Creek
    -2147483038,  # Verdant Creek
    -2147483114,  # Rockwall Random

    # ── Yoho ────────────────────────────────────────────────────────────────
    -2147483002,  # Twin Falls
    -2147482982,  # Laughing Falls
    -2147482973,  # Little Yoho
    -2147483055,  # Yoho Lake
    -2147483050,  # McArthur Creek
    -2147482979,  # Twin Lakes - Tw7
    -2147483016,  # Amiskwi Random
    -2147483023,  # Kicking Horse Random
    -2147483031,  # Ice River Random
    -2147483138,  # Ottertail Random

    # ── Banff – Lake Louise / Skoki / Bow Summit ────────────────────────────
    -2147482971,  # Paradise Valley - Pa10
    -2147483063,  # Taylor Lake - Ta6
    -2147483008,  # Baker Lake - Sk11
    -2147483081,  # Hidden Lake - Sk5
    -2147483061,  # Skoki Random
    -2147483052,  # Skoki Lodge Random
    -2147483021,  # Merlin Meadows - Sk18
    -2147482999,  # Red Deer Lakes - Sk19
    -2147483053,  # Lake Louise Random

    # ── Banff – Icefields Parkway / Bow Valley ──────────────────────────────
    -2147483107,  # Hector Lake - He5
    -2147483109,  # Bow River - Bo1C
    -2147483088,  # Upper Bow Random
    -2147483077,  # Mosquito Creek - Mo5
    -2147483113,  # Molar Creek - Mo16
    -2147483030,  # Fish Lakes - Mo18

    # ── Banff – Johnston Creek / Mystic / Sawback ───────────────────────────
    -2147482983,  # Johnston Creek - Jo18
    -2147483140,  # Luellen Lake - Jo19
    -2147483105,  # Larry's Camp - Jo9
    -2147483093,  # Badger Pass Junction - Jo29
    -2147483122,  # Big Springs - Br9
    -2147483136,  # Sawback Lake - Fm29
    -2147483099,  # Sawback Random
    -2147482998,  # Mystic Junction - Fm19
    -2147483076,  # Mystic Valley - Mi22
    -2147483024,  # Mount Cockscomb - Fm10

    # ── Banff – Cascade / Red Deer / Clearwater ──────────────────────────────
    -2147482985,  # Cascade Bridge - Cr6
    -2147483094,  # Stoney Creek - Cr15
    -2147483022,  # Block Lakes Junction - Cr37
    -2147482970,  # Flint's Park - Cr31
    -2147483095,  # Cascade Random
    -2147483120,  # Lower Red Deer Random
    -2147483080,  # Upper Red Deer Random
    -2147483111,  # Clearwater Random

    # ── Banff – Upper Spray / Sundance / Howard Douglas ─────────────────────
    -2147483065,  # Howard Douglas Lake - Su8
    -2147483075,  # Upper Spray Random
    -2147483062,  # Birdwood - Us15
    -2147483047,  # Burstall - Us18

    # ── Banff – North Saskatchewan / Siffleur / Howse ──────────────────────
    -2147483041,  # Glacier Lake - Gl9
    -2147483134,  # Alexandra Random
    -2147483029,  # Saskatchewan Crossing Random
    -2147483125,  # Upper North Saskatchewan Random
    -2147483116,  # Howse Random
    -2147483133,  # Mistaya Random
    -2147482981,  # Siffleur River - Sf
    -2147483000,  # Siffleur Random
    -2147483064,  # Norman Lake - No5
    -2147483130,  # Pipestone Random

    # ── Jasper – Brazeau / Poboktan circuit ─────────────────────────────────
    -2147483238,  # 19 - Brazeau River
    -2147483243,  # 21 - Brazeau Lake
    -2147483258,  # 22 - John-John
    -2147483179,  # 23 - Jonas Cutoff
    -2147483187,  # 25 - Waterfalls
    -2147483223,  # 26 - Poboktan
    -2147483250,  # 27 - Evelyn Creek
    -2147483167,  # 28 - Little Shovel
    -2147483222,  # 29 - Snowbowl
    -2147483191,  # 30 - Curator
    -2147483225,  # 31 - Tekarra
    -2147483193,  # 32 - Signal
    -2147483210,  # Southesk/Brazeau Random
    -2147483220,  # South Boundary Random

    # ── Jasper – Fryatt / South Boundary ────────────────────────────────────
    -2147483282,  # 33 - Watchtower
    -2147483203,  # 34 - Lower Fryatt
    -2147483214,  # 35 - Brussels
    -2147483184,  # 37 - Second Geraldine Lake
    -2147483190,  # 38 - Jacques Lake
    -2147483226,  # 39 - Saturday Night Lake
    -2147483269,  # 40 - Minnow Lake

    # ── Jasper – Tonquin Valley ──────────────────────────────────────────────
    -2147483219,  # 42 - Astoria
    -2147483289,  # 43 - Switchback
    -2147483197,  # 44 - Clitheroe
    -2147483257,  # 45 - Surprise Point
    -2147483186,  # 46 - Amethyst
    -2147483278,  # 47 - Maccarib
    -2147483233,  # 48 - Portal

    # ── Jasper – Athabasca Pass corridor ────────────────────────────────────
    -2147483224,  # 49 - Big Bend
    -2147483234,  # 50 - Athabasca Crossing
    -2147483204,  # 51 - Utopia
    -2147483211,  # 52 - Slide Creek
    -2147483166,  # 54 - Whitehorse
    -2147483185,  # 55 - Whirlpool
    -2147483192,  # 56 - Tie Camp
    -2147483242,  # 58 - Middle Forks
    -2147483230,  # 59 - Scott Camp
    -2147483178,  # 60 - Kane Meadows
    -2147483256,  # 61 - Athabasca Pass
    -2147483260,  # 62 - Shalebanks
    -2147483215,  # Whirlpool Random

    # ── Jasper – North Boundary Trail ───────────────────────────────────────
    -2147483173,  # 63 - Seldom Inn
    -2147483165,  # 65 - Horseshoe
    -2147483285,  # 66 - Willow Creek
    -2147483200,  # 68 - Welbourne
    -2147483174,  # 70 - Blue Creek
    -2147483228,  # 72 - Three Slides
    -2147483209,  # 73 - Oatmeal
    -2147483270,  # 74 - Byng
    -2147483198,  # 75 - Twintree
    -2147483263,  # 76 - Donaldson Creek
    -2147483216,  # 77 - Chown Creek
    -2147483201,  # 80 - Wolverine North
    -2147483236,  # 81 - Adolphus
    -2147483249,  # 84 - Little Heaven
    -2147483168,  # 85 - Spruce Tree
    -2147483189,  # 86 - Ancient Wall
    -2147483266,  # 87 - Natural Arch
    -2147483221,  # 92 - Medicine Tent
    -2147483183,  # 94 - La Grace
    -2147483237,  # 95 - Cairn Pass
    -2147483188,  # 96 - Cairn River
    -2147483281,  # 97 - Southesk River
    -2147483287,  # 98 - Isaac Creek
    -2147483265,  # 99 - Arête
    -2147483271,  # North Boundary Random
    -2147483231,  # Chaba Random
    -2147483229,  # Icefields Random
])


@lru_cache(maxsize=1)
def _load_mapping() -> dict:
    """Load campsites.yaml from the package data directory."""
    data_dir = importlib_resources.files("parks_monitor.data")
    text = (data_dir / "campsites.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text)


def id_to_name() -> dict[int, str]:
    """Return a flat dict of resource_id -> campsite name across all locations."""
    mapping = _load_mapping()
    result: dict[int, str] = {}
    for loc in mapping.values():
        for rid, name in loc["resources"].items():
            result[int(rid)] = name
    return result


def name_to_id() -> dict[str, int]:
    """Return a flat dict of lowercase campsite name -> resource_id."""
    return {name.lower(): rid for rid, name in id_to_name().items()}


def resolve_name(resource_id: int) -> str:
    """Look up the human-readable name for a resource ID. Returns the ID as string if unknown."""
    return id_to_name().get(resource_id, str(resource_id))


@lru_cache(maxsize=1)
def _resource_to_location() -> dict[int, int]:
    """resource_id → resource_location_id for every known resource."""
    mapping = _load_mapping()
    out: dict[int, int] = {}
    for loc in mapping.values():
        rlid = int(loc["resource_location_id"])
        for rid in loc["resources"]:
            out[int(rid)] = rlid
    return out


_RESERVATION_BASE_URL = "https://reservation.pc.gc.ca/create-booking/results"


def reservation_url(resource_id: int) -> str:
    """Canonical Parks Canada reservation URL for the park that owns this resource.

    Falls back to the site root if the resource is unknown — a broken-but-usable
    link is better than a link to the wrong park.
    """
    rlid = _resource_to_location().get(resource_id)
    if rlid is None:
        return _RESERVATION_BASE_URL
    return f"{_RESERVATION_BASE_URL}?resourceLocationId={rlid}&searchTabGroupId=0"


def resolve_id(campsite_name: str) -> int | None:
    """Exact case-insensitive match. Returns the single resource_id, or None."""
    return name_to_id().get(campsite_name.lower())


def resolve_ids(campsite_name: str) -> list[int]:
    """Find resource IDs matching a campsite name (case-insensitive substring match).

    For discovery / shell-completion only — watchlist entries should use
    `resolve_id` for unambiguous single-site matches.

    Returns all matching IDs sorted by name.
    """
    query = campsite_name.lower()
    matches = []
    for rid, name in id_to_name().items():
        if query == name.lower() or query in name.lower():
            matches.append((name, rid))
    return [rid for _, rid in sorted(matches)]


def is_gdt_site(resource_id: int) -> bool:
    """Return True if this resource is along the GDT corridor."""
    return resource_id in _GDT_SITE_IDS


def gdt_sites() -> dict[int, str]:
    """Return resource_id -> name for all GDT-relevant sites, sorted by name."""
    all_names = id_to_name()
    return dict(
        sorted(
            {rid: all_names[rid] for rid in _GDT_SITE_IDS if rid in all_names}.items(),
            key=lambda x: x[1],
        )
    )


def campsite_names(
    site_type: SiteType | None = None,
    gdt_only: bool = False,
) -> list[str]:
    """Return sorted campsite names, optionally filtered by type and/or GDT relevance."""
    all_sites = id_to_name()
    results = []
    for rid, name in all_sites.items():
        if site_type and classify_type(name) != site_type:
            continue
        if gdt_only and rid not in _GDT_SITE_IDS:
            continue
        results.append(name)
    return sorted(results)


def locations() -> list[dict]:
    """Return the list of backcountry locations with their resources."""
    mapping = _load_mapping()
    result = []
    for key, loc in mapping.items():
        result.append(
            {
                "key": key,
                "resource_location_id": loc["resource_location_id"],
                "display_name": loc["display_name"],
                "resources": {int(rid): name for rid, name in loc["resources"].items()},
            }
        )
    return result
