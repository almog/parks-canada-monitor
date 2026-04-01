from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class State:
    last_availability: dict[str, bool] = field(default_factory=dict)
    last_notified: dict[str, datetime] = field(default_factory=dict)
    bookings_today: list = field(default_factory=list)
    last_poll_at: datetime | None = None

    def is_new_opening(self, key: str, available: bool) -> bool:
        """True if this was previously unavailable and is now available."""
        prev = self.last_availability.get(key)
        if prev is None:
            return False  # baseline — don't notify on first check
        return available and not prev

    def should_notify(self, key: str, dedup_hours: int = 4) -> bool:
        """True if enough time has passed since last notification for this key."""
        last = self.last_notified.get(key)
        if last is None:
            return True
        return datetime.now() - last > timedelta(hours=dedup_hours)
