"""Tests for nbkp config CLI commands."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from nbkp.cli import app
from tests.clihelpers import (
    runner,
    sample_config,
    strip_panel,
)


class TestConfigShowCommand:
    @patch("nbkp.clihelpers.config.load_config")
    def test_human_output(self, mock_load: MagicMock) -> None:
        config = sample_config()
        mock_load.return_value = config

        result = runner.invoke(app, ["config", "show", "--config", "/fake.yaml"])
        assert result.exit_code == 0
        assert "Volumes:" in result.output
        assert "Syncs:" in result.output
        assert "local-data" in result.output
        assert "nas" in result.output
        assert "photos" in result.output

    @patch("nbkp.clihelpers.config.load_config")
    def test_human_output_shows_servers(self, mock_load: MagicMock) -> None:
        config = sample_config()
        mock_load.return_value = config

        result = runner.invoke(app, ["config", "show", "--config", "/fake.yaml"])
        assert result.exit_code == 0
        assert "SSH Endpoints:" in result.output
        assert "nas-server" in result.output
        assert "nas.example.com" in result.output

    @patch("nbkp.clihelpers.config.load_config")
    def test_json_output(self, mock_load: MagicMock) -> None:
        config = sample_config()
        mock_load.return_value = config

        result = runner.invoke(
            app,
            [
                "config",
                "show",
                "--config",
                "/fake.yaml",
                "--output",
                "json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "volumes" in data
        assert "syncs" in data
        assert "ssh-endpoints" in data

    @patch(
        "nbkp.clihelpers.config.load_config",
        side_effect=__import__("nbkp.config", fromlist=["ConfigError"]).ConfigError(
            "bad config",
            reason=__import__(
                "nbkp.config", fromlist=["ConfigErrorReason"]
            ).ConfigErrorReason.VALIDATION,
        ),
    )
    def test_config_error(self, mock_load: MagicMock) -> None:
        result = runner.invoke(
            app,
            ["config", "show", "--config", "/bad.yaml"],
        )
        assert result.exit_code == 2


class TestConfigError:
    @patch(
        "nbkp.clihelpers.config.load_config",
        side_effect=__import__("nbkp.config", fromlist=["ConfigError"]).ConfigError(
            "bad config",
            reason=__import__(
                "nbkp.config", fromlist=["ConfigErrorReason"]
            ).ConfigErrorReason.VALIDATION,
        ),
    )
    def test_check_config_error(self, mock_load: MagicMock) -> None:
        result = runner.invoke(app, ["preflight", "check", "--config", "/bad.yaml"])
        assert result.exit_code == 2

    @patch(
        "nbkp.clihelpers.config.load_config",
        side_effect=__import__("nbkp.config", fromlist=["ConfigError"]).ConfigError(
            "bad config",
            reason=__import__(
                "nbkp.config", fromlist=["ConfigErrorReason"]
            ).ConfigErrorReason.VALIDATION,
        ),
    )
    def test_run_config_error(self, mock_load: MagicMock) -> None:
        result = runner.invoke(app, ["run", "--config", "/bad.yaml"])
        assert result.exit_code == 2

    def test_plain_error_message(self) -> None:
        from nbkp.config import ConfigError

        from nbkp.config import ConfigErrorReason

        err = ConfigError(
            "Config file not found: /bad.yaml",
            reason=ConfigErrorReason.FILE_NOT_FOUND,
        )
        with patch("nbkp.clihelpers.config.load_config", side_effect=err):
            result = runner.invoke(app, ["preflight", "check", "--config", "/bad.yaml"])
        assert result.exit_code == 2
        out = strip_panel(result.output)
        assert "Config file not found: /bad.yaml" in out

    def test_validation_error_message(self) -> None:
        from nbkp.config import ConfigError, ConfigErrorReason
        from pydantic import ValidationError
        from nbkp.config.protocol import Config

        try:
            Config.model_validate({"volumes": {"v": {"type": "ftp", "path": "/x"}}})
        except ValidationError as ve:
            err = ConfigError(str(ve), reason=ConfigErrorReason.VALIDATION)
            err.__cause__ = ve

        with patch("nbkp.clihelpers.config.load_config", side_effect=err):
            result = runner.invoke(app, ["preflight", "check", "--config", "/bad.yaml"])
        assert result.exit_code == 2
        out = strip_panel(result.output)
        assert "volumes → v" in out
        assert "does not match any of the expected tags" in out

    def test_yaml_error_message(self) -> None:
        import yaml
        from nbkp.config import ConfigError, ConfigErrorReason

        try:
            yaml.safe_load("not_a_list:\n  - [invalid")
        except yaml.YAMLError as ye:
            err = ConfigError(
                f"Invalid YAML in /bad.yaml: {ye}",
                reason=ConfigErrorReason.INVALID_YAML,
            )
            err.__cause__ = ye

        with patch("nbkp.clihelpers.config.load_config", side_effect=err):
            result = runner.invoke(app, ["preflight", "check", "--config", "/bad.yaml"])
        assert result.exit_code == 2
        out = strip_panel(result.output)
        assert "Invalid YAML" in out

    def test_cross_reference_error_message(self) -> None:
        from nbkp.config import ConfigError, ConfigErrorReason
        from pydantic import ValidationError
        from nbkp.config.protocol import Config

        try:
            Config.model_validate(
                {
                    "ssh-endpoints": {},
                    "volumes": {
                        "v": {
                            "type": "remote",
                            "ssh-endpoint": "missing",
                            "path": "/x",
                        },
                    },
                    "syncs": {},
                }
            )
        except ValidationError as ve:
            err = ConfigError(str(ve), reason=ConfigErrorReason.VALIDATION)
            err.__cause__ = ve

        with patch("nbkp.clihelpers.config.load_config", side_effect=err):
            result = runner.invoke(app, ["preflight", "check", "--config", "/bad.yaml"])
        assert result.exit_code == 2
        out = strip_panel(result.output)
        assert "unknown ssh-endpoint 'missing'" in out
