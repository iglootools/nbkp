"""Tests for config graph rendering (output.py graph functions)."""

import json
from io import StringIO
from pathlib import Path

from rich.console import Console

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
    format_mermaid_ascii_graph,
    format_mermaid_graph,
    format_rich_tree_graph,
)
from tests.clidisplay import runner
from tests.fakes import fake_runtime


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
