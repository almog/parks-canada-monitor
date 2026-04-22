from pathlib import Path

from typer.testing import CliRunner

from parks_monitor.cli import app

runner = CliRunner()


def test_version_flag():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "parks-monitor" in result.output


def test_run_missing_config(tmp_path: Path):
    result = runner.invoke(app, ["run", "--config", str(tmp_path / "nope.yaml")])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_run_missing_watchlist(tmp_path: Path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("monitor:\n  poll_interval_minutes: 5\n")
    result = runner.invoke(
        app,
        ["run", "--config", str(config_file), "--watchlist", str(tmp_path / "nope.yaml")],
    )
    assert result.exit_code == 1
    assert "not found" in result.output


def test_discover_lists_all():
    result = runner.invoke(app, ["discover"])
    assert result.exit_code == 0
    assert "Banff" in result.output
    assert "Jasper" in result.output
    assert "Waterton" in result.output


def test_discover_filters_by_park():
    result = runner.invoke(app, ["discover", "--park", "waterton"])
    assert result.exit_code == 0
    assert "Waterton" in result.output
    assert "Jasper" not in result.output


def test_discover_gdt_filter():
    result = runner.invoke(app, ["discover", "--gdt", "--park", "jasper"])
    assert result.exit_code == 0
    assert "GDT" in result.output
    assert "61 - Athabasca Pass" in result.output


def test_discover_type_filter():
    result = runner.invoke(app, ["discover", "--park", "banff", "--type", "designated"])
    assert result.exit_code == 0
    assert "Egypt Lake - E13" in result.output
    assert "Trailhead" not in result.output


def test_discover_unknown_park():
    result = runner.invoke(app, ["discover", "--park", "notapark"])
    assert result.exit_code == 1


def test_watchlist_list_empty(tmp_path: Path):
    result = runner.invoke(app, ["watchlist", "list", "--watchlist", str(tmp_path / "wl.yaml")])
    assert result.exit_code == 0
    assert "empty" in result.output.lower() or "No watchlist" in result.output


def test_watchlist_add_creates_file(tmp_path: Path):
    wl = tmp_path / "wl.yaml"
    result = runner.invoke(
        app,
        ["watchlist", "add", "Egypt Lake - E13", "--start", "2026-07-01", "--end", "2026-07-03", "--watchlist", str(wl)],
    )
    assert result.exit_code == 0
    assert "Added" in result.output
    assert wl.exists()


def test_watchlist_add_bad_campsite(tmp_path: Path):
    result = runner.invoke(
        app,
        ["watchlist", "add", "Totally Fake Campsite XYZ", "--start", "2026-07-01", "--end", "2026-07-03",
         "--watchlist", str(tmp_path / "wl.yaml")],
    )
    assert result.exit_code == 1
    assert "No campsite found" in result.output


def test_watchlist_add_and_list(tmp_path: Path):
    wl = tmp_path / "wl.yaml"
    runner.invoke(
        app,
        ["watchlist", "add", "61 - Athabasca Pass", "--start", "2026-07-10", "--end", "2026-07-12",
         "--watchlist", str(wl)],
    )
    result = runner.invoke(app, ["watchlist", "list", "--watchlist", str(wl)])
    assert result.exit_code == 0
    assert "Athabasca Pass" in result.output


def test_watchlist_remove(tmp_path: Path):
    wl = tmp_path / "wl.yaml"
    runner.invoke(
        app,
        ["watchlist", "add", "Egypt Lake - E13", "--start", "2026-07-01", "--end", "2026-07-03",
         "--watchlist", str(wl)],
    )
    result = runner.invoke(app, ["watchlist", "remove", "0", "--yes", "--watchlist", str(wl)])
    assert result.exit_code == 0
    assert "Removed" in result.output
    # List should now be empty
    result2 = runner.invoke(app, ["watchlist", "list", "--watchlist", str(wl)])
    assert "empty" in result2.output.lower()


def test_watchlist_remove_bad_index(tmp_path: Path):
    wl = tmp_path / "wl.yaml"
    runner.invoke(
        app,
        ["watchlist", "add", "Egypt Lake - E13", "--start", "2026-07-01", "--end", "2026-07-03",
         "--watchlist", str(wl)],
    )
    result = runner.invoke(app, ["watchlist", "remove", "99", "--yes", "--watchlist", str(wl)])
    assert result.exit_code == 1
    assert "out of range" in result.output
