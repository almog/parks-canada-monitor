from datetime import date, datetime, timedelta

import pytest

from parks_monitor.client import DailyAvailability
from parks_monitor.config import Watchlist, WatchlistEntry
from parks_monitor.monitor import check_entry, run_cycle
from parks_monitor.state import State


class FakeNotifier:
    def __init__(self):
        self.sent: list = []

    async def notify(self, change, entry):
        self.sent.append((change, entry))


class FakeClient:
    """Returns pre-configured availability per resource_id."""

    def __init__(self, responses: dict[int, list[DailyAvailability]]):
        self._responses = responses

    async def check_daily_availability(
        self, resource_id, start_date, end_date, party_size=1
    ):
        return self._responses.get(resource_id, [])


def _make_entry(name="Test", resource_ids=None, start="2026-07-01", end="2026-07-03", flexibility_days=0):
    return WatchlistEntry(
        name=name,
        campground=name,
        resource_ids=resource_ids or [-100],
        date_ranges=[{"start": start, "end": end}],
        flexibility_days=flexibility_days,
    )


def _avail(n: int, available: bool) -> list[DailyAvailability]:
    return [
        DailyAvailability(
            availability=1 if available else 0,
            processed_availability=5,
        )
        for _ in range(n)
    ]


# --- check_entry ---


async def test_check_entry_returns_tuples():
    client = FakeClient({-100: _avail(3, True)})
    entry = _make_entry()
    results = await check_entry(client, entry)
    assert len(results) == 3
    key, rid, d, avail = results[0]
    assert rid == -100
    assert d == date(2026, 7, 1)
    assert avail is True


async def test_check_entry_api_error_returns_empty():
    class ErrorClient:
        async def check_daily_availability(self, **kw):
            raise ConnectionError("boom")

    entry = _make_entry()
    results = await check_entry(ErrorClient(), entry)
    assert results == []


async def test_check_entry_flexibility_expands_dates():
    calls = []

    class TrackingClient:
        async def check_daily_availability(
            self, resource_id, start_date, end_date, party_size=1
        ):
            calls.append((start_date, end_date))
            return _avail((end_date - start_date).days + 1, False)

    entry = _make_entry(flexibility_days=2)
    await check_entry(TrackingClient(), entry)
    # Original range is Jul 1-3, with ±2 flexibility should be Jun 29 - Jul 5
    assert calls[0] == (date(2026, 6, 29), date(2026, 7, 5))


# --- run_cycle ---


async def test_baseline_no_notifications():
    """First cycle: everything goes into state, zero notifications."""
    client = FakeClient({-100: _avail(3, False)})
    watchlist = Watchlist(entries=[_make_entry()])
    state = State()
    notifier = FakeNotifier()

    count = await run_cycle(client, watchlist, state, notifier)
    assert count == 0
    assert len(notifier.sent) == 0
    # State should be populated
    assert len(state.last_availability) == 3


async def test_unavail_to_avail_notifies():
    """Unavailable → available triggers notification."""
    state = State()
    # Seed baseline: all unavailable
    state.last_availability["-100::2026-07-01"] = False
    state.last_availability["-100::2026-07-02"] = False
    state.last_availability["-100::2026-07-03"] = False

    # Now day 2 becomes available
    responses = [
        DailyAvailability(availability=0, processed_availability=5),
        DailyAvailability(availability=1, processed_availability=5),
        DailyAvailability(availability=0, processed_availability=5),
    ]
    client = FakeClient({-100: responses})
    watchlist = Watchlist(entries=[_make_entry()])
    notifier = FakeNotifier()

    count = await run_cycle(client, watchlist, state, notifier)
    assert count == 1
    assert len(notifier.sent) == 1
    change, entry = notifier.sent[0]
    assert change.site_date == date(2026, 7, 2)
    assert change.is_available is True


async def test_avail_to_avail_no_duplicate():
    """Available → still available: no second notification within dedup window."""
    state = State()
    state.last_availability["-100::2026-07-01"] = True
    state.last_notified["-100::2026-07-01"] = datetime.now()

    client = FakeClient({-100: _avail(1, True)})
    entry = _make_entry(start="2026-07-01", end="2026-07-01")
    watchlist = Watchlist(entries=[entry])
    notifier = FakeNotifier()

    count = await run_cycle(client, watchlist, state, notifier)
    assert count == 0


async def test_avail_to_unavail_no_notification():
    """Available → unavailable: state updated, no notification."""
    state = State()
    state.last_availability["-100::2026-07-01"] = True

    client = FakeClient({-100: _avail(1, False)})
    entry = _make_entry(start="2026-07-01", end="2026-07-01")
    watchlist = Watchlist(entries=[entry])
    notifier = FakeNotifier()

    count = await run_cycle(client, watchlist, state, notifier)
    assert count == 0
    assert state.last_availability["-100::2026-07-01"] is False


async def test_dedup_expiry_renotifies():
    """Same opening after dedup window expires → re-notified."""
    state = State()
    state.last_availability["-100::2026-07-01"] = False
    state.last_notified["-100::2026-07-01"] = datetime.now() - timedelta(hours=5)

    client = FakeClient({-100: _avail(1, True)})
    entry = _make_entry(start="2026-07-01", end="2026-07-01")
    watchlist = Watchlist(entries=[entry])
    notifier = FakeNotifier()

    count = await run_cycle(client, watchlist, state, notifier, dedup_hours=4)
    assert count == 1


async def test_multiple_entries_all_checked():
    """Multiple watchlist entries are all checked."""
    client = FakeClient({
        -100: _avail(1, False),
        -200: _avail(1, True),
    })
    entries = [
        _make_entry(name="A", resource_ids=[-100], start="2026-07-01", end="2026-07-01"),
        _make_entry(name="B", resource_ids=[-200], start="2026-07-01", end="2026-07-01"),
    ]
    watchlist = Watchlist(entries=entries)
    state = State()
    notifier = FakeNotifier()

    await run_cycle(client, watchlist, state, notifier)
    # Both entries should be in state
    assert "-100::2026-07-01" in state.last_availability
    assert "-200::2026-07-01" in state.last_availability


async def test_notifier_error_doesnt_crash_cycle():
    """If notifier raises, the cycle continues."""
    state = State()
    state.last_availability["-100::2026-07-01"] = False

    client = FakeClient({-100: _avail(1, True)})
    entry = _make_entry(start="2026-07-01", end="2026-07-01")
    watchlist = Watchlist(entries=[entry])

    class BrokenNotifier:
        async def notify(self, change, entry):
            raise RuntimeError("email failed")

    count = await run_cycle(client, watchlist, state, BrokenNotifier())
    assert count == 0
    # State should still be updated
    assert state.last_availability["-100::2026-07-01"] is True
