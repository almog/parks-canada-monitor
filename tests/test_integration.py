"""Integration tests: wire real components together, mock only HTTP."""
from datetime import date
from pathlib import Path

from parks_monitor.client import DailyAvailability
from parks_monitor.config import Watchlist, WatchlistEntry, load_watchlist
from parks_monitor.monitor import run_cycle
from parks_monitor.state import State


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
    all_changes = []

    # Cycle 1: all booked
    client = ScriptedClient([_booked(3)])
    changes = await run_cycle(client, watchlist, state)
    all_changes.extend(changes)
    assert changes == []
    assert len(state.last_availability) == 3

    # Cycle 2: day 2 becomes available
    mixed = [
        DailyAvailability(availability=0, processed_availability=5),
        DailyAvailability(availability=1, processed_availability=5),
        DailyAvailability(availability=0, processed_availability=5),
    ]
    client = ScriptedClient([mixed])
    changes = await run_cycle(client, watchlist, state)
    all_changes.extend(changes)
    assert len(changes) == 1
    assert changes[0].site_date == date(2026, 7, 2)

    # Cycle 3: same availability → no duplicate
    client = ScriptedClient([mixed])
    changes = await run_cycle(client, watchlist, state)
    assert changes == []
    assert len(all_changes) == 1  # still just the one from cycle 2

    # Cycle 4: back to all booked
    client = ScriptedClient([_booked(3)])
    changes = await run_cycle(client, watchlist, state)
    assert changes == []
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

    # First call returns booked, second returns available
    client = ScriptedClient([_booked(1), _available(1)])
    changes = await run_cycle(client, watchlist, state)
    # Baseline — no changes
    assert changes == []
    assert "-100::2026-07-01" in state.last_availability
    assert "-200::2026-07-01" in state.last_availability
    assert state.last_availability["-100::2026-07-01"] is False
    assert state.last_availability["-200::2026-07-01"] is True


async def test_dedup_across_cycles():
    """Notification dedup works across multiple poll cycles."""
    entry = _make_entry()
    watchlist = Watchlist(entries=[entry])
    state = State()

    # Cycle 1: baseline all booked
    client = ScriptedClient([_booked(3)])
    await run_cycle(client, watchlist, state)

    # Cycle 2: slot opens → one batched change covering all 3 days
    client = ScriptedClient([_available(3)])
    changes = await run_cycle(client, watchlist, state)
    assert len(changes) == 1
    assert len(changes[0].runs) == 1
    assert changes[0].runs[0].start == date(2026, 7, 1)
    assert changes[0].runs[0].end == date(2026, 7, 3)

    # Cycle 3: goes back to booked
    client = ScriptedClient([_booked(3)])
    await run_cycle(client, watchlist, state)

    # Cycle 4: opens again within dedup window → should NOT re-emit
    client = ScriptedClient([_available(3)])
    changes = await run_cycle(client, watchlist, state, dedup_hours=4)
    assert changes == []  # within dedup window
