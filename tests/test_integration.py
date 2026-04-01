"""Integration tests: wire real components together, mock only HTTP."""
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from parks_monitor.client import DailyAvailability
from parks_monitor.config import Watchlist, WatchlistEntry, load_watchlist
from parks_monitor.monitor import run_cycle
from parks_monitor.state import State


class FakeNotifier:
    def __init__(self):
        self.sent: list = []

    async def notify(self, change, entry):
        self.sent.append((change, entry))


class ScriptedClient:
    """Returns different availability per call (simulating state changes over time)."""

    def __init__(self, call_sequence: list[list[DailyAvailability]]):
        self._sequence = list(call_sequence)
        self._call_idx = 0

    async def check_daily_availability(
        self, resource_id, start_date, end_date, party_size=1
    ):
        if self._call_idx < len(self._sequence):
            result = self._sequence[self._call_idx]
            self._call_idx += 1
            return result
        return self._sequence[-1]


def _booked(n: int) -> list[DailyAvailability]:
    return [DailyAvailability(availability=0, processed_availability=5) for _ in range(n)]


def _available(n: int) -> list[DailyAvailability]:
    return [DailyAvailability(availability=1, processed_availability=5) for _ in range(n)]


def _make_entry():
    return WatchlistEntry(
        name="Egypt Lake",
        campground="Egypt Lake",
        resource_ids=[-100],
        date_ranges=[{"start": "2026-07-01", "end": "2026-07-03"}],
    )


async def test_full_poll_cycle_lifecycle():
    """
    Cycle 1: all unavailable → baseline, zero notifications
    Cycle 2: one site available → notification sent
    Cycle 3: same site still available → no duplicate
    Cycle 4: site unavailable again → state updated, no notification
    """
    entry = _make_entry()
    watchlist = Watchlist(entries=[entry])
    state = State()
    notifier = FakeNotifier()

    # Cycle 1: all booked
    client = ScriptedClient([_booked(3)])
    count = await run_cycle(client, watchlist, state, notifier)
    assert count == 0
    assert len(notifier.sent) == 0
    assert len(state.last_availability) == 3

    # Cycle 2: day 2 becomes available
    mixed = [
        DailyAvailability(availability=0, processed_availability=5),
        DailyAvailability(availability=1, processed_availability=5),
        DailyAvailability(availability=0, processed_availability=5),
    ]
    client = ScriptedClient([mixed])
    count = await run_cycle(client, watchlist, state, notifier)
    assert count == 1
    assert notifier.sent[0][0].site_date == date(2026, 7, 2)

    # Cycle 3: same availability → no duplicate
    client = ScriptedClient([mixed])
    count = await run_cycle(client, watchlist, state, notifier)
    assert count == 0
    assert len(notifier.sent) == 1  # still just the one from cycle 2

    # Cycle 4: back to all booked
    client = ScriptedClient([_booked(3)])
    count = await run_cycle(client, watchlist, state, notifier)
    assert count == 0
    assert state.last_availability["-100::2026-07-02"] is False


async def test_watchlist_hot_reload(tmp_path: Path):
    """Verify watchlist file changes are detected."""
    wl_file = tmp_path / "watchlist.yaml"
    wl_file.write_text("""
entries:
  - name: "Site A"
    campground: "Site A"
    resource_ids: [-100]
    date_ranges:
      - start: "2026-07-01"
        end: "2026-07-01"
""")
    wl = load_watchlist(wl_file)
    assert len(wl.entries) == 1

    # Modify and reload
    wl_file.write_text("""
entries:
  - name: "Site A"
    campground: "Site A"
    resource_ids: [-100]
    date_ranges:
      - start: "2026-07-01"
        end: "2026-07-01"
  - name: "Site B"
    campground: "Site B"
    resource_ids: [-200]
    date_ranges:
      - start: "2026-07-05"
        end: "2026-07-05"
""")
    wl = load_watchlist(wl_file)
    assert len(wl.entries) == 2
    assert wl.entries[1].name == "Site B"


async def test_multiple_resources_per_entry():
    """Entry with multiple resource_ids checks all of them."""
    entry = WatchlistEntry(
        name="Multi",
        campground="Multi",
        resource_ids=[-100, -200],
        date_ranges=[{"start": "2026-07-01", "end": "2026-07-01"}],
    )
    watchlist = Watchlist(entries=[entry])
    state = State()
    notifier = FakeNotifier()

    # First call returns booked, second returns available
    client = ScriptedClient([_booked(1), _available(1)])
    count = await run_cycle(client, watchlist, state, notifier)
    # Baseline — no notifications
    assert count == 0
    assert "-100::2026-07-01" in state.last_availability
    assert "-200::2026-07-01" in state.last_availability
    assert state.last_availability["-100::2026-07-01"] is False
    assert state.last_availability["-200::2026-07-01"] is True


async def test_dedup_across_cycles():
    """Notification dedup works across multiple poll cycles."""
    entry = _make_entry()
    watchlist = Watchlist(entries=[entry])
    state = State()
    notifier = FakeNotifier()

    # Cycle 1: baseline all booked
    client = ScriptedClient([_booked(3)])
    await run_cycle(client, watchlist, state, notifier)

    # Cycle 2: slot opens → notify
    client = ScriptedClient([_available(3)])
    await run_cycle(client, watchlist, state, notifier)
    assert len(notifier.sent) == 3

    # Cycle 3: goes back to booked
    client = ScriptedClient([_booked(3)])
    await run_cycle(client, watchlist, state, notifier)

    # Cycle 4: opens again within dedup window → should NOT re-notify
    client = ScriptedClient([_available(3)])
    count = await run_cycle(client, watchlist, state, notifier, dedup_hours=4)
    assert count == 0  # within dedup window
