from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from parks_monitor.client import GoingToCampClient
from parks_monitor.config import MonitorConfig, Watchlist, WatchlistEntry, load_watchlist
from parks_monitor.state import State

logger = logging.getLogger(__name__)


@dataclass
class AvailabilityChange:
    entry_name: str
    resource_id: int
    site_date: date
    is_available: bool


async def check_entry(
    client: GoingToCampClient, entry: WatchlistEntry
) -> list[tuple[str, int, date, bool]]:
    """Query API for one watchlist entry.

    Returns (key, resource_id, date, available) tuples.
    Expands flexibility window. Catches and logs API errors, returns [] on failure.
    """
    results: list[tuple[str, int, date, bool]] = []
    ranges = entry.effective_date_ranges()
    for rid in entry.resource_ids:
        for dr in ranges:
            try:
                days = await client.check_daily_availability(
                    resource_id=rid,
                    start_date=dr.start,
                    end_date=dr.end,
                    party_size=entry.party_size,
                )
                current = dr.start
                for d in days:
                    key = f"{rid}::{current.isoformat()}"
                    available = d.availability == 1
                    results.append((key, rid, current, available))
                    current = date.fromordinal(current.toordinal() + 1)
            except Exception:
                logger.exception(
                    "Error checking resource %d for %s", rid, entry.name
                )
    return results


async def run_cycle(
    client: GoingToCampClient,
    watchlist: Watchlist,
    state: State,
    notifier,
    dedup_hours: int = 4,
) -> int:
    """Run one poll cycle. Returns number of notifications sent."""
    changes_notified = 0
    for entry in watchlist.entries:
        results = await check_entry(client, entry)
        for key, resource_id, site_date, available in results:
            if state.is_new_opening(key, available) and state.should_notify(
                key, dedup_hours
            ):
                change = AvailabilityChange(
                    entry_name=entry.name,
                    resource_id=resource_id,
                    site_date=site_date,
                    is_available=available,
                )
                try:
                    await notifier.notify(change, entry)
                    state.last_notified[key] = datetime.now()
                    changes_notified += 1
                except Exception:
                    logger.exception("Failed to send notification for %s", key)
            state.last_availability[key] = available
        # Random delay between entries to avoid hammering the API
        if len(watchlist.entries) > 1:
            await asyncio.sleep(random.uniform(1, 3))
    state.last_poll_at = datetime.now()
    return changes_notified


async def poll_loop(
    client: GoingToCampClient,
    watchlist_path: Path,
    state: State,
    notifier,
    config: MonitorConfig,
    dedup_hours: int = 4,
):
    """Run cycles on interval until cancelled. Reloads watchlist on file change."""
    last_mtime: float | None = None
    watchlist = load_watchlist(watchlist_path)
    last_mtime = watchlist_path.stat().st_mtime

    while True:
        # Hot-reload watchlist if file changed
        mtime = watchlist_path.stat().st_mtime
        if mtime != last_mtime:
            logger.info("Watchlist changed, reloading")
            watchlist = load_watchlist(watchlist_path)
            last_mtime = mtime

        count = await run_cycle(client, watchlist, state, notifier, dedup_hours)
        logger.info(
            "Poll cycle complete: %d notifications sent, %d entries checked",
            count,
            len(watchlist.entries),
        )

        jitter = random.uniform(-config.jitter_seconds, config.jitter_seconds)
        await asyncio.sleep(config.poll_interval_minutes * 60 + jitter)
