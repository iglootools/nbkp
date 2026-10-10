"""Tests for follow-up command suggestions built from the invocation."""

from __future__ import annotations

from pathlib import Path

from nbkp.commands.invocation import Invocation, display_path


class TestDisplayPath:
    def test_under_cwd_is_relative(self) -> None:
        assert display_path(Path("/w/conf/c.yaml"), Path("/w")) == "conf/c.yaml"

    def test_outside_cwd_stays_absolute(self) -> None:
        assert (
            display_path(Path("/etc/nbkp/config.yaml"), Path("/home/u"))
            == "/etc/nbkp/config.yaml"
        )


class TestCommand:
    def test_minimal(self) -> None:
        assert Invocation().command("preflight", "troubleshoot") == (
            "nbkp preflight troubleshoot"
        )

    def test_config_is_relative_to_cwd(self) -> None:
        inv = Invocation(config_path=Path("/w/my conf.yaml"), cwd=Path("/w"))
        assert inv.command("preflight", "troubleshoot") == (
            "nbkp preflight troubleshoot -c 'my conf.yaml'"
        )

    def test_repeated_locations_and_network(self) -> None:
        inv = Invocation.of(None, ["home", "office"], ["travel"], "public")
        assert inv.command("preflight", "troubleshoot") == (
            "nbkp preflight troubleshoot -l home -l office -L travel -N public"
        )

    def test_without_endpoint_flags(self) -> None:
        inv = Invocation(
            config_path=Path("/w/c.yaml"), locations=("home",), cwd=Path("/w")
        )
        assert inv.command("disks", "setup-auth", endpoint_flags=False) == (
            "nbkp disks setup-auth -c c.yaml"
        )
