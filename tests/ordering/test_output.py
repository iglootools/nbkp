"""Tests for ordering output rendering: the sync tree and the graph formats."""

import json
from io import StringIO
from pathlib import Path

from rich.console import Console, RenderableType

from nbkp.cli import app
from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.config.testkit import write_config
from nbkp.ordering.output import (
    build_graph_json,
    build_mermaid_graph,
    build_rich_tree_sections,
    format_mermaid_ascii_graph,
    format_mermaid_graph,
    format_rich_tree_graph,
)
from nbkp.policy import Severity
from tests.fakes import fake_runtime
from tests.fixtures.cli import runner


def _render(renderable: RenderableType) -> str:
    buf = StringIO()
    Console(file=buf, highlight=False, markup=True, force_terminal=False).print(
        renderable,
    )
    return buf.getvalue()


def _two_sync_chain_config() -> Config:
    """
    Build an A → B → C config.

    source-a → ep-b (via sync-ab) → ep-c (via sync-bc).
    """
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
        trees = build_rich_tree_sections(_two_sync_chain_config())
        rendered = "\n".join(_render(t) for t in trees)
        assert "✓" not in rendered
        assert "⚠" not in rendered
        assert "✗" not in rendered
        assert "sync-ab" in rendered
        assert "sync-bc" in rendered

    def test_severities_prefix_each_sync_label(self) -> None:
        """Provided severities show up as icons before each sync's label."""
        config = _two_sync_chain_config()
        severities = {
            "sync-ab": Severity.OK,
            "sync-bc": Severity.WARNING,
        }
        trees = build_rich_tree_sections(config, severities)
        rendered = "\n".join(_render(t) for t in trees)
        assert "✓" in rendered
        assert "⚠" in rendered

    def test_missing_severity_renders_without_icon(self) -> None:
        """Syncs not in the severities dict appear without an icon (defensive)."""
        config = _two_sync_chain_config()
        severities = {"sync-ab": Severity.OK}
        trees = build_rich_tree_sections(config, severities)
        rendered = "\n".join(_render(t) for t in trees)
        assert "✓" in rendered  # from sync-ab
        assert "sync-bc" in rendered

    def test_error_severity_renders_cross(self) -> None:
        config = _two_sync_chain_config()
        severities = {"sync-ab": Severity.ERROR, "sync-bc": Severity.OK}
        trees = build_rich_tree_sections(config, severities)
        rendered = "\n".join(_render(t) for t in trees)
        assert "✗" in rendered
        assert "✓" in rendered


def _chain_config() -> Config:
    """Config with a chain: A -> B -> C and A -> D."""
    return Config(
        volumes={
            "laptop": LocalVolume(slug="laptop", path="/mnt/laptop"),
            "server": LocalVolume(slug="server", path="/mnt/server"),
            "usb": LocalVolume(slug="usb", path="/mnt/usb"),
            "offsite": LocalVolume(slug="offsite", path="/mnt/offsite"),
        },
        sync_endpoints={
            "laptop-docs": SyncEndpoint(
                slug="laptop-docs",
                volume="laptop",
                subdir="docs",
            ),
            "server-docs": SyncEndpoint(
                slug="server-docs",
                volume="server",
                subdir="docs",
                btrfs_snapshots=BtrfsSnapshotConfig(enabled=True, max_snapshots=10),
            ),
            "usb-docs": SyncEndpoint(
                slug="usb-docs",
                volume="usb",
                subdir="docs",
                hard_link_snapshots=HardLinkSnapshotConfig(
                    enabled=True,
                    max_snapshots=5,
                ),
            ),
            "offsite-docs": SyncEndpoint(
                slug="offsite-docs",
                volume="offsite",
                subdir="docs",
            ),
        },
        syncs={
            "laptop-to-server": SyncConfig(
                slug="laptop-to-server",
                source="laptop-docs",
                destination="server-docs",
            ),
            "server-to-usb": SyncConfig(
                slug="server-to-usb",
                source="server-docs",
                destination="usb-docs",
            ),
            "laptop-to-offsite": SyncConfig(
                slug="laptop-to-offsite",
                source="laptop-docs",
                destination="offsite-docs",
                enabled=False,
            ),
        },
    )


class TestBuildMermaidGraph:
    def test_contains_graph_header(self) -> None:
        cfg = _chain_config()
        result = build_mermaid_graph(cfg)
        assert result.startswith("graph LR")

    def test_contains_all_edges(self) -> None:
        cfg = _chain_config()
        result = build_mermaid_graph(cfg)
        assert "laptop-docs -->|laptop-to-server| server-docs" in result
        assert "server-docs -->|server-to-usb| usb-docs" in result
        assert "laptop-docs -->|laptop-to-offsite| offsite-docs" in result

    def test_chain_order(self) -> None:
        """Upstream edges appear before downstream edges."""
        cfg = _chain_config()
        result = build_mermaid_graph(cfg)
        lines = result.splitlines()
        laptop_to_server_idx = next(
            i for i, line in enumerate(lines) if "laptop-to-server" in line
        )
        server_to_usb_idx = next(
            i for i, line in enumerate(lines) if "server-to-usb" in line
        )
        assert laptop_to_server_idx < server_to_usb_idx


