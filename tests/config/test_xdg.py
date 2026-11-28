"""Tests for nbkp.config.xdg: which XDG base directory values are honoured."""

from pathlib import Path

import pytest

from nbkp.config.xdg import resolve_xdg_dir

_DEFAULT = Path("/home/alice/.config")


class TestResolveXdgDir:
    def test_absolute_value_is_used(self) -> None:
        result = resolve_xdg_dir(
            {"XDG_CONFIG_HOME": "/etc/xdg"},
            "XDG_CONFIG_HOME",
            _DEFAULT,
        )
        assert result == Path("/etc/xdg")

    @pytest.mark.parametrize(
        "environ",
        [{}, {"XDG_CONFIG_HOME": ""}, {"XDG_CONFIG_HOME": "rel"}],
    )
    def test_default_is_used_when_unset_empty_or_relative(
        self,
        environ: dict[str, str],
    ) -> None:
        assert resolve_xdg_dir(environ, "XDG_CONFIG_HOME", _DEFAULT) == _DEFAULT
