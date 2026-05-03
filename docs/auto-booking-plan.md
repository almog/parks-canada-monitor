# Auto-Booking Implementation Plan

## Context

The parks-monitor tool detects backcountry permit cancellations on Parks Canada (GoingToCamp platform) and sends push notifications via ntfy.sh. Monitoring is fully working. The next step is **auto-booking**: when a watched campsite becomes available, automatically complete the booking via browser automation, then notify the user of the result.

The user has ~20+ permits needed for a GDT thru-hike in summer 2026. Openings can happen at any time — auto-booking catches them even while the user is asleep.

**Three user-facing modes per watchlist entry:**
- `auto_book: false` (default) — detect opening, send notification, user books manually
- `auto_book: true` with `dry_run: true` — detect opening, navigate booking flow, screenshot the confirmation page but don't click Confirm, notify
- `auto_book: true` with `dry_run: false` — detect opening, complete booking, notify with confirmation number

---

## Prerequisites

### 0. Create a Parks Canada account & record the booking flow

**This is manual work — no code.** The user has never completed a booking on reservation.pc.gc.ca.

1. Create an account on Parks Canada (GCKey or Google SSO)
2. Open the site with DevTools Network tab recording
3. Walk through a backcountry booking flow (can cancel before paying, or use a throwaway date):
   - Navigate to a backcountry campsite search
   - Click "Book" on an available result
   - Go through cart/itinerary steps
   - Reach the checkout/confirmation page
   - **Record**: every page transition, form field, button click, and POST/PUT API call
4. Save: HAR file, screenshots of each step, notes on CSS selectors for key elements
5. Document in `docs/booking-flow.md`

**Why gating**: Without knowing the exact steps, selectors, and API calls in the booking flow, the Playwright automation would be guesswork. This discovery produces the selector catalog and flow map that `booking/flow.py` implements.

---

## Phase 3a: Session Management

**Goal**: Let users authenticate once via a visible browser, persist the session, and validate it before booking attempts.

### New files
- `src/parks_monitor/booking/__init__.py` — re-export key classes
- `src/parks_monitor/booking/session.py` — SessionManager class

### Modify
- `src/parks_monitor/config.py` — add `session_path` to AutoBookConfig
- `src/parks_monitor/cli.py` — add `login` and `session-check` commands

### Design

```python
# booking/session.py
class SessionManager:
    def __init__(self, state_path: Path, headless: bool = True): ...

    async def launch_for_login(self) -> None:
        # Opens VISIBLE browser → Parks Canada sign-in page
        # User completes login + MFA manually
        # Saves context.storage_state() to state_path (0600 perms)

    async def load_context(self, playwright) -> BrowserContext:
        # Loads saved session into headless context

    async def is_authenticated(self, context: BrowserContext) -> bool:
        # Navigates to a lightweight auth-check URL
        # Returns True if session is still valid

    @property
    def session_exists(self) -> bool: ...
```

**Config addition:**
```python
class AutoBookConfig(BaseModel):
    enabled: bool = False
    dry_run: bool = True
    session_path: str = "~/.parks-monitor/session.json"  # new
```

**CLI commands:**
- `parks-monitor login` — visible browser, manual auth, saves session
- `parks-monitor session-check` — validates saved session, prints result

**Session expiry handling:**
When `is_authenticated()` returns False during a booking attempt:
1. Log WARNING: "Session expired — auto-booking paused. Re-run `parks-monitor login`."
2. Send a notification about the expired session.
3. Skip booking, continue monitoring.

### Tests (`tests/test_session.py`)
- `session_exists` with/without file
- `is_authenticated` with page.route() mocking auth-check response
- No real browser launch in CI

### Depends on: Prerequisite 0 (need to know auth-check URL)

---

## Phase 3b: Booking Flow

**Goal**: Navigate the booking UI from search results through checkout. Screenshot every step. Dry-run stops before clicking Confirm.

