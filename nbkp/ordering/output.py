"""Rendering the sync dependency graph as a tree, mermaid text, ASCII art or JSON."""

from rich.console import Group, RenderableType
from rich.text import Text
from rich.tree import Tree

# Only `ordering graph --format ascii` and `demo output` draw ASCII art.
# mermaid-ascii-diagrams leaves render_ascii's parameter unannotated; retire the
# pyright ignore when it annotates it.
lazy from mermaid_ascii import parse_mermaid, render_ascii  # pyright: ignore[reportUnknownVariableType]

from nbkp.clidisplay import severity_icon
from nbkp.config import Config, SyncEndpoint
from nbkp.ordering.graph import build_adjacency
from nbkp.policy import Severity


def _endpoint_annotation(ep: SyncEndpoint) -> str:
    match (ep.snapshot_mode, ep.max_snapshots):
        case ("none", _):
            return ""
        case (mode, None):
            return mode
        case (mode, max_s):
            return f"{mode}, max: {max_s}"


def build_mermaid_graph(config: Config) -> str:
    """
    Return the sync graph as mermaid ``graph LR`` source.

    Nodes are sync endpoints and each edge is a sync labelled with its slug,
    walked from the root endpoints first, then from any endpoint left over.
    """
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


def format_mermaid_ascii_graph(config: Config) -> Text:
    """
    Format the ASCII-art graph as text, not markup.

    The ASCII art is drawing, and any bracket in it is a box character, not a style tag.
    """
    return Text(render_ascii(parse_mermaid(build_mermaid_graph(config))))


def build_rich_tree_sections(
    config: Config,
    sync_severities: dict[str, Severity] | None = None,
) -> list[RenderableType]:
    """
    Build the sync tree sections, one per root.

    When *sync_severities* is given, each sync node's label is prefixed with the
    matching severity icon (``✓`` / ``⚠`` / ``✗``) so the tree doubles as a per-sync
    result summary. Syncs missing from the map render without an icon (e.g. config
    preview where no run results are available).
    """
    children, roots = build_adjacency(config.syncs)
    severities = sync_severities or {}

    def _add_children(tree: Tree, ep_slug: str, visited: set[str]) -> None:
        for sync in children.get(ep_slug, []):
            dst_slug = sync.destination
            annotation = _endpoint_annotation(config.sync_endpoints[dst_slug])
            severity = severities.get(sync.slug)
            icon_prefix = f"{severity_icon(severity)} " if severity is not None else ""
            label = " ".join(
                part
                for part in [
                    f"{icon_prefix}[bold]{sync.slug}[/bold] -> {dst_slug}",
                    f"({annotation})" if annotation else "",
                    "(disabled)" if not sync.enabled else "",
                ]
                if part
            )
            child = tree.add(label, style="dim" if not sync.enabled else "")

            if dst_slug not in visited:
                visited.add(dst_slug)
                _add_children(child, dst_slug, visited)

    trees: list[RenderableType] = []
    for root in sorted(roots):
        tree = Tree(f"[bold]{root}[/bold]")
        visited: set[str] = {root}
        _add_children(tree, root, visited)
        trees.append(tree)
    return trees


def format_rich_tree_graph(config: Config) -> Group:
    """Format the sync graph as Rich trees, one per root endpoint, without icons."""
    return Group(*build_rich_tree_sections(config))


def format_mermaid_graph(config: Config) -> Text:
    """
    Return the exact mermaid text, ready to pipe into a renderer.

    A ``Text`` is never parsed for markup or emoji codes, nor highlighted. Print it with
    ``soft_wrap=True`` so long lines are neither wrapped nor cropped.
    """
    return Text(build_mermaid_graph(config))


def build_graph_json(config: Config) -> dict[str, list[dict[str, object]]]:
    """
    Return the sync graph as JSON-ready data for ``--output json``.

    ``nodes`` holds each sync endpoint a sync uses, sorted by slug; ``edges``
    holds each sync, from its source to its destination endpoint.
    """
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
