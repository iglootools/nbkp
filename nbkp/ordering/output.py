"""Graph output rendering for sync dependency visualization."""

from __future__ import annotations

from mermaid_ascii import parse_mermaid, render_ascii
from rich.console import Console
from rich.tree import Tree

from ..config import Config, SyncEndpoint
from .graph import build_adjacency


def _endpoint_annotation(ep: SyncEndpoint) -> str:
    """Format snapshot mode annotation for a sync endpoint."""
    match ep.snapshot_mode:
        case "btrfs":
            max_s = ep.btrfs_snapshots.max_snapshots
            suffix = f", max: {max_s}" if max_s is not None else ""
            return f"btrfs{suffix}"
        case "hard-link":
            max_s = ep.hard_link_snapshots.max_snapshots
            suffix = f", max: {max_s}" if max_s is not None else ""
            return f"hard-link{suffix}"
        case _:
            return ""


def build_mermaid_graph(config: Config) -> str:
    """Generate mermaid graph LR syntax from config."""
    children, roots = build_adjacency(config.syncs)
    lines = ["graph LR"]
    visited: set[str] = set()

    def _walk(ep_slug: str) -> None:
        if ep_slug in visited:
            return
        visited.add(ep_slug)
        for sync in children.get(ep_slug, []):
            dst_slug = sync.destination
            lines.append(f"    {ep_slug} -->|{sync.slug}| {dst_slug}")
            _walk(dst_slug)

    for root in sorted(roots):
        _walk(root)

    # Include any endpoints not reachable from roots (cycles or isolated)
    for ep_slug in sorted(children.keys()):
        _walk(ep_slug)

    return "\n".join(lines)


def print_mermaid_ascii_graph(
    config: Config,
    *,
    console: Console | None = None,
) -> None:
    """Render the config graph as ASCII art using mermaid-ascii-diagrams."""
    if console is None:
        console = Console()
    mermaid_src = build_mermaid_graph(config)
    diagram = parse_mermaid(mermaid_src)
    console.print(render_ascii(diagram), highlight=False)


def print_rich_tree_graph(
    config: Config,
    *,
    console: Console | None = None,
) -> None:
    """Render the config graph as Rich Trees."""
    if console is None:
        console = Console()

    children, roots = build_adjacency(config.syncs)

    def _add_children(tree: Tree, ep_slug: str, visited: set[str]) -> None:
        for sync in children.get(ep_slug, []):
            dst_slug = sync.destination
            annotation = _endpoint_annotation(config.sync_endpoints[dst_slug])
            label = " ".join(
                part
                for part in [
                    f"[bold]{sync.slug}[/bold] -> {dst_slug}",
                    f"({annotation})" if annotation else "",
                    "(disabled)" if not sync.enabled else "",
                ]
                if part
            )
            child = tree.add(label, style="dim" if not sync.enabled else "")

            if dst_slug not in visited:
                visited.add(dst_slug)
                _add_children(child, dst_slug, visited)

    for root in sorted(roots):
        tree = Tree(f"[bold]{root}[/bold]")
        visited: set[str] = {root}
        _add_children(tree, root, visited)
        console.print(tree)


def print_mermaid_graph(config: Config) -> None:
    """Print raw mermaid graph syntax to stdout."""
    print(build_mermaid_graph(config))


def build_graph_json(config: Config) -> dict[str, object]:
    """Build JSON-serializable graph structure."""
    ep_slugs = {
        slug
        for sync in config.syncs.values()
        for slug in (sync.source, sync.destination)
    }

    return {
        "nodes": [
            {
                "slug": slug,
                "volume": ep.volume,
                "subdir": ep.subdir,
                "snapshot_mode": ep.snapshot_mode,
            }
            for slug in sorted(ep_slugs)
            if (ep := config.sync_endpoints.get(slug)) is not None
        ],
        "edges": [
            {
                "sync": sync.slug,
                "source": sync.source,
                "destination": sync.destination,
                "enabled": sync.enabled,
            }
            for sync in config.syncs.values()
        ],
    }
