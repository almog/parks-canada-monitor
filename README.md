# Parks Canada Backcountry Permit Monitor

Watches for cancelled backcountry camping permits on Parks Canada's reservation system and alerts you instantly via email. Designed for thru-hikers who need dozens of permits across multiple parks (e.g., the Great Divide Trail).

Parks Canada uses the GoingToCamp platform at `reservation.pc.gc.ca`. Permits sell out within minutes of opening day each January. The only way to get them after that is to catch cancellations — this tool does that automatically.

## Quick Start

```bash
# Clone and install
git clone <repo-url> && cd parks-canada-monitor
uv sync

# Find campground IDs
uv run parks-monitor discover --park banff

# Set up config files (see below)
cp config.example.yaml config.yaml
cp watchlist.example.yaml watchlist.yaml
# Edit both files with your settings

# Run a one-time check
uv run parks-monitor check

# Start continuous monitoring
uv run parks-monitor run
```

## Finding Campsite Names

Before setting up your watchlist, you need to know the campsite names. Use the `discover` command:

```bash
# List all backcountry locations and campsites
parks-monitor discover

# Filter to a specific park
parks-monitor discover --park jasper

# Waterton has only 16 campsites
parks-monitor discover --park waterton
```

This prints a table of all backcountry campsites and their names. Copy the exact campsite name into your watchlist's `campsites` field.

## Configuration

### config.yaml

```yaml
monitor:
  poll_interval_minutes: 10     # how often to check (minutes)
  jitter_seconds: 30            # random ± jitter added to interval

parks_canada:
  base_url: "https://reservation.pc.gc.ca"

notifications:
  email:
    enabled: true
    smtp_host: "smtp.gmail.com"
    smtp_port: 587
    smtp_user: "${SMTP_USER}"           # interpolated from environment
    smtp_password: "${SMTP_PASSWORD}"   # interpolated from environment
    from_address: "you@gmail.com"
    to_addresses:
      - "you@gmail.com"
  dedup_hours: 4                # don't re-alert same opening within this window

auto_book:
  enabled: false    # Phase 2 — not yet implemented
  dry_run: true
  daily_limit: 3
```

Credentials use `${VAR}` syntax and are interpolated from environment variables. Put them in a `.env` file (never commit this):

```
SMTP_USER=you@gmail.com
SMTP_PASSWORD=your-app-password
```

For Gmail, use an [App Password](https://support.google.com/accounts/answer/185833) — not your regular password.

### watchlist.yaml

```yaml
entries:
  - name: "Egypt Lake - Banff"
    campground: "Egypt Lake"
    campsites: ["Egypt Lake - E13"]
    date_ranges:
      - start: "2026-07-01"
        end: "2026-07-03"
    flexibility_days: 2       # also check ±2 days from these dates
    party_size: 1

  - name: "Jasper - Athabasca Pass"
    campground: "Athabasca Pass"
    campsites: ["61 - Athabasca Pass", "58 - Middle Forks", "59 - Scott Camp"]
    date_ranges:
      - start: "2026-07-10"
        end: "2026-07-12"
    flexibility_days: 1
    party_size: 1
```

Use campsite names from `parks-monitor discover` in the `campsites` field. You can also use numeric `resource_ids` directly if you prefer.

| Field | Required | Default | Description |
|---|---|---|---|
| `name` | yes | — | Display name (used in emails and logs) |
| `campground` | yes | — | Campground name (for reference) |
| `campsites` | no | `[]` | Campsite names from `parks-monitor discover` (resolved to IDs automatically) |
| `resource_ids` | no | `[]` | Numeric resource IDs (alternative to `campsites`) |
| `date_ranges` | yes | — | List of `{start, end}` date ranges (YYYY-MM-DD) |
| `flexibility_days` | no | `0` | Expand each date range by ±N days |
| `party_size` | no | `1` | Number of people |
| `auto_book` | no | `false` | Auto-book when available (not yet implemented) |
| `priority` | no | `"medium"` | `high`, `medium`, or `low` |

At least one of `campsites` or `resource_ids` is required per entry.

The watchlist is hot-reloaded — edit it while the monitor is running and changes take effect on the next poll cycle. No restart needed.

## CLI Commands

### `parks-monitor run`

Starts the continuous monitoring loop. Polls Parks Canada every N minutes and sends email alerts when watched sites become available.

```bash
parks-monitor run                                  # defaults
parks-monitor run -c my-config.yaml -w my-list.yaml  # custom paths
parks-monitor run -v                                 # verbose logging
```

### `parks-monitor check`

Runs a single poll cycle and prints current availability to the terminal. Useful for testing your watchlist without starting the full loop.

```bash
parks-monitor check
parks-monitor check -w watchlist.yaml -v
```

### `parks-monitor discover`

Lists backcountry campgrounds and their resource IDs from the Parks Canada API.

```bash
parks-monitor discover                # all backcountry locations
parks-monitor discover --park banff   # filter by park name
```

### Common options

| Flag | Short | Description |
|---|---|---|
| `--config` | `-c` | Path to config.yaml (default: `config.yaml`) |
| `--watchlist` | `-w` | Path to watchlist.yaml (default: `watchlist.yaml`) |
| `--verbose` | `-v` | Enable debug logging |

## How It Works

1. Each poll cycle, the monitor queries `/api/availability/resourcedailyavailability` for every resource ID in your watchlist (including flexibility-expanded dates)
2. Results are compared against the previous cycle's state
3. If a site transitions from **unavailable to available**, an email is sent with the campsite name, dates, and a link to the booking page
4. Duplicate alerts for the same opening are suppressed within the dedup window (default 4 hours)
5. Requests are made sequentially with 1-3 second random delays between entries to avoid rate limiting

The first cycle is always a baseline — it records current state but sends no notifications, so you won't get flooded with alerts on startup.

## Development

```bash
# Install with all dev dependencies
uv sync --all-extras

# Run tests
uv run pytest

# Run tests with verbose output
uv run pytest -v

# Run a specific test file
uv run pytest tests/test_monitor.py

# Lint
uv run ruff check src/ tests/
```

### Project Structure

```
src/parks_monitor/
  config.py      Config models + YAML loading
  state.py       In-memory availability state + dedup
  client.py      Parks Canada API client (httpx async)
  monitor.py     Availability checker + diff + poll loop
  notify.py      Email notifier (aiosmtplib)
  resolver.py    Resource ID <-> campsite name resolution
  cli.py         Typer CLI commands
  data/          Bundled campsite name mappings (415 resources)

tests/
  test_config.py       Config loading + validation
  test_state.py        State transitions + dedup logic
  test_client.py       API client with mocked HTTP (respx)
  test_monitor.py      Poll cycle with fake client/notifier
  test_notify.py       Email formatting + error handling
  test_cli.py          CLI commands
  test_integration.py  Multi-cycle lifecycle tests
  fixtures/            Recorded API responses from Parks Canada
```

## Requirements

- [uv](https://docs.astral.sh/uv/) (or Python 3.11+ with pip)
- A Gmail account (or other SMTP provider) for email notifications
- The campsite names you want to monitor (use `parks-monitor discover` to browse)
