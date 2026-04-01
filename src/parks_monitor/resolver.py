"""Resolve resource IDs to/from human-readable campsite names.

Uses the bundled campsites.yaml mapping (fetched from the Parks Canada API).
"""

from __future__ import annotations

from importlib import resources as importlib_resources
from functools import lru_cache

import yaml


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


def resolve_ids(campsite_name: str) -> list[int]:
    """Find resource IDs matching a campsite name (case-insensitive substring match).

    Returns all matching IDs sorted by name.
    """
    query = campsite_name.lower()
    matches = []
    for rid, name in id_to_name().items():
        if query == name.lower() or query in name.lower():
            matches.append((name, rid))
    return [rid for _, rid in sorted(matches)]


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
