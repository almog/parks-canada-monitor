import asyncio
from datetime import date, datetime, timedelta

import httpx
import pytest

from parks_monitor.client import DailyAvailability
from parks_monitor.config import Watchlist, WatchlistEntry
from parks_monitor.monitor import (
    _expected_keys,
    _merge_runs,
    _Pacer,
    _prune_state,
    check_entry,
    run_cycle,
)
from parks_monitor.state import State


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
    """First cycle: everything goes into state, zero changes returned."""
    client = FakeClient({-100: _avail(3, False)})
    watchlist = Watchlist(entries=[_make_entry()])
    state = State()

    changes = await run_cycle(client, watchlist, state)
    assert changes == []
    # State should be populated
    assert len(state.last_availability) == 3


async def test_unavail_to_avail_notifies():
    """Unavailable → available produces a change."""
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

    changes = await run_cycle(client, watchlist, state)
    assert len(changes) == 1
    assert changes[0].site_date == date(2026, 7, 2)


async def test_avail_to_avail_no_duplicate():
    """Available → still available: no second change within dedup window."""
    state = State()
    state.last_availability["-100::2026-07-01"] = True
    state.last_notified["-100::2026-07-01"] = datetime.now()

    client = FakeClient({-100: _avail(1, True)})
    entry = _make_entry(start="2026-07-01", end="2026-07-01")
    watchlist = Watchlist(entries=[entry])

    changes = await run_cycle(client, watchlist, state)
    assert changes == []


async def test_avail_to_unavail_no_notification():
    """Available → unavailable: state updated, no change."""
    state = State()
    state.last_availability["-100::2026-07-01"] = True

    client = FakeClient({-100: _avail(1, False)})
    entry = _make_entry(start="2026-07-01", end="2026-07-01")
    watchlist = Watchlist(entries=[entry])

    changes = await run_cycle(client, watchlist, state)
    assert changes == []
    assert state.last_availability["-100::2026-07-01"] is False


async def test_dedup_expiry_renotifies():
    """Same opening after dedup window expires → re-emitted."""
    state = State()
    state.last_availability["-100::2026-07-01"] = False
    state.last_notified["-100::2026-07-01"] = datetime.now() - timedelta(hours=5)

    client = FakeClient({-100: _avail(1, True)})
    entry = _make_entry(start="2026-07-01", end="2026-07-01")
    watchlist = Watchlist(entries=[entry])

    changes = await run_cycle(client, watchlist, state, dedup_hours=4)
    assert len(changes) == 1


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

    await run_cycle(client, watchlist, state)
    # Both entries should be in state
    assert "-100::2026-07-01" in state.last_availability
    assert "-200::2026-07-01" in state.last_availability


async def test_opening_logged(caplog):
    """A new opening produces a NEW OPENING log line."""
    import logging
    state = State()
    state.last_availability["-100::2026-07-01"] = False

    client = FakeClient({-100: _avail(1, True)})
    entry = _make_entry(start="2026-07-01", end="2026-07-01")
    watchlist = Watchlist(entries=[entry])

    with caplog.at_level(logging.WARNING, logger="parks_monitor.monitor"):
        await run_cycle(client, watchlist, state, request_delay=(0, 0))
    assert any("NEW OPENING" in r.message for r in caplog.records)


# --- batching (Issue 5) ---


async def test_consecutive_dates_batched_into_one_notification():
    """3 consecutive new openings collapse into one change with one run."""
    state = State()
    for d in ("2026-07-01", "2026-07-02", "2026-07-03"):
        state.last_availability[f"-100::{d}"] = False

    client = FakeClient({-100: _avail(3, True)})
    watchlist = Watchlist(entries=[_make_entry()])
    changes = await run_cycle(
        client, watchlist, state, request_delay=(0, 0)
    )
    assert len(changes) == 1
    assert len(changes[0].runs) == 1
    assert changes[0].runs[0].start == date(2026, 7, 1)
    assert changes[0].runs[0].end == date(2026, 7, 3)


async def test_non_consecutive_dates_split_into_runs():
    """Non-contiguous openings produce multiple runs inside one change."""
    state = State()
    for d in ("2026-07-01", "2026-07-02", "2026-07-03"):
        state.last_availability[f"-100::{d}"] = False

    # Jul 1 open, Jul 2 booked, Jul 3 open
    client = FakeClient({-100: [
        DailyAvailability(availability=1, processed_availability=5),
        DailyAvailability(availability=0, processed_availability=5),
        DailyAvailability(availability=1, processed_availability=5),
    ]})
    watchlist = Watchlist(entries=[_make_entry()])
    changes = await run_cycle(client, watchlist, state, request_delay=(0, 0))
    assert len(changes[0].runs) == 2


