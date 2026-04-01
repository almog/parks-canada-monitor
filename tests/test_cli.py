from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from parks_monitor.cli import app
from parks_monitor.client import BackcountryLocation

runner = CliRunner()


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


def test_discover_lists_locations():
    fake_locations = [
        BackcountryLocation(
            resource_location_id=-100, name="Banff - Backcountry", root_map_id=-1
        ),
        BackcountryLocation(
            resource_location_id=-200, name="Jasper - Backcountry", root_map_id=-2
        ),
    ]

    with patch(
        "parks_monitor.cli.GoingToCampClient.list_backcountry_locations",
        new_callable=AsyncMock,
        return_value=fake_locations,
    ):
        result = runner.invoke(app, ["discover"])

    assert result.exit_code == 0
    assert "Banff" in result.output
    assert "Jasper" in result.output


def test_discover_filters_by_park():
    fake_locations = [
        BackcountryLocation(
            resource_location_id=-100, name="Banff - Backcountry", root_map_id=-1
        ),
        BackcountryLocation(
            resource_location_id=-200, name="Jasper - Backcountry", root_map_id=-2
        ),
    ]

    with patch(
        "parks_monitor.cli.GoingToCampClient.list_backcountry_locations",
        new_callable=AsyncMock,
        return_value=fake_locations,
    ), patch(
        "parks_monitor.cli.GoingToCampClient.list_zones",
        new_callable=AsyncMock,
        return_value=[],
    ):
        result = runner.invoke(app, ["discover", "--park", "jasper"])

    assert result.exit_code == 0
    assert "Jasper" in result.output
    # Banff should be filtered out
    assert "Banff" not in result.output
