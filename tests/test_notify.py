import pytest
import httpx

from parks_monitor.monitor import AvailabilityChange, DateRun
from parks_monitor.notify import NtfyNotifier


def _change(entry="Test Entry", resource_id=-2147483054, start="2026-07-15", end="2026-07-15"):
    from datetime import date
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    return AvailabilityChange(entry_name=entry, resource_id=resource_id, runs=[DateRun(s, e)])


@pytest.mark.respx(base_url="https://ntfy.sh")
async def test_send_posts_to_topic(respx_mock):
    route = respx_mock.post("/my-topic").mock(return_value=httpx.Response(200))
    async with httpx.AsyncClient() as http:
        notifier = NtfyNotifier(http, "my-topic")
        await notifier.send(_change())
    assert route.called
    request = route.calls[0].request
    assert b"2026-07-15" in request.content
    assert request.headers["title"] == "Permit opened: Test Entry"
    assert request.headers["priority"] == "high"


@pytest.mark.respx(base_url="https://ntfy.sh")
async def test_send_date_range_in_message(respx_mock):
    from datetime import date
    route = respx_mock.post("/topic").mock(return_value=httpx.Response(200))
    change = AvailabilityChange(
        entry_name="E",
        resource_id=-2147483054,
        runs=[DateRun(date(2026, 7, 15), date(2026, 7, 17))],
    )
    async with httpx.AsyncClient() as http:
        await NtfyNotifier(http, "topic").send(change)
    body = route.calls[0].request.content.decode()
    assert "2026-07-15" in body
    assert "2026-07-17" in body


@pytest.mark.respx(base_url="https://custom.ntfy.example")
async def test_send_custom_base_url(respx_mock):
    route = respx_mock.post("/mytopic").mock(return_value=httpx.Response(200))
    async with httpx.AsyncClient() as http:
        await NtfyNotifier(http, "mytopic", "https://custom.ntfy.example").send(_change())
    assert route.called


@pytest.mark.respx(base_url="https://ntfy.sh")
async def test_send_http_error_does_not_raise(respx_mock):
    respx_mock.post("/topic").mock(return_value=httpx.Response(500))
    async with httpx.AsyncClient() as http:
        # Should log but not raise
        await NtfyNotifier(http, "topic").send(_change())


async def test_send_network_error_does_not_raise():
    async with httpx.AsyncClient() as http:
        notifier = NtfyNotifier(http, "topic", "https://127.0.0.1:1")
        # Connection refused — should log but not raise
        await notifier.send(_change())
