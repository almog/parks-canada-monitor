from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.table import Table

from parks_monitor.client import GoingToCampClient
from parks_monitor.config import load_config, load_watchlist
from parks_monitor.monitor import AvailabilityChange, check_entry, poll_loop, run_cycle
from parks_monitor.notify import EmailNotifier
from parks_monitor.resolver import resolve_name, locations as resolver_locations
from parks_monitor.state import State

app = typer.Typer(name="parks-monitor", help="Parks Canada backcountry permit monitor")
console = Console()


def _setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _default_config_path() -> Path:
    return Path("config.yaml")


def _default_watchlist_path() -> Path:
    return Path("watchlist.yaml")


@app.command()
def run(
    config_path: Path = typer.Option(_default_config_path, "--config", "-c"),
    watchlist_path: Path = typer.Option(_default_watchlist_path, "--watchlist", "-w"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Start the monitoring loop."""
    _setup_logging(verbose)
    if not config_path.exists():
        console.print(f"[red]Config file not found: {config_path}[/red]")
        raise typer.Exit(1)
    if not watchlist_path.exists():
        console.print(f"[red]Watchlist file not found: {watchlist_path}[/red]")
        raise typer.Exit(1)

    config = load_config(config_path)
    notifier = EmailNotifier(config.notifications.email)
    state = State()

    console.print(f"[green]Starting monitor (poll every {config.monitor.poll_interval_minutes} min)[/green]")

    async def _run():
        async with httpx.AsyncClient() as http:
            client = GoingToCampClient(http, config.parks_canada.base_url)
            await poll_loop(
                client,
                watchlist_path,
                state,
                notifier,
                config.monitor,
                dedup_hours=config.notifications.dedup_hours,
            )

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped.[/yellow]")


@app.command()
def check(
    config_path: Path = typer.Option(_default_config_path, "--config", "-c"),
    watchlist_path: Path = typer.Option(_default_watchlist_path, "--watchlist", "-w"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Run a single poll cycle and exit."""
    _setup_logging(verbose)
    if not config_path.exists():
        console.print(f"[red]Config file not found: {config_path}[/red]")
        raise typer.Exit(1)
    if not watchlist_path.exists():
        console.print(f"[red]Watchlist file not found: {watchlist_path}[/red]")
        raise typer.Exit(1)

    config = load_config(config_path)
    watchlist = load_watchlist(watchlist_path)
    state = State()

    class PrintNotifier:
        async def notify(self, change: AvailabilityChange, entry):
            console.print(
                f"[green bold]AVAILABLE[/green bold] {change.entry_name} "
                f"resource={change.resource_id} date={change.site_date}"
            )

    async def _check():
        async with httpx.AsyncClient() as http:
            client = GoingToCampClient(http, config.parks_canada.base_url)
            # Run two cycles: first to set baseline, second to detect changes
            # For a single check, just show current availability
            for entry in watchlist.entries:
                results = await check_entry(client, entry)
                table = Table(title=entry.name)
                table.add_column("Campsite")
                table.add_column("Date")
                table.add_column("Available")
                for key, rid, site_date, available in results:
                    status = "[green]YES[/green]" if available else "[red]no[/red]"
                    table.add_row(resolve_name(rid), site_date.isoformat(), status)
                console.print(table)

    asyncio.run(_check())


@app.command()
def discover(
    park: str = typer.Option("", "--park", "-p", help="Filter by park name"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """List backcountry campgrounds and their campsite names."""
    _setup_logging(verbose)

    locs = resolver_locations()

    if park:
        locs = [loc for loc in locs if park.lower() in loc["display_name"].lower()]

    if not locs:
        console.print(f"[red]No backcountry locations matching '{park}'[/red]")
        raise typer.Exit(1)

    for loc in locs:
        table = Table(title=loc["display_name"])
        table.add_column("Campsite Name")
        table.add_column("Resource ID", style="dim")
        for rid, name in sorted(loc["resources"].items(), key=lambda x: x[1]):
            table.add_row(name, str(rid))
        console.print(table)
        console.print()