### New files
- `src/parks_monitor/booking/result.py` — BookingResult dataclass
- `src/parks_monitor/booking/flow.py` — BookingFlow class

### Design

```python
# booking/result.py
@dataclass
class BookingResult:
    success: bool
    entry_name: str
    resource_id: int
    target_date: date
    confirmation_number: str | None = None
    reason: str | None = None   # "slot_gone", "session_expired", "checkout_error", "dry_run"
    screenshots: list[Path] = field(default_factory=list)
    dry_run: bool = False
```

```python
# booking/flow.py
class BookingFlow:
    def __init__(self, context: BrowserContext, dry_run: bool = True,
                 screenshot_dir: Path | None = None): ...

    async def book(self, resource_id: int, resource_location_id: int,
                   target_date: date, party_size: int = 1) -> BookingResult:
        # 1. Navigate to search results URL (backcountry tab)
        # 2. Screenshot: search_results
        # 3. Find and click target resource's "Book" button
        #    → if not found: return BookingResult(success=False, reason="slot_gone")
        # 4. Navigate through cart/itinerary steps
        # 5. Screenshot each step
        # 6. If dry_run: screenshot confirmation page, return (reason="dry_run")
        # 7. If not dry_run: click Confirm, extract confirmation number
        # 8. Return BookingResult(success=True, confirmation_number=...)
```

**Race condition handling**: The slot might be taken between detection and booking. This produces `BookingResult(success=False, reason="slot_gone")`, not an exception.

**Note**: The exact selectors and flow steps will be filled in after Prerequisite 0.

### Tests (`tests/test_booking_flow.py`)
- Playwright `page.route()` intercepts all network, serves pre-recorded HTML fixtures
- Happy path: search → book → cart → checkout → confirmation
- Slot gone: search page shows site unavailable
- Dry-run: stops before confirm
- Screenshot capture at each step

### Depends on: Phase 3a, Prerequisite 0 (selectors)

---

## Phase 3c: Orchestrator + Poll Loop Integration

**Goal**: Wire auto-booking into the poll loop. When an opening is detected for an `auto_book=true` entry, attempt the booking.

### New files
- `src/parks_monitor/booking/orchestrator.py`

### Modify
- `src/parks_monitor/monitor.py` — `poll_loop` accepts optional booker
- `src/parks_monitor/state.py` — add BookingRecord for audit trail
- `src/parks_monitor/cli.py` — wire orchestrator in `run` command

### Design

```python
# booking/orchestrator.py
class BookingOrchestrator:
    def __init__(self, session: SessionManager, config: AutoBookConfig,
                 notifier: Notifier | None = None): ...

    _lock: asyncio.Lock          # one booking at a time

    async def maybe_book(self, change: AvailabilityChange,
                         entry: WatchlistEntry) -> BookingResult | None:
        # Guards: enabled, session valid
        # Acquire lock → run BookingFlow → record result → notify
```

**Poll loop change** (monitor.py):
```python
async def poll_loop(..., notifier=None, booker=None):
    ...
    changes = await run_cycle(...)

    for change in changes:
        # Always notify about the opening
        if notifier is not None:
            await notifier.send(change)

        # Attempt booking if configured
        if booker is not None:
            entry = _find_entry(watchlist, change.entry_name)
            if entry and entry.auto_book:
                result = await booker.maybe_book(change, entry)
                # Notify about booking result (success/failure)
                if result and notifier is not None:
                    await notifier.send_booking_result(result)
```

**State addition:**
```python
@dataclass
class BookingRecord:
    entry_name: str
    resource_id: int
    target_date: date
    booked_at: datetime
    confirmation_number: str | None = None
```

### Tests (`tests/test_orchestrator.py`)
- Booking skipped when `enabled=False`
- Booking skipped when session expired
- Lock prevents concurrent bookings
- Result notification sent on success and failure

### Depends on: Phase 3a, Phase 3b

