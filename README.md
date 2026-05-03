# Parks Canada Backcountry Permit Monitor

Watches for cancelled backcountry camping permits on Parks Canada's reservation system and surfaces new openings in the log (WARNING level). Designed for thru-hikers who need dozens of permits across multiple parks (e.g., the Great Divide Trail).

Parks Canada uses the GoingToCamp platform at `reservation.pc.gc.ca`. Permits sell out within minutes of opening day each January. The only way to get them after that is to catch cancellations — this tool does that automatically.

Current bundled campsite names cover Banff, Kootenay and Yoho, Jasper, and Waterton Lakes backcountry. The monitor is not inherently tied to only those parks, but new Parks Canada locations need a verified resource mapping before they are usable by name. This tool is for backcountry permits, not frontcountry campground reservations.

## Quick Start

```bash
# Clone and install
git clone https://github.com/almog/parks-canada-monitor && cd parks-canada-monitor
uv sync

# Find exact campsite names
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

# Show only GDT-corridor sites in Jasper
parks-monitor discover --park jasper --gdt

# Waterton has only 16 campsites
parks-monitor discover --park waterton
```

This prints a table of all backcountry campsites and their names. Copy the exact campsite name into your watchlist's `campsites` field.

Example output:

```text
               Jasper - Backcountry [GDT]
┏━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━┓
┃ Campsite Name       ┃ Type       ┃ Resource ID ┃
┡━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━┩
│ 45 - Surprise Point │ designated │ -2147483257 │
│ 46 - Amethyst       │ designated │ -2147483186 │
│ 47 - Maccarib       │ designated │ -2147483278 │
└─────────────────────┴────────────┴─────────────┘
```

You can also add entries without hand-editing YAML:

```bash
parks-monitor watchlist add "Egypt Lake - E13" \
  --start 2026-07-15 \
  --end 2026-07-17 \
  --party 2

parks-monitor watchlist list
```

## Configuration

### config.yaml

```yaml
monitor:
  poll_interval_minutes: 10     # how often to check (minutes)
  jitter_seconds: 30            # random ± jitter added to interval
  dedup_hours: 4                # don't re-log the same opening within this window

parks_canada:
  base_url: "https://reservation.pc.gc.ca"

notifications:
  ntfy_topic: ""                # set to your topic name to enable push notifications
  ntfy_url: "https://ntfy.sh"  # override for a self-hosted ntfy server

auto_book:
  enabled: false    # planned — not implemented in the current release
  dry_run: true
  daily_limit: 3
```

Environment variable interpolation with `${VAR}` syntax is supported in any string value if you ever need it.

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
| `name` | yes | — | Display name (used in log lines) |
| `campground` | yes | — | Campground name (for reference) |
| `campsites` | no | `[]` | Campsite names from `parks-monitor discover` (resolved to IDs automatically) |
| `resource_ids` | no | `[]` | Numeric resource IDs (alternative to `campsites`) |
| `date_ranges` | yes | — | List of `{start, end}` date ranges (YYYY-MM-DD) |
| `flexibility_days` | no | `0` | Expand each date range by ±N days |
| `party_size` | no | `1` | Number of people to ask Parks Canada about |
| `auto_book` | no | `false` | Auto-book when available (not yet implemented) |
| `priority` | no | `"medium"` | `high`, `medium`, or `low` |

At least one of `campsites` or `resource_ids` is required per entry.

`party_size` is passed through to Parks Canada's availability endpoint. The monitor treats a response as "available" only when Parks Canada says the site is bookable for the requested party size; it does not interpret `availability: 1` as "one person" or "one remaining spot".

The watchlist is hot-reloaded — edit it while the monitor is running and changes take effect on the next poll cycle. No restart needed.

## CLI Commands

### `parks-monitor run`

Starts the continuous monitoring loop. Polls Parks Canada every N minutes and emits a `NEW OPENING` log line at WARNING level when a watched site transitions from booked to available.

