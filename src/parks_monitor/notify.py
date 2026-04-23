"""Notification channel implementations.

Each class implements the `Notifier` protocol from monitor.py. To add a new
channel, write a class with `async def send(self, change: AvailabilityChange) -> None`
and wire it up in cli.py — no changes to monitor.py required.
"""

from __future__ import annotations

import logging

import httpx

from parks_monitor.monitor import AvailabilityChange
from parks_monitor.resolver import reservation_url, resolve_name

logger = logging.getLogger(__name__)


class NtfyNotifier:
    """Push notifications via ntfy.sh (or a self-hosted ntfy server)."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        topic: str,
        base_url: str = "https://ntfy.sh",
    ) -> None:
        self._http = http
        self._topic = topic
        self._base_url = base_url.rstrip("/")

    async def send(self, change: AvailabilityChange) -> None:
        runs_str = ", ".join(str(r) for r in change.runs)
        title = f"Permit opened: {change.entry_name}"
        message = f"{resolve_name(change.resource_id)}: {runs_str}"
        url = reservation_url(change.resource_id)

        try:
            r = await self._http.post(
                f"{self._base_url}/{self._topic}",
                content=message.encode(),
                headers={
                    "Title": title,
                    "Priority": "high",
                    "Tags": "national_park,tada",
                    "Click": url,
                    "Actions": f"view, Book now, {url}, clear=true",
                },
                timeout=10.0,
            )
            r.raise_for_status()
            logger.info("ntfy notification sent for %s", change.entry_name)
        except Exception:
            logger.exception(
                "ntfy notification failed (topic=%s, entry=%s)",
                self._topic, change.entry_name,
            )
