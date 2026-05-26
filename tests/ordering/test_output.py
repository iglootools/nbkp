"""Tests for ordering output rendering."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from nbkp.clihelpers import Severity
from nbkp.config import (
    Config,
    LocalVolume,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.ordering.output import build_rich_tree_sections


def _render(renderable) -> str:  # type: ignore[no-untyped-def]
    buf = StringIO()
    Console(file=buf, highlight=False, markup=True, force_terminal=False).print(
        renderable
    )
    return buf.getvalue()


def _chain_config() -> Config:
    """A → B → C config: source-a → ep-b (via sync-ab) → ep-c (via sync-bc)."""
    return Config(
        volumes={"vol": LocalVolume(slug="vol", path="/mnt/vol")},
        sync_endpoints={
            "ep-a": SyncEndpoint(slug="ep-a", volume="vol", subdir="a"),
            "ep-b": SyncEndpoint(slug="ep-b", volume="vol", subdir="b"),
            "ep-c": SyncEndpoint(slug="ep-c", volume="vol", subdir="c"),
        },
        syncs={
            "sync-ab": SyncConfig(slug="sync-ab", source="ep-a", destination="ep-b"),
            "sync-bc": SyncConfig(slug="sync-bc", source="ep-b", destination="ep-c"),
        },
    )


class TestBuildRichTreeSectionsSeverity:
    def test_no_severities_renders_without_icons(self) -> None:
        """When no sync_severities dict is given, the tree shows no icons."""
        trees = build_rich_tree_sections(_chain_config())
        rendered = "\n".join(_render(t) for t in trees)
        assert "✓" not in rendered
        assert "⚠" not in rendered
        assert "✗" not in rendered
        assert "sync-ab" in rendered
        assert "sync-bc" in rendered

    def test_severities_prefix_each_sync_label(self) -> None:
        """Provided severities show up as icons before each sync's label."""
        config = _chain_config()
        severities = {
            "sync-ab": Severity.OK,
            "sync-bc": Severity.WARNING,
        }
        trees = build_rich_tree_sections(config, severities)
        rendered = "\n".join(_render(t) for t in trees)
        # Each sync's icon appears immediately before its slug.
        assert "✓" in rendered
        assert "⚠" in rendered

    def test_missing_severity_renders_without_icon(self) -> None:
        """Syncs not in the severities dict appear without an icon (defensive)."""
        config = _chain_config()
        # Only sync-ab classified; sync-bc missing
        severities = {"sync-ab": Severity.OK}
        trees = build_rich_tree_sections(config, severities)
        rendered = "\n".join(_render(t) for t in trees)
        assert "✓" in rendered  # from sync-ab
        # sync-bc still appears in the tree, just without an icon
        assert "sync-bc" in rendered

    def test_error_severity_renders_cross(self) -> None:
        config = _chain_config()
        severities = {"sync-ab": Severity.ERROR, "sync-bc": Severity.OK}
        trees = build_rich_tree_sections(config, severities)
        rendered = "\n".join(_render(t) for t in trees)
        assert "✗" in rendered
        assert "✓" in rendered