```bash
parks-monitor run                                  # defaults
parks-monitor run -c my-config.yaml -w my-list.yaml  # custom paths
parks-monitor run -v                                 # verbose logging
parks-monitor run -w watchlist-watch.yaml --log-file monitor.log
```

The first monitor cycle records current availability as a baseline and does not send alerts. Alerts only happen when a watched site changes from unavailable to available after that baseline.

Example log line:

```text
NEW OPENING: Tonquin Valley — resource -2147483186 — 2026-07-15 → 2026-07-17
```

### `parks-monitor check`

Runs a single poll cycle and prints current availability to the terminal. Useful for testing your watchlist without starting the full loop.

```bash
parks-monitor check
parks-monitor check -w watchlist.yaml -v
```

Example output:

```text
                Tonquin Valley
┏━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━┓
┃ Campsite      ┃ Date       ┃ Available ┃
┡━━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━┩
│ 46 - Amethyst │ 2026-07-15 │ no        │
│ 46 - Amethyst │ 2026-07-16 │ YES       │
└───────────────┴────────────┴───────────┘
```

### `parks-monitor discover`

Lists bundled backcountry campsite names and resource IDs. Availability checks still use the live Parks Canada API.

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

## Notifications (ntfy.sh)

Get push notifications on your phone the moment a permit opens up.

1. Install the [ntfy app](https://ntfy.sh/) on iOS or Android (free)
2. Pick a topic name — this is like a private channel, so make it hard to guess (e.g. `parks-monitor-x7k2m`)
3. Tap **Subscribe** in the app and enter your topic name
4. Add to `config.yaml`:

```yaml
notifications:
  ntfy_topic: "parks-monitor-x7k2m"
```

That's it. When a watched site opens up you'll get a high-priority push notification with the site name and dates. The monitor also continues logging at WARNING level regardless.

> **Keeping the topic secret:** `config.yaml` is gitignored so it won't be committed accidentally. If you prefer to keep the config file in version control, use environment variable interpolation instead:
> ```yaml
> notifications:
>   ntfy_topic: "${NTFY_TOPIC}"
> ```
> Then `export NTFY_TOPIC=parks-monitor-x7k2m` before running.

> The public ntfy.sh server is free and requires no account. For privacy, you can [self-host ntfy](https://docs.ntfy.sh/install/) and set `ntfy_url` to your server's address.

## Common Mistakes

- Campsite names must match exactly. Use `parks-monitor discover` or `parks-monitor watchlist add` to avoid typos.
- Date ranges are inclusive: `start: 2026-07-15` and `end: 2026-07-17` checks July 15, 16, and 17.
- `config.yaml` may contain your private ntfy topic. It is gitignored for that reason; commit `config.example.yaml`, not your local config.
- Auto-booking is planned but not implemented in the current release. The monitor detects openings and can notify you; it does not reserve permits.
- The first `run` cycle is baseline-only. Use `parks-monitor check` if you want to inspect current availability before starting the long-running monitor.

## How It Works

1. Each poll cycle, the monitor queries `/api/availability/resourcedailyavailability` for every resource ID in your watchlist (including flexibility-expanded dates)
2. Results are compared against the previous cycle's state
3. If a site transitions from **unavailable to available**, a `NEW OPENING` line is logged at WARNING level with the entry name, resource ID, and date runs (consecutive dates merged into ranges)
4. Duplicate log lines for the same opening are suppressed within the dedup window (default 4 hours)
5. Requests are made sequentially with 1-3 second random delays between entries to avoid rate limiting

The first cycle is always a baseline — it records current state but emits no opening logs, so you won't get spammed on startup.

## Development

```bash
# Install with all dev dependencies
uv sync --all-extras

# Run tests
uv run pytest

# Lint
uv run ruff check src/ tests/
```

## Requirements

- [uv](https://docs.astral.sh/uv/) (or Python 3.11+ with pip)
- The campsite names you want to monitor (use `parks-monitor discover` to browse)
