from datetime import date
from pathlib import Path

import pytest

from parks_monitor.config import (
    AppConfig,
    DateRange,
    WatchlistEntry,
    load_config,
    load_watchlist,
)


def test_date_range_valid():
    dr = DateRange(start=date(2026, 7, 1), end=date(2026, 7, 3))
    assert dr.start == date(2026, 7, 1)
    assert dr.end == date(2026, 7, 3)


def test_date_range_end_before_start():
    with pytest.raises(ValueError, match="before start"):
        DateRange(start=date(2026, 7, 5), end=date(2026, 7, 1))


def test_date_range_same_day():
    dr = DateRange(start=date(2026, 7, 1), end=date(2026, 7, 1))
    assert dr.start == dr.end


def test_watchlist_entry_defaults():
    entry = WatchlistEntry(
        name="test",
        campground="Test Lake",
        resource_ids=[-2147483054],
        date_ranges=[{"start": "2026-07-01", "end": "2026-07-03"}],
    )
    assert entry.flexibility_days == 0
    assert entry.party_size == 1
    assert entry.priority == "medium"


def test_effective_date_ranges_no_flexibility():
    entry = WatchlistEntry(
        name="test",
        campground="Test",
        resource_ids=[-1],
        date_ranges=[{"start": "2026-07-01", "end": "2026-07-03"}],
        flexibility_days=0,
    )
    ranges = entry.effective_date_ranges()
    assert len(ranges) == 1
    assert ranges[0].start == date(2026, 7, 1)
    assert ranges[0].end == date(2026, 7, 3)


def test_effective_date_ranges_with_flexibility():
    entry = WatchlistEntry(
        name="test",
        campground="Test",
        resource_ids=[-1],
        date_ranges=[{"start": "2026-07-05", "end": "2026-07-07"}],
        flexibility_days=2,
    )
    ranges = entry.effective_date_ranges()
    assert ranges[0].start == date(2026, 7, 3)
    assert ranges[0].end == date(2026, 7, 9)


def test_app_config_defaults():
    config = AppConfig()
    assert config.monitor.poll_interval_minutes == 10
    assert config.monitor.dedup_hours == 4
    assert config.parks_canada.base_url == "https://reservation.pc.gc.ca"


def test_load_config_from_yaml(tmp_path: Path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
monitor:
  poll_interval_minutes: 5
  dedup_hours: 2
"""
    )
    config = load_config(config_file)
    assert config.monitor.poll_interval_minutes == 5
    assert config.monitor.dedup_hours == 2


def test_load_config_env_interpolation(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TEST_BASE_URL", "https://example.com")
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
parks_canada:
  base_url: "${TEST_BASE_URL}"
"""
    )
    config = load_config(config_file)
    assert config.parks_canada.base_url == "https://example.com"


def test_load_config_env_unresolved_raises(tmp_path: Path, monkeypatch):
    """An unset ${VAR} must fail loudly, not silently pass through the placeholder."""
    monkeypatch.delenv("UNSET_TOPIC_XYZ", raising=False)
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
notifications:
  ntfy_topic: "${UNSET_TOPIC_XYZ}"
"""
    )
    with pytest.raises(ValueError, match="UNSET_TOPIC_XYZ"):
        load_config(config_file)


def test_load_config_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nonexistent.yaml")


def test_load_watchlist(tmp_path: Path):
    wl_file = tmp_path / "watchlist.yaml"
    wl_file.write_text(
        """
entries:
  - name: "Egypt Lake"
    campground: "Egypt Lake"
    resource_ids: [-2147483137]
    date_ranges:
      - start: "2026-07-01"
        end: "2026-07-03"
    flexibility_days: 2
    party_size: 1
"""
    )
    wl = load_watchlist(wl_file)
    assert len(wl.entries) == 1
    assert wl.entries[0].name == "Egypt Lake"
    assert wl.entries[0].resource_ids == [-2147483137]
    assert wl.entries[0].flexibility_days == 2


def test_load_watchlist_invalid_dates(tmp_path: Path):
    wl_file = tmp_path / "watchlist.yaml"
    wl_file.write_text(
        """
entries:
  - name: "Bad"
    campground: "Bad"
    resource_ids: [-1]
    date_ranges:
      - start: "2026-07-05"
        end: "2026-07-01"
"""
    )
    with pytest.raises(ValueError):
        load_watchlist(wl_file)


def test_watchlist_entry_campsites_resolve():
    entry = WatchlistEntry(
        name="test",
        campground="Egypt Lake",
        campsites=["Egypt Lake - E13"],
        date_ranges=[{"start": "2026-07-01", "end": "2026-07-03"}],
    )
    assert -2147483054 in entry.resource_ids


def test_watchlist_entry_no_ids_or_campsites():
    with pytest.raises(ValueError, match="at least one"):
        WatchlistEntry(
            name="test",
            campground="Test",
            date_ranges=[{"start": "2026-07-01", "end": "2026-07-03"}],
        )


def test_watchlist_entry_bad_campsite_name():
    with pytest.raises(ValueError, match="No exact campsite match"):
        WatchlistEntry(
            name="test",
            campground="Test",
            campsites=["Nonexistent Fake Campsite XYZ"],
            date_ranges=[{"start": "2026-07-01", "end": "2026-07-03"}],
        )


def test_watchlist_entry_substring_campsite_rejected():
    """Substring matches must be rejected; user must specify the exact name."""
    with pytest.raises(ValueError, match="Did you mean"):
        WatchlistEntry(
            name="test",
            campground="Test",
            campsites=["Egypt Lake"],  # matches multiple — Egypt Lake - E13, Egypt Lake Shelter, ...
            date_ranges=[{"start": "2026-07-01", "end": "2026-07-03"}],
        )