class TestFormatMermaidGraph:
    def test_output_is_exact_mermaid_text(self) -> None:
        # Lines wider than the console must not be wrapped, and ``|``/``>``
        # must not be highlighted: the output is piped into mermaid.
        cfg = _chain_config()
        buf = StringIO()
        Console(file=buf, width=20).print(format_mermaid_graph(cfg), soft_wrap=True)
        assert buf.getvalue() == build_mermaid_graph(cfg) + "\n"

    def test_command_prints_exact_mermaid_text(self, tmp_path: Path) -> None:
        cfg = _chain_config()
        config_path = write_config(tmp_path, cfg)
        result = runner.invoke(
            app,
            ["ordering", "graph", "-c", str(config_path), "-f", "mermaid"],
            obj=fake_runtime(),
            env={"COLUMNS": "20"},
        )
        assert result.exit_code == 0, result.output
        assert result.output == build_mermaid_graph(cfg) + "\n"


class TestFormatMermaidAsciiGraph:
    def test_renders_without_error(self) -> None:
        cfg = _chain_config()
        buf = StringIO()
        console = Console(file=buf, highlight=False, markup=False)
        console.print(format_mermaid_ascii_graph(cfg))
        output = buf.getvalue()
        assert "laptop-docs" in output
        assert "server-docs" in output
        assert "usb-docs" in output


class TestFormatRichTreeGraph:
    def test_contains_root_and_sync_slugs(self) -> None:
        cfg = _chain_config()
        buf = StringIO()
        console = Console(file=buf, highlight=False, markup=False)
        console.print(format_rich_tree_graph(cfg))
        output = buf.getvalue()
        assert "laptop-docs" in output
        assert "laptop-to-server" in output
        assert "server-docs" in output
        assert "server-to-usb" in output
        assert "usb-docs" in output

    def test_shows_snapshot_annotations(self) -> None:
        cfg = _chain_config()
        buf = StringIO()
        console = Console(file=buf, highlight=False, markup=False)
        console.print(format_rich_tree_graph(cfg))
        output = buf.getvalue()
        assert "btrfs, max: 10" in output
        assert "hard-link, max: 5" in output

    def test_shows_disabled_sync(self) -> None:
        cfg = _chain_config()
        buf = StringIO()
        console = Console(file=buf, highlight=False, markup=False)
        console.print(format_rich_tree_graph(cfg))
        output = buf.getvalue()
        assert "laptop-to-offsite" in output
        assert "(disabled)" in output

    def test_no_annotation_for_no_snapshots(self) -> None:
        cfg = _chain_config()
        buf = StringIO()
        console = Console(file=buf, highlight=False, markup=False)
        console.print(format_rich_tree_graph(cfg))
        output = buf.getvalue()
        # offsite-docs has no snapshots — line should not have parens
        # except for (disabled)
        for line in output.splitlines():
            if "offsite-docs" in line:
                assert "btrfs" not in line
                assert "hard-link" not in line


class TestBuildGraphJson:
    def test_structure(self) -> None:
        cfg = _chain_config()
        result = build_graph_json(cfg)
        assert "nodes" in result
        assert "edges" in result

    def test_node_count(self) -> None:
        cfg = _chain_config()
        result = build_graph_json(cfg)
        assert len(result["nodes"]) == 4

    def test_edge_count(self) -> None:
        cfg = _chain_config()
        result = build_graph_json(cfg)
        assert len(result["edges"]) == 3

    def test_node_fields(self) -> None:
        cfg = _chain_config()
        result = build_graph_json(cfg)
        node_map = {n["slug"]: n for n in result["nodes"]}
        server = node_map["server-docs"]
        assert server["volume"] == "server"
        assert server["subdir"] == "docs"
        assert server["snapshot_mode"] == "btrfs"

    def test_edge_fields(self) -> None:
        cfg = _chain_config()
        result = build_graph_json(cfg)
        edge_map = {e["sync"]: e for e in result["edges"]}
        edge = edge_map["laptop-to-server"]
        assert edge["source"] == "laptop-docs"
        assert edge["destination"] == "server-docs"
        assert edge["enabled"] is True

    def test_disabled_edge(self) -> None:
        cfg = _chain_config()
        result = build_graph_json(cfg)
        edge_map = {e["sync"]: e for e in result["edges"]}
        assert edge_map["laptop-to-offsite"]["enabled"] is False

    def test_json_serializable(self) -> None:
        cfg = _chain_config()
        result = build_graph_json(cfg)
        serialized = json.dumps(result)
        assert isinstance(serialized, str)