---

## Phase 3d: Booking Result Notifications

**Goal**: Extend NtfyNotifier to send booking outcomes without breaking existing `Notifier` protocol.

### Modify
- `src/parks_monitor/notify.py` — add `send_booking_result()` to NtfyNotifier

### Design

```python
class NtfyNotifier:
    # existing send() unchanged

    async def send_booking_result(self, result: BookingResult) -> None:
        if result.success and not result.dry_run:
            title = f"Booked: {result.entry_name}"
            message = f"Confirmation #{result.confirmation_number}\n{resolve_name(result.resource_id)}: {result.target_date}"
            priority, tags = "urgent", "national_park,white_check_mark"
        elif result.dry_run:
            title = f"Dry-run: {result.entry_name}"
            message = f"Would book {resolve_name(result.resource_id)} on {result.target_date}"
            priority, tags = "default", "national_park,test_tube"
        else:
            title = f"Booking failed: {result.entry_name}"
            message = f"{resolve_name(result.resource_id)}: {result.reason}"
            priority, tags = "high", "national_park,x"
        # POST to ntfy (same pattern as send())
```

### Tests
- Additional tests in `tests/test_notify.py` for `send_booking_result()` with success, failure, and dry-run outcomes

### Depends on: Phase 3b (BookingResult)

---

## Implementation Order

```
Prerequisite 0: Manual booking flow discovery
        |
Phase 3a: Session management (login command)
        |
Phase 3b: Booking flow (Playwright automation)
        |
   +----+----+
   |         |
Phase 3c  Phase 3d
Orchestrator  Booking notifications
   |         |
   +----+----+
        |
   Wire together in cli.py
```

Phases 3c and 3d can be built in parallel after 3b, then wired together.

---

## Safety Controls

| Control | Config | Default |
|---|---|---|
| Global kill switch | `auto_book.enabled` | `false` |
| Dry-run mode | `auto_book.dry_run` | `true` |
| Per-entry opt-in | `entries[].auto_book` | `false` |
| Session validation | automatic | check before each attempt |
| Screenshots | automatic | every step saved to `screenshots/` |

All three of `enabled`, `dry_run=false`, and per-entry `auto_book` must be explicitly set for a real booking to happen. Default config produces zero bookings. No daily limit — the watchlist itself defines exactly which bookings are wanted.

---

## What's NOT in this plan

- **httpx API-only booking** — if the discovery phase reveals clean REST endpoints for cart/checkout, we could skip Playwright for booking steps (still need it for login). Deferred to Phase 4.
- **Docker support** — Playwright in Docker requires extra setup. Deferred.
- **Concurrent bookings** — explicitly one-at-a-time via asyncio.Lock.
- **Payment automation** — if checkout requires entering payment info, this needs additional discussion.

---

## Known Fix Needed

`resolver.py:reservation_url()` uses `searchTabGroupId=0` but backcountry needs `searchTabGroupId=1`. Should be fixed as part of Phase 3b.

---

## Verification

After each phase:
- `uv run pytest` — all existing tests still pass
- `uv run pytest tests/test_session.py` (3a), `tests/test_booking_flow.py` (3b), `tests/test_orchestrator.py` (3c)
- `uv run ruff check src/ tests/`

End-to-end (after all phases):
1. `uv run parks-monitor login` — complete auth, verify session saved
2. `uv run parks-monitor session-check` — verify session valid
3. Edit watchlist: set `auto_book: true` on one entry with `dry_run: true`
4. `uv run parks-monitor run` — wait for an opening, verify dry-run booking screenshots + notification
5. Flip `dry_run: false` and test with a real cancellation (cancel your own permit, then watch the monitor book it back)

## First Step

Before any code: the user needs to create a Parks Canada account (if they don't have one), walk through a booking with DevTools open, and document the flow. Save the HAR file and share it so we can extract the exact selectors and API calls.
