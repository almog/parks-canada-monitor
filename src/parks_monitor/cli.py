from __future__ import annotations

import asyncio
import logging
import os
from datetime import date
from logging.handlers import RotatingFileHandler
from pathlib import Path

import httpx
import typer
import yaml
from rich.console import Console
from rich.table import Table

from parks_monitor import __version__
from parks_monitor.client import GoingToCampClient
from parks_monitor.config import load_config, load_watchlist
from parks_monitor.monitor import check_entry, poll_loop
from parks_monitor.notify import NtfyNotifier
from parks_monitor.resolver import (
    campsite_names,
    classify_type,
    is_gdt_site,
    locations as resolver_locations,
    resolve_id,
    resolve_ids,
    resolve_name,
)
from parks_monitor.state import State

app = typer.Typer(name="parks-monitor", help="Parks Canada backcountry permit monitor")
watchlist_app = typer.Typer(help="Manage your watchlist")
app.add_typer(watchlist_app, name="watchlist")

console = Console()


def _version_callback(value: bool):
    if value:
        console.print(f"parks-monitor {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool | None = typer.Option(
        None, "--version", callback=_version_callback, is_eager=True,
        help="Show version and exit.",
    ),
):
    """Parks Canada backcountry permit monitor."""


def _setup_logging(verbose: bool = False, log_file: Path | None = None) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(level)
    # Clear any handlers a prior call installed (relevant in tests / repeated CLI invocations).
    for h in list(root.handlers):
        root.removeHandler(h)

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    root.addHandler(stream)

    if log_file is not None:
        # 10 MB per file, keep 5 old files → ~60 MB cap for a long-running monitor.
        rotating = RotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8",
        )
        rotating.setFormatter(fmt)
        root.addHandler(rotating)


def _default_config_path() -> Path:
    return Path("config.yaml")


def _default_watchlist_path() -> Path:
    return Path("watchlist.yaml")


def _complete_campsite(incomplete: str) -> list[str]:
    """Shell completion callback for campsite names."""
    return [n for n in campsite_names() if incomplete.lower() in n.lower()]


def _complete_campsite_gdt(incomplete: str) -> list[str]:
    """Shell completion for GDT campsite names only."""
    return [n for n in campsite_names(gdt_only=True) if incomplete.lower() in n.lower()]


