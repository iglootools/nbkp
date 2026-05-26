"""Tests for AuthRules block accessors shared by setup-auth and troubleshoot."""

from __future__ import annotations

from nbkp.disks.auth import (
    POLKIT_RULES_PATH,
    SUDOERS_RULES_PATH,
    AuthRuleBlock,
    AuthRules,
)


class TestAuthRulesBlocks:
    def test_polkit_block_present(self) -> None:
        rules = AuthRules(polkit="// rule", sudoers=None)
        block = rules.polkit_block()
        assert block is not None
        assert block.name == "polkit rules"
        assert block.path == POLKIT_RULES_PATH
        assert block.content == "// rule"
        assert block.install_hint == f"Install to: {POLKIT_RULES_PATH}"

    def test_polkit_block_absent(self) -> None:
        rules = AuthRules(polkit=None, sudoers="nopasswd ...")
        assert rules.polkit_block() is None

    def test_sudoers_block_present(self) -> None:
        rules = AuthRules(polkit=None, sudoers="user ALL=(root) NOPASSWD: ...")
        block = rules.sudoers_block()
        assert block is not None
        assert block.name == "sudoers rules"
        assert block.path == SUDOERS_RULES_PATH
        assert block.install_hint == f"Install to: {SUDOERS_RULES_PATH}"

    def test_sudoers_block_absent(self) -> None:
        rules = AuthRules(polkit="// rule", sudoers=None)
        assert rules.sudoers_block() is None

    def test_blocks_yields_in_install_order(self) -> None:
        """polkit before sudoers — the install order convention."""
        rules = AuthRules(polkit="// p", sudoers="s")
        names = [b.name for b in rules.blocks()]
        assert names == ["polkit rules", "sudoers rules"]

    def test_blocks_skips_empty_sections(self) -> None:
        rules = AuthRules(polkit=None, sudoers="s")
        blocks = list(rules.blocks())
        assert len(blocks) == 1
        assert blocks[0].name == "sudoers rules"

    def test_blocks_empty_when_no_content(self) -> None:
        rules = AuthRules(polkit=None, sudoers=None)
        assert list(rules.blocks()) == []


class TestAuthRuleBlockInstallHint:
    """Single source of truth for the user-facing install hint."""

    def test_install_hint_format(self) -> None:
        block = AuthRuleBlock(name="polkit rules", path="/etc/foo", content="x")
        assert block.install_hint == "Install to: /etc/foo"
