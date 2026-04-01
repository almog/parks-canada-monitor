from __future__ import annotations

import logging
from email.message import EmailMessage

import aiosmtplib

from parks_monitor.config import EmailConfig, WatchlistEntry
from parks_monitor.monitor import AvailabilityChange
from parks_monitor.resolver import resolve_name

logger = logging.getLogger(__name__)

_BOOKING_URL = "https://reservation.pc.gc.ca/create-booking/results?resourceLocationId=-2147483638&searchTabGroupId=0"


class EmailNotifier:
    def __init__(self, config: EmailConfig):
        self._config = config

    async def notify(self, change: AvailabilityChange, entry: WatchlistEntry) -> None:
        if not self._config.enabled:
            return

        campsite_name = resolve_name(change.resource_id)
        subject = f"{entry.name} — {campsite_name} available {change.site_date.strftime('%b %d')}"
        body = (
            f"Campsite opening detected!\n\n"
            f"Watchlist entry: {entry.name}\n"
            f"Campground: {entry.campground}\n"
            f"Campsite: {campsite_name}\n"
            f"Date: {change.site_date.isoformat()}\n"
            f"Party size: {entry.party_size}\n\n"
            f"Book now: {_BOOKING_URL}\n"
        )

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self._config.from_address
        msg["To"] = ", ".join(self._config.to_addresses)
        msg.set_content(body)

        try:
            await aiosmtplib.send(
                msg,
                hostname=self._config.smtp_host,
                port=self._config.smtp_port,
                username=self._config.smtp_user,
                password=self._config.smtp_password,
                start_tls=True,
            )
            logger.info("Email sent: %s", subject)
        except Exception:
            logger.exception("Failed to send email: %s", subject)
