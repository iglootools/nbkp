from unittest.mock import patch

import typer
from typer.testing import CliRunner

from nbkp.cli import app
from nbkp.remote.pool import ConnectionPool, active_pool


def test_subcommands_run_in_one_ssh_connection_scope() -> None:
    seen: list[ConnectionPool | None] = []

    def record_pool(*args: object) -> None:
        seen.append(active_pool())
        raise typer.Exit(0)

    with patch("nbkp.config.cli.show_cmd.load_config_or_exit", side_effect=record_pool):
        result = CliRunner().invoke(app, ["config", "show"])

    assert result.exit_code == 0, result.output
    assert len(seen) == 1
    assert isinstance(seen[0], ConnectionPool)
    assert active_pool() is None
