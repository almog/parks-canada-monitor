from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

from parks_monitor.client import GoingToCampClient
from parks_monitor.config import MonitorConfig, Watchlist, WatchlistEntry, load_watchlist
from parks_monitor.state import State

logger = logging.getLogger(__name__)


@dataclass
class DateRun:
    """A contiguous range of dates, inclusive on both ends."""

    start: date
    end: date

    def __str__(self) -> str:
        if self.start == self.end:
            return self.start.isoformat()
        return f"{self.start.isoformat()} → {self.end.isoformat()}"


@dataclass
class AvailabilityChange:
    entry_name: str
    resource_id: int
    runs: list[DateRun]

    @property
    def site_date(self) -> date:
        """First date of the first run — convenience for callers / legacy tests."""
        return self.runs[0].start

    @property
    def is_available(self) -> bool:
        # Currently we only emit AvailabilityChange for new openings.
        return True

    @property
    def all_dates(self) -> list[date]:
        out: list[date] = []
        for r in self.runs:
            d = r.start
            while d <= r.end:
                out.append(d)
                d += timedelta(days=1)
        return out


class _RateLimited(Exception):
    """Raised when the API returns 429. Triggers cycle abort, not per-call retry."""


class _Pacer:
    """Sleeps `delay` seconds between calls. The first call is not delayed.

    One pacer is created per cycle; passing the same instance to multiple
    `check_entry` calls keeps pacing consistent across entry boundaries.
    """

    def __init__(self, delay: tuple[float, float]):
        self._delay = delay
        self._first = True

    async def tick(self) -> None:
        if self._first:
            self._first = False
            return
        lo, hi = self._delay
        if hi > 0:
            await asyncio.sleep(random.uniform(lo, hi))


def _merge_runs(dates: list[date]) -> list[DateRun]:
    """Merge a flat list of dates into contiguous DateRuns."""
    if not dates:
        return []
    dates = sorted(set(dates))
    runs = [DateRun(start=dates[0], end=dates[0])]
    for d in dates[1:]:
        if d == runs[-1].end + timedelta(days=1):
            runs[-1] = DateRun(start=runs[-1].start, end=d)
        else:
            runs.append(DateRun(start=d, end=d))
    return runs


def _expected_keys(watchlist: Watchlist) -> set[str]:
    """The state keys the current watchlist can produce."""
    keys: set[str] = set()
    for entry in watchlist.entries:
        for dr in entry.effective_date_ranges():
            d = dr.start
            while d <= dr.end:
                for rid in entry.resource_ids:
                    keys.add(f"{rid}::{d.isoformat()}")
                d += timedelta(days=1)
    return keys


def _prune_state(state: State, expected: set[str]) -> int:
    """Drop state keys not produced by the current watchlist. Returns count removed."""
    stale_avail = set(state.last_availability) - expected
    stale_notif = set(state.last_notified) - expected
    for k in stale_avail:
        state.last_availability.pop(k, None)
    for k in stale_notif:
        state.last_notified.pop(k, None)
    return len(stale_avail) + len(stale_notif)


async def check_entry(
    client: GoingToCampClient,
    entry: WatchlistEntry,
    pacer: _Pacer | None = None,
) -> list[tuple[str, int, date, bool]]:
    """Query API for one watchlist entry.

    Returns (key, resource_id, date, available) tuples. Expands the flexibility
    window. Catches and logs per-call API errors. Raises `_RateLimited` if the
    API returns 429 — caller should abort the cycle.
    """
    if pacer is None:
        pacer = _Pacer((0.0, 0.0))

    results: list[tuple[str, int, date, bool]] = []
    ranges = entry.effective_date_ranges()
    for rid in entry.resource_ids:
        for dr in ranges:
            await pacer.tick()
            try:
                days = await client.check_daily_availability(
                    resource_id=rid,
                    start_date=dr.start,
                    end_date=dr.end,
                    party_size=entry.party_size,
                )
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                if code == 429:
                    logger.warning(
                        "Rate limited (429) on resource %d for %s; aborting cycle",
                        rid, entry.name,
                    )
                    raise _RateLimited() from e
                if 500 <= code < 600:
                    logger.warning(
                        "Server error %d on resource %d for %s; skipping this call",
                        code, rid, entry.name,
                    )
                    continue
                logger.exception(
                    "HTTP error %d on resource %d for %s", code, rid, entry.name
                )
                continue
            except (httpx.TimeoutException, httpx.NetworkError):
                logger.warning(
                    "Network error on resource %d for %s; skipping this call",
                    rid, entry.name,
                )
                continue
            except Exception:
                logger.exception(
                    "Unexpected error checking resource %d for %s", rid, entry.name
                )
                continue

            current = dr.start
            for d in days:
                key = f"{rid}::{current.isoformat()}"
                available = d.is_bookable
                results.append((key, rid, current, available))
                current = current + timedelta(days=1)
    return results


