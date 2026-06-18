"""Tests for AuthRules block accessors and polkit rule generation."""

from __future__ import annotations

from nbkp.config import (
    Config,
    LocalVolume,
    LuksEncryptionConfig,
    MountConfig,
)
from nbkp.disks.auth import (
    POLKIT_RULES_PATH,
    UDISKS_ACTIONS,
    AuthRuleBlock,
    AuthRules,
    generate_auth_rules,
    generate_polkit_rules,
)


class TestAuthRulesBlocks:
    def test_polkit_block_present(self) -> None:
        rules = AuthRules(polkit="// rule")
        block = rules.polkit_block()
        assert block is not None
        assert block.name == "polkit rules"
        assert block.path == POLKIT_RULES_PATH
        assert block.content == "// rule"
        assert block.install_hint == f"Install to: {POLKIT_RULES_PATH}"

    def test_polkit_block_absent(self) -> None:
        rules = AuthRules(polkit=None)
        assert rules.polkit_block() is None

    def test_no_sudoers_field(self) -> None:
        """udisks is authorized purely by polkit — there is no sudoers block."""
        assert not hasattr(AuthRules(polkit="// rule"), "sudoers")
        assert not hasattr(AuthRules(polkit="// rule"), "sudoers_block")

    def test_blocks_yields_polkit_only(self) -> None:
        rules = AuthRules(polkit="// p")
        names = [b.name for b in rules.blocks()]
        assert names == ["polkit rules"]

    def test_blocks_empty_when_no_content(self) -> None:
        assert list(AuthRules(polkit=None).blocks()) == []


class TestAuthRuleBlockInstallHint:
    def test_install_hint_format(self) -> None:
        block = AuthRuleBlock(name="polkit rules", path="/etc/foo", content="x")
        assert block.install_hint == "Install to: /etc/foo"


class TestGeneratePolkitRules:
    def test_contains_user(self) -> None:
        rule = generate_polkit_rules("backup")
        assert 'subject.user == "backup"' in rule

    def test_contains_udisks_action_ids(self) -> None:
        rule = generate_polkit_rules("backup")
        assert "org.freedesktop.udisks2.filesystem-mount" in rule
        assert "org.freedesktop.udisks2.encrypted-unlock-system" in rule
        # Every configured action must appear in the rule.
        for action in UDISKS_ACTIONS:
            assert action in rule

    def test_install_path_referenced(self) -> None:
        assert POLKIT_RULES_PATH in generate_polkit_rules("backup")


def _config_with_mount() -> Config:
    return Config(
        volumes={
            "enc": LocalVolume(
                slug="enc",
                path="/mnt/enc",
                mount=MountConfig(
                    device_uuid="5941f273-f73c-44c5-a3ef-fae7248db1b6",
                    encryption=LuksEncryptionConfig(passphrase_id="enc"),
                ),
            ),
        },
    )


def _config_without_mount() -> Config:
    return Config(
        volumes={"plain": LocalVolume(slug="plain", path="/mnt/plain")},
    )


class TestGenerateAuthRules:
    def test_with_mount_volume_generates_polkit(self) -> None:
        rules = generate_auth_rules(_config_with_mount(), "backup")
        assert rules.polkit is not None
        assert 'subject.user == "backup"' in rules.polkit

    def test_without_mount_volume_no_rules(self) -> None:
        rules = generate_auth_rules(_config_without_mount(), "backup")
        assert rules.polkit is None
        assert list(rules.blocks()) == []
