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
    auto_book: bool = False
    priority: Literal["high", "medium", "low"] = "medium"

    @model_validator(mode="after")
    def resolve_campsite_names(self):
        """Resolve human-readable campsite names to resource IDs."""
        if self.campsites:
            from parks_monitor.resolver import resolve_ids

            for campsite_name in self.campsites:
                ids = resolve_ids(campsite_name)
                if not ids:
                    raise ValueError(
                        f"No campsite found matching '{campsite_name}'. "
                        f"Run 'parks-monitor discover' to see available names."
                    )
                for rid in ids:
                    if rid not in self.resource_ids:
                        self.resource_ids.append(rid)
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


class ParksCanadaConfig(BaseModel):
    base_url: str = "https://reservation.pc.gc.ca"


class EmailConfig(BaseModel):
    enabled: bool = True
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    from_address: str = ""
    to_addresses: list[str] = []


class NotificationsConfig(BaseModel):
    email: EmailConfig = EmailConfig()
    dedup_hours: int = 4


class AutoBookConfig(BaseModel):
    enabled: bool = False
    dry_run: bool = True
    daily_limit: int = 3


class AppConfig(BaseModel):
    monitor: MonitorConfig = MonitorConfig()
    parks_canada: ParksCanadaConfig = ParksCanadaConfig()
    notifications: NotificationsConfig = NotificationsConfig()
    auto_book: AutoBookConfig = AutoBookConfig()


_ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")


def _interpolate_env_vars(obj):
    """Recursively replace ${VAR} with os.environ[VAR] in strings."""
    if isinstance(obj, str):
        return _ENV_VAR_PATTERN.sub(
            lambda m: os.environ.get(m.group(1), m.group(0)), obj
        )
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