async def run_cycle(
    client: GoingToCampClient,
    watchlist: Watchlist,
    state: State,
    dedup_hours: int = 4,
    request_delay: tuple[float, float] = (1.0, 3.0),
) -> list[AvailabilityChange]:
    """Run one poll cycle. Returns the list of new openings detected.

    Diff each (resource_id, date) against state, batch openings into runs,
    and log one line per (entry, resource_id) pair. The log line is the
    notification — there is no separate delivery channel.
    """
    pacer = _Pacer(request_delay)
    pending: dict[tuple[str, int], list[date]] = {}

    for entry in watchlist.entries:
        try:
            results = await check_entry(client, entry, pacer)
        except _RateLimited:
            logger.warning("Cycle aborted early due to rate limiting")
            state.last_poll_at = datetime.now()
            return []

        for key, rid, site_date, available in results:
            if state.is_new_opening(key, available) and state.should_notify(
                key, dedup_hours
            ):
                pending.setdefault((entry.name, rid), []).append(site_date)
            state.last_availability[key] = available

    changes: list[AvailabilityChange] = []
    now = datetime.now()
    for (entry_name, rid), dates in pending.items():
        runs = _merge_runs(dates)
        change = AvailabilityChange(
            entry_name=entry_name, resource_id=rid, runs=runs,
        )
        logger.warning(
            "NEW OPENING: %s — resource %d — %s",
            entry_name, rid, ", ".join(str(r) for r in runs),
        )
        for d in dates:
            state.last_notified[f"{rid}::{d.isoformat()}"] = now
        changes.append(change)

    state.last_poll_at = datetime.now()
    return changes


async def poll_loop(
    client: GoingToCampClient,
    watchlist_path: Path,
    state: State,
    config: MonitorConfig,
):
    """Run cycles on interval until cancelled. Reloads watchlist on file change.

    Survives transient errors — any exception inside a cycle is logged and the
    loop continues. Cancellation propagates so KeyboardInterrupt still works.
    """
    last_mtime: float | None = None
    watchlist: Watchlist | None = None
    request_delay = (
        config.request_delay_min_seconds,
        config.request_delay_max_seconds,
    )
    dedup_hours = config.dedup_hours

    while True:
        try:
            mtime = watchlist_path.stat().st_mtime
            if mtime != last_mtime:
                try:
                    new_watchlist = load_watchlist(watchlist_path)
                    if watchlist is not None:
                        removed = _prune_state(state, _expected_keys(new_watchlist))
                        if removed:
                            logger.info(
                                "Pruned %d stale state keys after watchlist reload",
                                removed,
                            )
                    watchlist = new_watchlist
                    logger.info(
                        "Watchlist loaded: %d entries", len(watchlist.entries)
                    )
                except Exception:
                    logger.exception(
                        "Failed to (re)load watchlist at %s; keeping previous version",
                        watchlist_path,
                    )
                # Update mtime even on failure so we don't re-spam on every cycle.
                # A subsequent edit (new mtime) will trigger another reload attempt.
                last_mtime = mtime

            if watchlist is None:
                logger.warning("No watchlist loaded yet; skipping cycle")
            else:
                changes = await run_cycle(
                    client, watchlist, state, dedup_hours, request_delay,
                )
                logger.info(
                    "Poll cycle complete: %d new openings, %d entries",
                    len(changes), len(watchlist.entries),
                )

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Unexpected error during poll cycle; continuing")

        jitter = random.uniform(-config.jitter_seconds, config.jitter_seconds)
        sleep_seconds = max(0.0, config.poll_interval_minutes * 60 + jitter)
        await asyncio.sleep(sleep_seconds)
