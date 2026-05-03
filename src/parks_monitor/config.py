from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, model_validator


class DateRange(BaseModel):
    start: date
    end: date

    @model_validator(mode="after")
    def end_after_start(self):
        if self.end < self.start:
            raise ValueError(f"end date {self.end} is before start date {self.start}")
        return self


class WatchlistEntry(BaseModel):
    name: str
    campground: str
    resource_ids: list[int] = []
    campsites: list[str] = []
    date_ranges: list[DateRange]
    flexibility_days: int = 0
    party_size: int = 1
    priority: Literal["high", "medium", "low"] = "medium"

    @model_validator(mode="after")
    def resolve_campsite_names(self):
        """Resolve human-readable campsite names to resource IDs (exact match only)."""
        if self.campsites:
            from parks_monitor.resolver import resolve_id, resolve_ids, resolve_name

            ids = list(self.resource_ids)
            seen = set(ids)
            for campsite_name in self.campsites:
                rid = resolve_id(campsite_name)
                if rid is None:
                    suggestions = [
                        resolve_name(r) for r in resolve_ids(campsite_name)[:5]
                    ]
                    hint = (
                        f" Did you mean: {', '.join(suggestions)}?"
                        if suggestions
                        else " Run 'parks-monitor discover' to see available names."
                    )
                    raise ValueError(
                        f"No exact campsite match for '{campsite_name}'.{hint}"
                    )
                if rid not in seen:
                    ids.append(rid)
                    seen.add(rid)
            self.resource_ids = ids
        if not self.resource_ids:
            raise ValueError(
                "Entry must have at least one of 'resource_ids' or 'campsites'"
            )
        return self

    def effective_date_ranges(self) -> list[DateRange]:
        """Expand each date range by flexibility_days in both directions."""
        from datetime import timedelta

        expanded = []
        for dr in self.date_ranges:
            expanded.append(
                DateRange(
                    start=dr.start - timedelta(days=self.flexibility_days),
                    end=dr.end + timedelta(days=self.flexibility_days),
                )
            )
        return expanded


class Watchlist(BaseModel):
    entries: list[WatchlistEntry]


class MonitorConfig(BaseModel):
    poll_interval_minutes: int = 10
    jitter_seconds: int = 30
    request_delay_min_seconds: float = 1.0
    request_delay_max_seconds: float = 3.0
    dedup_hours: int = 4


class ParksCanadaConfig(BaseModel):
    base_url: str = "https://reservation.pc.gc.ca"


class NotificationsConfig(BaseModel):
    ntfy_topic: str = ""
    ntfy_url: str = "https://ntfy.sh"


class AppConfig(BaseModel):
    monitor: MonitorConfig = MonitorConfig()
    parks_canada: ParksCanadaConfig = ParksCanadaConfig()
    notifications: NotificationsConfig = NotificationsConfig()


_ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")


def _interpolate_env_vars(obj):
    """Recursively replace ${VAR} with os.environ[VAR] in strings.

    Raises ValueError if any ${VAR} reference has no matching environment
    variable — silently leaving the literal placeholder would misconfigure
    downstream calls (e.g., posting to ntfy.sh/${MY_TOPIC} as a topic name).
    """
    if isinstance(obj, str):
        missing: list[str] = []

        def replace(m: re.Match) -> str:
            name = m.group(1)
            if name in os.environ:
                return os.environ[name]
            missing.append(name)
            return m.group(0)

        result = _ENV_VAR_PATTERN.sub(replace, obj)
        if missing:
            raise ValueError(
                f"Unresolved environment variable(s) in config: "
                f"{', '.join(sorted(set(missing)))}"
            )
        return result
    if isinstance(obj, dict):
        return {k: _interpolate_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_interpolate_env_vars(v) for v in obj]
    return obj


def load_config(path: Path) -> AppConfig:
    """Load config.yaml with env var interpolation."""
    text = path.read_text()
    data = yaml.safe_load(text) or {}
    data = _interpolate_env_vars(data)
    return AppConfig(**data)


def load_watchlist(path: Path) -> Watchlist:
    """Load watchlist.yaml."""
    text = path.read_text()
    data = yaml.safe_load(text) or {}
    return Watchlist(**data)
