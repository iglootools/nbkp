"""Tests for nbkp.cliwiring.options: how a path option is read."""

from pathlib import Path

from nbkp.cli import app
from nbkp.cliwiring.options import expand_user_path
from tests.fixtures.cli import runner


class TestExpandUserPath:
    def test_tilde_is_expanded_before_resolving(self) -> None:
        assert expand_user_path(Path("~/nbkp.yaml")) == Path.home() / "nbkp.yaml"

    def test_none_stays_none(self) -> None:
        assert expand_user_path(None) is None

    def test_config_option_expands_a_tilde_the_shell_left_alone(self) -> None:
        # `--config=~/…` (after `=`) is passed to nbkp unexpanded.
        result = runner.invoke(
            app,
            ["config", "show", "--config=~/nbkp-no-such-config.yaml", "-o", "json"],
        )

        assert result.exit_code == 2
        assert str(Path.home() / "nbkp-no-such-config.yaml") in result.output
        assert "/~/" not in result.output
