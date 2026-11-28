"""Tests for follow-up command suggestions built from the invocation."""

from pathlib import Path

from nbkp.cliwiring.invocation import Invocation, display_path
from nbkp.policy import Strictness


class TestDisplayPath:
    def test_under_cwd_is_relative(self) -> None:
        assert display_path(Path("/w/conf/c.yaml"), Path("/w")) == "conf/c.yaml"

    def test_outside_cwd_stays_absolute(self) -> None:
        assert (
            display_path(Path("/etc/nbkp/config.yaml"), Path("/home/u"))
            == "/etc/nbkp/config.yaml"
        )


class TestCommand:
    def test_command_prefixed_with_program_name(self) -> None:
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
            config_path=Path("/w/c.yaml"),
            locations=("home",),
            cwd=Path("/w"),
        )
        assert inv.command("disks", "setup-auth", endpoint_flags=False) == (
            "nbkp disks setup-auth -c c.yaml"
        )


class TestTroubleshootCommand:
    def test_default_strictness_and_mount_add_no_flags(self) -> None:
        assert Invocation().troubleshoot_command() == "nbkp preflight troubleshoot"

    def test_keeps_the_strictness_and_no_mount(self) -> None:
        inv = Invocation.of(
            Path("/w/c.yaml"),
            strictness=Strictness.IGNORE_NONE,
            mount=False,
        )
        assert inv.troubleshoot_command() == (
            "nbkp preflight troubleshoot -S ignore-none --no-mount -c /w/c.yaml"
        )
