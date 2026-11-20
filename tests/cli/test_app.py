import importlib.metadata
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from nbkp.cli import app
from nbkp.config import Config, Volume
from nbkp.config.testkit import write_config
from nbkp.remote.endpoints import ResolvedEndpoints
from nbkp.remote.pool import ConnectionPool, active_pool
from tests.cliwiring.test_mount import encrypted_volume
from tests.fakes import completed, fake_runtime


def test_subcommands_run_in_one_ssh_connection_scope(tmp_path: Path) -> None:
    seen: list[ConnectionPool | None] = []

    def record_pool(
        cmd: list[str],
        volume: Volume,
        resolved_endpoints: ResolvedEndpoints,
        *,
        input: str | None = None,
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
