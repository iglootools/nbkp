"""Tests for nbkp sh CLI command."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from nbkp.cli import app
from tests.clihelpers import (
    runner,
    sample_config,
)


class TestShCommand:
    @patch("nbkp.config.clihelpers.load_config")
    def test_generates_script(self, mock_load: MagicMock) -> None:
        config = sample_config()
        mock_load.return_value = config

        result = runner.invoke(app, ["sh", "--config", "/fake.yaml"])
        assert result.exit_code == 0
        assert "#!/bin/bash" in result.output
        assert "set -euo pipefail" in result.output
        assert "sync_photos_to_nas()" in result.output

    @patch("nbkp.config.clihelpers.load_config")
    def test_config_path_in_header(self, mock_load: MagicMock) -> None:
        config = sample_config()
        mock_load.return_value = config

        result = runner.invoke(app, ["sh", "--config", "/fake.yaml"])
        assert result.exit_code == 0
        assert "# Config: /fake.yaml" in result.output

    @patch("nbkp.config.clihelpers.load_config")
    def test_output_file(self, mock_load: MagicMock, tmp_path: object) -> None:
        import pathlib
        import stat

        tp = pathlib.Path(str(tmp_path))
        config = sample_config()
        mock_load.return_value = config
        out = tp / "backup.sh"

        result = runner.invoke(
            app,
            ["sh", "--config", "/fake.yaml", "-o", str(out)],
        )
        assert result.exit_code == 0
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "#!/bin/bash" in content
        assert "sync_photos_to_nas()" in content
        mode = out.stat().st_mode
        assert mode & stat.S_IXUSR
        assert mode & stat.S_IXGRP

    def test_relative_without_output_file(self) -> None:
        result = runner.invoke(
            app,
            ["sh", "--config", "/fake.yaml", "--relative-src"],
        )
        assert result.exit_code == 2

    @patch("nbkp.config.clihelpers.load_config")
    def test_relative_with_output_file(
        self,
        mock_load: MagicMock,
        tmp_path: object,
    ) -> None:
        import pathlib

        tp = pathlib.Path(str(tmp_path))
        config = sample_config()
        mock_load.return_value = config
        out = tp / "backup.sh"

        result = runner.invoke(
            app,
            [
                "sh",
                "--config",
                "/fake.yaml",
                "-o",
                str(out),
                "--relative-src",
            ],
        )
        assert result.exit_code == 0
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "NBKP_SCRIPT_DIR" in content

    @patch(
        "nbkp.config.clihelpers.load_config",
        side_effect=__import__("nbkp.config", fromlist=["ConfigError"]).ConfigError(
            "bad config",
            reason=__import__(
                "nbkp.config", fromlist=["ConfigErrorReason"]
            ).ConfigErrorReason.VALIDATION,
        ),
    )
    def test_config_error(self, mock_load: MagicMock) -> None:
        result = runner.invoke(app, ["sh", "--config", "/bad.yaml"])
        assert result.exit_code == 2
