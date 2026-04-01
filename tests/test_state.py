from datetime import datetime, timedelta

from parks_monitor.state import State


def test_is_new_opening_baseline():
    """First check for a key should return False (don't alert on initial scan)."""
    state = State()
    assert state.is_new_opening("site-1::2026-07-01", True) is False


def test_is_new_opening_unavail_to_avail():
    state = State()
    state.last_availability["site-1::2026-07-01"] = False
    assert state.is_new_opening("site-1::2026-07-01", True) is True


def test_is_new_opening_avail_to_avail():
    state = State()
    state.last_availability["site-1::2026-07-01"] = True
    assert state.is_new_opening("site-1::2026-07-01", True) is False


def test_is_new_opening_avail_to_unavail():
    state = State()
    state.last_availability["site-1::2026-07-01"] = True
    assert state.is_new_opening("site-1::2026-07-01", False) is False


def test_should_notify_first_time():
    state = State()
    assert state.should_notify("site-1::2026-07-01") is True


def test_should_notify_within_window():
    state = State()
    state.last_notified["site-1::2026-07-01"] = datetime.now() - timedelta(hours=1)
    assert state.should_notify("site-1::2026-07-01", dedup_hours=4) is False


def test_should_notify_past_window():
    state = State()
    state.last_notified["site-1::2026-07-01"] = datetime.now() - timedelta(hours=5)
    assert state.should_notify("site-1::2026-07-01", dedup_hours=4) is True
