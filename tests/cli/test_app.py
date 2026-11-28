import importlib.metadata
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from nbkp.cli import app
from nbkp.config import Config
from nbkp.config.testkit import write_config
from nbkp.remote.pool import ConnectionPool, active_pool
from tests.fakes import completed, fake_runtime
from tests.fixtures.disks import encrypted_volume


def test_subcommands_run_in_one_ssh_connection_scope(tmp_path: Path) -> None:
    seen: list[ConnectionPool | None] = []

    def record_pool(
        *_args: object,
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        seen.append(active_pool())
        return completed()

    config_path = write_config(tmp_path, Config(volumes={"enc": encrypted_volume()}))

    result = CliRunner().invoke(
        app,
        ["disks", "status", "-c", str(config_path), "-o", "json"],
        obj=fake_runtime(run=record_pool),
    )

    assert result.exit_code == 0, result.output
    assert seen
    assert isinstance(seen[0], ConnectionPool)
    assert all(pool is seen[0] for pool in seen)
    assert active_pool() is None


def test_version_prints_the_installed_version_and_exits_before_any_command() -> None:
    # --version is eager: it answers even next to an unknown subcommand, which
    # would otherwise fail argument parsing.
    result = CliRunner().invoke(app, ["--version", "no-such-command"])

    assert result.exit_code == 0, result.output
    assert result.output == f"{importlib.metadata.version('nbkp')}\n"


def test_version_short_flag() -> None:
    result = CliRunner().invoke(app, ["-V"])

    assert result.exit_code == 0, result.output
    assert result.output == f"{importlib.metadata.version('nbkp')}\n"


def test_config_help_names_where_the_config_is_searched() -> None:
    result = CliRunner().invoke(app, ["run", "--help"], env={"COLUMNS": "200"})

    assert result.exit_code == 0, result.output
    assert "$XDG_CONFIG_HOME/nbkp" in result.output