def _atomic_write_yaml(path: Path, data: dict) -> None:
    # Write to a temp file in the same directory, then rename — avoids a
    # concurrent `run` reader observing a half-written file.
    text = yaml.dump(
        data, default_flow_style=False, allow_unicode=True, sort_keys=False, width=120
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


# ── run ──────────────────────────────────────────────────────────────────────

@app.command()
def run(
    config_path: Path = typer.Option(_default_config_path, "--config", "-c"),
    watchlist_path: Path = typer.Option(_default_watchlist_path, "--watchlist", "-w"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    log_file: Path | None = typer.Option(
        None, "--log-file", "-l",
        help="Write logs to this file with 10MB rotation (keeps 5 backups).",
    ),
):
    """Start the monitoring loop."""
    _setup_logging(verbose, log_file)
    if not config_path.exists():
        console.print(f"[red]Config file not found: {config_path}[/red]")
        raise typer.Exit(1)
    if not watchlist_path.exists():
        console.print(f"[red]Watchlist file not found: {watchlist_path}[/red]")
        raise typer.Exit(1)

    config = load_config(config_path)
    state = State()

    interval = config.monitor.poll_interval_minutes
    console.print(f"[green]Starting monitor (poll every {interval} min)[/green]")
    ntfy_topic = config.notifications.ntfy_topic
    if ntfy_topic:
        console.print(f"[green]Notifications → ntfy.sh/{ntfy_topic}[/green]")

    async def _run():
        async with httpx.AsyncClient() as http:
            client = GoingToCampClient(http, config.parks_canada.base_url)
            notifier = (
                NtfyNotifier(http, ntfy_topic, config.notifications.ntfy_url)
                if ntfy_topic else None
            )
            await poll_loop(client, watchlist_path, state, config.monitor, notifier=notifier)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped.[/yellow]")


# ── check ─────────────────────────────────────────────────────────────────────

@app.command()
def check(
    config_path: Path = typer.Option(_default_config_path, "--config", "-c"),
    watchlist_path: Path = typer.Option(_default_watchlist_path, "--watchlist", "-w"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Print current availability for each watchlist entry and exit.

    Does not update state. Read-only sanity check against the API.
    """
    _setup_logging(verbose)
    if not config_path.exists():
        console.print(f"[red]Config file not found: {config_path}[/red]")
        raise typer.Exit(1)
    if not watchlist_path.exists():
        console.print(f"[red]Watchlist file not found: {watchlist_path}[/red]")
        raise typer.Exit(1)

    config = load_config(config_path)
    watchlist = load_watchlist(watchlist_path)

    async def _check():
        async with httpx.AsyncClient() as http:
            client = GoingToCampClient(http, config.parks_canada.base_url)
            for entry in watchlist.entries:
                results = await check_entry(client, entry)
                table = Table(title=entry.name)
                table.add_column("Campsite")
                table.add_column("Date")
                table.add_column("Available")
                for _key, rid, site_date, available in results:
                    status = "[green]YES[/green]" if available else "[red]no[/red]"
                    table.add_row(resolve_name(rid), site_date.isoformat(), status)
                console.print(table)

    asyncio.run(_check())


# ── discover ──────────────────────────────────────────────────────────────────

@app.command()
def discover(
    park: str = typer.Option("", "--park", "-p", help="Filter by park name substring"),
    gdt: bool = typer.Option(False, "--gdt", help="Show only GDT-corridor sites"),
    site_type: str | None = typer.Option(
        None, "--type", "-t",
        help="Filter by type: designated, random, hut, trailhead, horse, access, out_of_park",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """List backcountry campsite names (use these in your watchlist)."""
    _setup_logging(verbose)

    locs = resolver_locations()

    if park:
        locs = [loc for loc in locs if park.lower() in loc["display_name"].lower()]

    if not locs:
        console.print(f"[red]No backcountry locations matching '{park}'[/red]")
        raise typer.Exit(1)

    for loc in locs:
        rows = []
        for rid, name in sorted(loc["resources"].items(), key=lambda x: x[1]):
            t = classify_type(name)
            if site_type and t != site_type:
                continue
            if gdt and not is_gdt_site(rid):
                continue
            rows.append((name, t, rid))

        if not rows:
            continue

        title = loc["display_name"]
        if gdt:
            title += " [GDT]"
        table = Table(title=title)
        table.add_column("Campsite Name")
        table.add_column("Type", style="dim")
        table.add_column("Resource ID", style="dim")
        for name, t, rid in rows:
            table.add_row(name, t, str(rid))
        console.print(table)
        console.print()


# ── watchlist subcommands ─────────────────────────────────────────────────────

@watchlist_app.command("list")
def watchlist_list(
    watchlist_path: Path = typer.Option(_default_watchlist_path, "--watchlist", "-w"),
):
    """Show current watchlist entries."""
    if not watchlist_path.exists():
        console.print(f"[yellow]No watchlist found at {watchlist_path}[/yellow]")
        raise typer.Exit(0)

    wl = load_watchlist(watchlist_path)
    if not wl.entries:
        console.print("[yellow]Watchlist is empty.[/yellow]")
        return

    table = Table(title=str(watchlist_path))
    table.add_column("#", style="dim")
    table.add_column("Name")
    table.add_column("Campsites")
    table.add_column("Dates")
    table.add_column("Party")

    for i, entry in enumerate(wl.entries):
        site_names = [resolve_name(r) for r in entry.resource_ids]
        dates = "; ".join(
            f"{dr.start} → {dr.end}" for dr in entry.date_ranges
        )
        table.add_row(
            str(i),
            entry.name,
            "\n".join(site_names),
            dates,
            str(entry.party_size),
        )
    console.print(table)


@watchlist_app.command("add")
def watchlist_add(
    campsite: str = typer.Argument(
        ...,
        help="Campsite name (tab-complete to browse). Run 'parks-monitor discover' to see all names.",
        autocompletion=_complete_campsite,
    ),
    start: str = typer.Option(..., "--start", "-s", help="Start date YYYY-MM-DD"),
    end: str = typer.Option(..., "--end", "-e", help="End date YYYY-MM-DD"),
    name: str = typer.Option("", "--name", "-n", help="Watchlist entry name (defaults to campsite name)"),
    party_size: int = typer.Option(1, "--party", "-p", help="Party size"),
    flexibility: int = typer.Option(0, "--flex", "-f", help="Flexibility ±days around date range"),
    watchlist_path: Path = typer.Option(_default_watchlist_path, "--watchlist", "-w"),
):
    """Add a campsite to the watchlist.

    Examples:
      parks-monitor watchlist add "Egypt Lake - E13" --start 2026-07-01 --end 2026-07-03
      parks-monitor watchlist add "28 - Little Shovel" -s 2026-07-10 -e 2026-07-12 --flex 2
    """
    # Validate campsite exists (exact match only)
    rid = resolve_id(campsite)
    if rid is None:
        suggestions = [resolve_name(r) for r in resolve_ids(campsite)[:5]]
        if suggestions:
            console.print(f"[red]No exact match for '{campsite}'.[/red]")
            console.print(f"Did you mean: {', '.join(suggestions)}?")
        else:
            console.print(f"[red]No campsite found matching '{campsite}'[/red]")
            console.print("Run [bold]parks-monitor discover[/bold] to browse campsite names.")
        raise typer.Exit(1)

    # Validate dates
    try:
        date.fromisoformat(start)
        date.fromisoformat(end)
    except ValueError as e:
        console.print(f"[red]Invalid date: {e}[/red]")
        raise typer.Exit(1)

    entry_name = name or campsite

    new_entry = {
        "name": entry_name,
        "campground": campsite,
        "campsites": [campsite],
        "date_ranges": [{"start": start, "end": end}],
        "party_size": party_size,
    }
    if flexibility:
        new_entry["flexibility_days"] = flexibility

    # Load existing watchlist or create empty one
    if watchlist_path.exists():
        existing = yaml.safe_load(watchlist_path.read_text()) or {}
    else:
        existing = {}

    entries = existing.get("entries", [])

    for existing_entry in entries:
        same_site = campsite in (existing_entry.get("campsites") or [])
        same_dates = any(
            dr.get("start") == start and dr.get("end") == end
            for dr in existing_entry.get("date_ranges") or []
        )
        if same_site and same_dates:
            console.print(
                f"[yellow]Duplicate: '{campsite}' {start} → {end} "
                f"already exists as '{existing_entry.get('name')}'.[/yellow]"
            )
            raise typer.Exit(1)

    entries.append(new_entry)
    existing["entries"] = entries

    _atomic_write_yaml(watchlist_path, existing)

    console.print(f"[green]Added:[/green] {entry_name}")
    console.print(f"  Campsite: {resolve_name(rid)}")
    console.print(f"  Dates: {start} → {end}" + (f" (±{flexibility} days)" if flexibility else ""))
    console.print(f"  Party size: {party_size}")
    console.print(f"  Watchlist: {watchlist_path}")


@watchlist_app.command("remove")
def watchlist_remove(
    index: int = typer.Argument(..., help="Entry index from 'parks-monitor watchlist list'"),
    watchlist_path: Path = typer.Option(_default_watchlist_path, "--watchlist", "-w"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Remove a watchlist entry by index."""
    if not watchlist_path.exists():
        console.print(f"[red]No watchlist found at {watchlist_path}[/red]")
        raise typer.Exit(1)

    data = yaml.safe_load(watchlist_path.read_text()) or {}
    entries = data.get("entries", [])

    if not entries:
        console.print("[red]Watchlist is empty — nothing to remove.[/red]")
        raise typer.Exit(1)
    if index < 0 or index >= len(entries):
        console.print(f"[red]Index {index} out of range (0–{len(entries)-1})[/red]")
        raise typer.Exit(1)

    entry = entries[index]
    entry_name = entry.get("name", f"entry #{index}")

    if not yes:
        typer.confirm(f"Remove '{entry_name}'?", abort=True)

    entries.pop(index)
    data["entries"] = entries
    _atomic_write_yaml(watchlist_path, data)
    console.print(f"[green]Removed:[/green] {entry_name}")