async def test_merge_runs_helper():
    runs = _merge_runs([date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 4)])
    assert len(runs) == 2
    assert runs[0].start == date(2026, 7, 1)
    assert runs[0].end == date(2026, 7, 2)
    assert runs[1].start == date(2026, 7, 4)
    assert runs[1].end == date(2026, 7, 4)


async def test_merge_runs_empty():
    assert _merge_runs([]) == []


# --- pacer (Issue 4 + 7) ---


async def test_pacer_skips_first_call_then_sleeps(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr("parks_monitor.monitor.asyncio.sleep", fake_sleep)
    pacer = _Pacer((0.1, 0.2))
    await pacer.tick()  # first — no sleep
    await pacer.tick()  # second — sleep
    await pacer.tick()  # third — sleep

    assert len(sleeps) == 2
    for s in sleeps:
        assert 0.1 <= s <= 0.2


async def test_pacer_zero_delay_never_sleeps(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr("parks_monitor.monitor.asyncio.sleep", fake_sleep)
    pacer = _Pacer((0.0, 0.0))
    await pacer.tick()
    await pacer.tick()
    await pacer.tick()

    assert sleeps == []


async def test_check_entry_paces_per_call(monkeypatch):
    """check_entry sleeps once between each pair of API calls."""
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr("parks_monitor.monitor.asyncio.sleep", fake_sleep)

    client = FakeClient({-100: _avail(1, False), -200: _avail(1, False)})
    entry = WatchlistEntry(
        name="Multi", campground="Multi",
        resource_ids=[-100, -200],
        date_ranges=[{"start": "2026-07-01", "end": "2026-07-01"}],
    )
    pacer = _Pacer((0.1, 0.1))
    await check_entry(client, entry, pacer)
    # 2 resources × 1 range = 2 calls, but first tick is silent → 1 sleep
    assert len(sleeps) == 1


async def test_pacer_shared_across_entries(monkeypatch):
    """One pacer per cycle keeps a gap between every pair of requests
    even across entry boundaries."""
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr("parks_monitor.monitor.asyncio.sleep", fake_sleep)

    client = FakeClient({-100: _avail(1, False), -200: _avail(1, False)})
    entries = [
        _make_entry(name="A", resource_ids=[-100], start="2026-07-01", end="2026-07-01"),
        _make_entry(name="B", resource_ids=[-200], start="2026-07-01", end="2026-07-01"),
    ]
    watchlist = Watchlist(entries=entries)
    state = State()
    await run_cycle(
        client, watchlist, state, request_delay=(0.1, 0.1)
    )
    # 2 entries × 1 call each = 2 calls → 1 inter-call sleep total
    assert len(sleeps) == 1


# --- 429 / rate-limit handling (Issue 11) ---


async def test_run_cycle_aborts_on_429():
    """A 429 from the API short-circuits the rest of the cycle."""

    class RateLimitClient:
        def __init__(self):
            self.calls = 0

        async def check_daily_availability(self, **kw):
            self.calls += 1
            req = httpx.Request("GET", "http://x")
            resp = httpx.Response(429, request=req)
            raise httpx.HTTPStatusError("rate", request=req, response=resp)

    rl = RateLimitClient()
    entries = [
        _make_entry(name="A", resource_ids=[-100], start="2026-07-01", end="2026-07-01"),
        _make_entry(name="B", resource_ids=[-200], start="2026-07-01", end="2026-07-01"),
    ]
    watchlist = Watchlist(entries=entries)
    state = State()
    changes = await run_cycle(rl, watchlist, state, request_delay=(0, 0))
    assert changes == []
    # First call hit 429, cycle aborted before B's call
    assert rl.calls == 1
    # No state recorded for either resource
    assert state.last_availability == {}


async def test_run_cycle_429_midway_still_emits_earlier_openings():
    """429 on the Nth entry must still notify for openings detected earlier in the cycle.

    Otherwise state.last_availability would be updated to 'available' without ever
    firing a notification — and next cycle they'd look unchanged and stay silent.
    """

    class HalfFlakyClient:
        def __init__(self):
            self.calls = 0

        async def check_daily_availability(self, resource_id, **kw):
            self.calls += 1
            if resource_id == -100:
                return _avail(1, True)
            req = httpx.Request("GET", "http://x")
            resp = httpx.Response(429, request=req)
            raise httpx.HTTPStatusError("rate", request=req, response=resp)

    client = HalfFlakyClient()
    # Seed baseline so entry A's site looks like a new opening.
    state = State()
    state.last_availability["-100::2026-07-01"] = False

    entries = [
        _make_entry(name="A", resource_ids=[-100], start="2026-07-01", end="2026-07-01"),
        _make_entry(name="B", resource_ids=[-200], start="2026-07-01", end="2026-07-01"),
    ]
    watchlist = Watchlist(entries=entries)
    changes = await run_cycle(client, watchlist, state, request_delay=(0, 0))

    assert len(changes) == 1
    assert changes[0].entry_name == "A"
    assert changes[0].resource_id == -100


async def test_run_cycle_skips_500_continues():
    """A 5xx error skips that single call but continues to the next entry."""

    class FlakyClient:
        def __init__(self):
            self.calls = 0

        async def check_daily_availability(self, resource_id, **kw):
            self.calls += 1
            if resource_id == -100:
                req = httpx.Request("GET", "http://x")
                resp = httpx.Response(503, request=req)
                raise httpx.HTTPStatusError("down", request=req, response=resp)
            return _avail(1, False)

    fc = FlakyClient()
    entries = [
        _make_entry(name="A", resource_ids=[-100], start="2026-07-01", end="2026-07-01"),
        _make_entry(name="B", resource_ids=[-200], start="2026-07-01", end="2026-07-01"),
    ]
    watchlist = Watchlist(entries=entries)
    state = State()
    changes = await run_cycle(fc, watchlist, state, request_delay=(0, 0))
    assert changes == []
    assert fc.calls == 2
    # B's data was recorded; A's was skipped
    assert "-200::2026-07-01" in state.last_availability
    assert "-100::2026-07-01" not in state.last_availability


# --- state pruning (Issue 10) ---


async def test_expected_keys_basic():
    wl = Watchlist(entries=[
        WatchlistEntry(
            name="A", campground="A", resource_ids=[-100, -200],
            date_ranges=[{"start": "2026-07-01", "end": "2026-07-02"}],
        ),
    ])
    keys = _expected_keys(wl)
    assert keys == {
        "-100::2026-07-01", "-100::2026-07-02",
        "-200::2026-07-01", "-200::2026-07-02",
    }


async def test_prune_state_removes_keys_outside_watchlist():
    state = State()
    state.last_availability["-999::2026-06-15"] = True   # stale (no longer watched)
    state.last_availability["-100::2026-07-01"] = False  # still in wl
    state.last_notified["-999::2026-06-15"] = datetime.now()

    wl = Watchlist(entries=[WatchlistEntry(
        name="A", campground="A", resource_ids=[-100],
        date_ranges=[{"start": "2026-07-01", "end": "2026-07-01"}],
    )])
    expected = _expected_keys(wl)
    removed = _prune_state(state, expected)
    assert removed == 2  # one from each dict
    assert "-999::2026-06-15" not in state.last_availability
    assert "-999::2026-06-15" not in state.last_notified
    assert "-100::2026-07-01" in state.last_availability


# --- poll_loop hardening (Issues 2 + 14) ---


async def test_poll_loop_survives_run_cycle_error(tmp_path, monkeypatch):
    """An exception inside run_cycle does not kill the loop."""
    from parks_monitor.config import MonitorConfig
    from parks_monitor.monitor import poll_loop

    wl = tmp_path / "wl.yaml"
    wl.write_text(
        'entries:\n  - name: A\n    campground: A\n    resource_ids: [-100]\n'
        '    date_ranges:\n      - {start: "2026-07-01", end: "2026-07-01"}\n'
    )
    calls = {"n": 0}

    async def fake_run_cycle(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return []

    monkeypatch.setattr("parks_monitor.monitor.run_cycle", fake_run_cycle)
    cfg = MonitorConfig(poll_interval_minutes=0, jitter_seconds=0)
    task = asyncio.create_task(
        poll_loop(None, wl, State(), cfg)
    )
    await asyncio.sleep(0.05)  # allow several iterations
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls["n"] >= 2  # loop continued after the failure


async def test_poll_loop_survives_broken_watchlist(tmp_path, monkeypatch):
    """A YAML edit error during reload does not kill the loop."""
    from parks_monitor.config import MonitorConfig
    from parks_monitor.monitor import poll_loop

    wl = tmp_path / "wl.yaml"
    wl.write_text(
        'entries:\n  - name: A\n    campground: A\n    resource_ids: [-100]\n'
        '    date_ranges:\n      - {start: "2026-07-01", end: "2026-07-01"}\n'
    )

    cycles = {"n": 0}

    async def fake_run_cycle(*a, **kw):
        cycles["n"] += 1
        return []

    monkeypatch.setattr("parks_monitor.monitor.run_cycle", fake_run_cycle)
    cfg = MonitorConfig(poll_interval_minutes=0, jitter_seconds=0)
    task = asyncio.create_task(
        poll_loop(None, wl, State(), cfg)
    )
    await asyncio.sleep(0.02)
    # Break the file
    wl.write_text("entries: this is not a list\n")
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Cycles still ran with the previous (good) watchlist
    assert cycles["n"] >= 2
