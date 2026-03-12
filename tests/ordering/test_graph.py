"""Tests for config graph rendering (output.py graph functions)."""

from __future__ import annotations

import json
from io import StringIO

from rich.console import Console

from nbkp.config import (
    BtrfsSnapshotConfig,
    Config,
    HardLinkSnapshotConfig,
    LocalVolume,
    SyncConfig,
    SyncEndpoint,
)
from nbkp.ordering.output import (
    build_graph_json,
    build_mermaid_graph,
    print_mermaid_ascii_graph,
    print_rich_tree_graph,
)


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
                slug="laptop-docs", volume="laptop", subdir="docs"
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
                    enabled=True, max_snapshots=5
                ),
            ),
            "offsite-docs": SyncEndpoint(
                slug="offsite-docs", volume="offsite", subdir="docs"
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


class TestPrintMermaidAsciiGraph:
    def test_renders_without_error(self) -> None:
        cfg = _chain_config()
        buf = StringIO()
        console = Console(file=buf, highlight=False, markup=False)
        print_mermaid_ascii_graph(cfg, console=console)
        output = buf.getvalue()
        assert "laptop-docs" in output
        assert "server-docs" in output
        assert "usb-docs" in output


class TestPrintRichTreeGraph:
    def test_contains_root_and_sync_slugs(self) -> None:
        cfg = _chain_config()
        buf = StringIO()
        console = Console(file=buf, highlight=False, markup=False)
        print_rich_tree_graph(cfg, console=console)
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
        print_rich_tree_graph(cfg, console=console)
        output = buf.getvalue()
        assert "btrfs, max: 10" in output
        assert "hard-link, max: 5" in output

    def test_shows_disabled_sync(self) -> None:
        cfg = _chain_config()
        buf = StringIO()
        console = Console(file=buf, highlight=False, markup=False)
        print_rich_tree_graph(cfg, console=console)
        output = buf.getvalue()
        assert "laptop-to-offsite" in output
        assert "(disabled)" in output

    def test_no_annotation_for_no_snapshots(self) -> None:
        cfg = _chain_config()
        buf = StringIO()
        console = Console(file=buf, highlight=False, markup=False)
        print_rich_tree_graph(cfg, console=console)
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
