from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from parks_monitor.config import EmailConfig, WatchlistEntry
from parks_monitor.monitor import AvailabilityChange
from parks_monitor.notify import EmailNotifier


def _make_config(**overrides):
    defaults = dict(
        enabled=True,
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="user@example.com",
        smtp_password="secret",
        from_address="from@example.com",
        to_addresses=["to@example.com"],
    )
    defaults.update(overrides)
    return EmailConfig(**defaults)


def _make_change():
    return AvailabilityChange(
        entry_name="Egypt Lake",
        resource_id=-2147483137,
        site_date=date(2026, 7, 2),
        is_available=True,
    )


def _make_entry():
    return WatchlistEntry(
        name="Egypt Lake",
        campground="Egypt Lake",
        resource_ids=[-2147483137],
        date_ranges=[{"start": "2026-07-01", "end": "2026-07-03"}],
    )


@patch("parks_monitor.notify.aiosmtplib.send", new_callable=AsyncMock)
async def test_sends_email(mock_send):
    notifier = EmailNotifier(_make_config())
    await notifier.notify(_make_change(), _make_entry())

    mock_send.assert_called_once()
    msg = mock_send.call_args[0][0]
    assert "Egypt Lake" in msg["Subject"]
    assert "Jul 02" in msg["Subject"]
    assert "to@example.com" in msg["To"]
    body = msg.get_content()
    assert "reservation.pc.gc.ca" in body
    assert "Castle Mountain Hut Random" in body


@patch("parks_monitor.notify.aiosmtplib.send", new_callable=AsyncMock)
async def test_disabled_does_not_send(mock_send):
    notifier = EmailNotifier(_make_config(enabled=False))
    await notifier.notify(_make_change(), _make_entry())
    mock_send.assert_not_called()


@patch("parks_monitor.notify.aiosmtplib.send", new_callable=AsyncMock)
async def test_smtp_failure_does_not_raise(mock_send):
    mock_send.side_effect = ConnectionError("SMTP down")
    notifier = EmailNotifier(_make_config())
    # Should not raise
    await notifier.notify(_make_change(), _make_entry())


@patch("parks_monitor.notify.aiosmtplib.send", new_callable=AsyncMock)
async def test_email_body_contains_entry_details(mock_send):
    notifier = EmailNotifier(_make_config())
    await notifier.notify(_make_change(), _make_entry())

    msg = mock_send.call_args[0][0]
    body = msg.get_content()
    assert "Egypt Lake" in body
    assert "2026-07-02" in body
    assert "Party size" in body or "party_size" in body.lower()
