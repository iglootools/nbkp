import json
from pathlib import Path

from nbkp.cli import app
from nbkp.config import ConfigErrorReason, ConfigValidationCode
from nbkp.config.testkit import write_config
from tests.clidisplay import (
    runner,
    sample_config,
    strip_panel,
)
from tests.fakes import fake_runtime

_MISSING = "/nonexistent/nbkp/config.yaml"
"""A config path no test machine has: the real file lookup fails on it."""


def _write_raw(tmp_path: Path, text: str) -> str:
    path = tmp_path / "config.yaml"
    path.write_text(text)
    return str(path)


_INVALID_TYPE = "volumes:\n  v:\n    type: ftp\n    path: /x\n"
_UNKNOWN_SSH_ENDPOINT = (
    "ssh-endpoints: {}\n"
    "volumes:\n"
    "  v:\n"
    "    type: remote\n"
    "    ssh-endpoint: missing\n"
    "    path: /x\n"
    "syncs: {}\n"
)


class TestConfigSearch:
    """Without ``--config``, the command searches the runtime's home and
    platform directories, never the developer's."""

    def test_finds_the_config_under_the_runtime_home(self, tmp_path: Path) -> None:
        (tmp_path / ".config" / "nbkp").mkdir(parents=True)
        write_config(tmp_path / ".config" / "nbkp", sample_config())

        result = runner.invoke(
            app,
            ["config", "show", "-o", "json"],
            obj=fake_runtime(home=tmp_path),
        )

        assert result.exit_code == 0, result.output
        assert "nas-server" in json.loads(result.output)["ssh-endpoints"]

    def test_reports_no_config_when_the_runtime_has_none(self) -> None:
        result = runner.invoke(
            app, ["config", "show", "-o", "json"], obj=fake_runtime()
        )

        assert result.exit_code == 2
        error = json.loads(result.output)["error"]
        assert error["reason"] == ConfigErrorReason.NO_CONFIG_FOUND
        assert "/nonexistent-nbkp-test/home/.config/nbkp" in error["message"]


class TestConfigShowCommand:
    def test_human_output(self, tmp_path: Path) -> None:
        config_path = write_config(tmp_path, sample_config())

        result = runner.invoke(app, ["config", "show", "--config", str(config_path)])
        assert result.exit_code == 0
        assert "Volumes:" in result.output
        assert "Syncs:" in result.output
        assert "local-data" in result.output
        assert "nas" in result.output
        assert "photos" in result.output

    def test_human_output_shows_servers(self, tmp_path: Path) -> None:
        config_path = write_config(tmp_path, sample_config())

        result = runner.invoke(app, ["config", "show", "--config", str(config_path)])
        assert result.exit_code == 0
        assert "SSH Endpoints:" in result.output
        assert "nas-server" in result.output
        assert "nas.example.com" in result.output

    def test_json_output(self, tmp_path: Path) -> None:
        config_path = write_config(tmp_path, sample_config())

        result = runner.invoke(
            app,
            [
                "config",
                "show",
                "--config",
                str(config_path),
                "--output",
                "json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "volumes" in data
        assert "syncs" in data
        assert "ssh-endpoints" in data

    def test_config_error(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["config", "show", "--config", _write_raw(tmp_path, _INVALID_TYPE)],
        )
        assert result.exit_code == 2


class TestConfigError:
    def test_check_config_error(self, tmp_path: Path) -> None:
        bad = _write_raw(tmp_path, _INVALID_TYPE)
        result = runner.invoke(app, ["preflight", "check", "--config", bad])
        assert result.exit_code == 2

    def test_run_config_error(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app, ["run", "--config", _write_raw(tmp_path, _INVALID_TYPE)]
        )
        assert result.exit_code == 2

    def test_plain_error_message(self) -> None:
        result = runner.invoke(app, ["preflight", "check", "--config", _MISSING])
        assert result.exit_code == 2
        out = strip_panel(result.output)
        assert f"Config file not found: {_MISSING}" in out

    def test_validation_error_message(self, tmp_path: Path) -> None:
        bad = _write_raw(tmp_path, _INVALID_TYPE)
        result = runner.invoke(app, ["preflight", "check", "--config", bad])
        assert result.exit_code == 2
        out = strip_panel(result.output)
        assert "volumes → v" in out
        assert "does not match any of the expected tags" in out

    def test_yaml_error_message(self, tmp_path: Path) -> None:
        bad = _write_raw(tmp_path, "not_a_list:\n  - [invalid")
        result = runner.invoke(app, ["preflight", "check", "--config", bad])
        assert result.exit_code == 2
        out = strip_panel(result.output)
        assert "Invalid YAML" in out

    def test_cross_reference_error_message(self, tmp_path: Path) -> None:
        bad = _write_raw(tmp_path, _UNKNOWN_SSH_ENDPOINT)
        result = runner.invoke(app, ["preflight", "check", "--config", bad])
        assert result.exit_code == 2
        out = strip_panel(result.output)
        assert "unknown ssh-endpoint 'missing'" in out


class TestConfigErrorJson:
    def test_validation_error_as_json(self, tmp_path: Path) -> None:
        bad = _write_raw(tmp_path, _UNKNOWN_SSH_ENDPOINT)
        result = runner.invoke(app, ["config", "show", "--config", bad, "-o", "json"])
        assert result.exit_code == 2
        data = json.loads(result.stdout)
        assert data["error"]["reason"] == ConfigErrorReason.VALIDATION
        assert [e["type"] for e in data["error"]["errors"]] == [
            ConfigValidationCode.UNKNOWN_REFERENCE
        ]

    def test_plain_error_as_json(self) -> None:
        result = runner.invoke(
            app, ["preflight", "check", "--config", _MISSING, "-o", "json"]
        )
        assert result.exit_code == 2
        assert json.loads(result.stdout) == {
            "error": {
                "reason": "file-not-found",
                "message": f"Config file not found: {_MISSING}",
            }
        }
