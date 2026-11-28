"""Rendering the sync dependency graph as a tree, mermaid text, ASCII art or JSON."""

from dataclasses import dataclass

from rich.console import Group, RenderableType
from rich.text import Text
from rich.tree import Tree

# Only `ordering graph -o mermaid-ascii` and `demo output` draw ASCII art.
# mermaid-ascii-diagrams leaves render_ascii's parameter unannotated; retire the
# pyright ignore when it annotates it.
lazy from mermaid_ascii import parse_mermaid, render_ascii  # pyright: ignore[reportUnknownVariableType]

from nbkp.config import Config, SyncConfig, SyncEndpoint
from nbkp.ordering.graph import build_adjacency
from nbkp.policy import Severity
from nbkp.theme import severity_icon


def _endpoint_annotation(ep: SyncEndpoint) -> str:
    match (ep.snapshot_mode, ep.max_snapshots):
        case ("none", _):
            return ""
        case (mode, None):
            return mode
        case (mode, max_s):
            return f"{mode}, max: {max_s}"


@dataclass(frozen=True)
class _Branch:
    """A sync, and the branches out of its destination endpoint."""

    sync: SyncConfig
    branches: tuple[_Branch, ...]


def _sync_forest(config: Config) -> list[tuple[str, tuple[_Branch, ...]]]:
    """
    Return each root endpoint, sorted, with the branches out of it.

    Config validation rejects self-syncs, shared destinations and dependency cycles,
    so the endpoints form a forest: each one is reached once, from a single root.
    """
    children, roots = build_adjacency(config.syncs)

    def branches(ep_slug: str) -> tuple[_Branch, ...]:
        return tuple(
            _Branch(sync, branches(sync.destination))
            for sync in children.get(ep_slug, [])
        )

    return [(root, branches(root)) for root in sorted(roots)]


def _depth_first(branches: tuple[_Branch, ...]) -> list[SyncConfig]:
    """Return the syncs in *branches*, each one before those downstream of it."""
    return [
        sync
        for branch in branches
        for sync in (branch.sync, *_depth_first(branch.branches))
    ]


def build_mermaid_graph(config: Config) -> str:
    """
    Return the sync graph as mermaid ``graph LR`` source.

    Nodes are sync endpoints and each edge is a sync labelled with its slug,
    walked depth-first from each root endpoint in turn.
    """
    return "\n".join(
        [
            "graph LR",
            *(
                f"    {sync.source} -->|{sync.slug}| {sync.destination}"
                for _, branches in _sync_forest(config)
                for sync in _depth_first(branches)
            ),
        ],
    )


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
    severities = sync_severities or {}

    def _add_branches(tree: Tree, branches: tuple[_Branch, ...]) -> None:
        for branch in branches:
            sync = branch.sync
            annotation = _endpoint_annotation(config.sync_endpoints[sync.destination])
            severity = severities.get(sync.slug)
            # Text, not markup: the slugs are the user's. Slug validation forbids
            # `[` today, but that is no reason to rely on it here.
            label = Text.assemble(
                *(
                    [Text.from_markup(severity_icon(severity)), " "]
                    if severity is not None
                    else []
                ),
                (sync.slug, "bold"),
                f" -> {sync.destination}",
                *([f" ({annotation})"] if annotation else []),
                *([" (disabled)"] if not sync.enabled else []),
            )
            _add_branches(
                tree.add(label, style="dim" if not sync.enabled else ""),
                branch.branches,
            )

    def _tree(root: str, branches: tuple[_Branch, ...]) -> Tree:
        tree = Tree(Text(root, style="bold"))
        _add_branches(tree, branches)
        return tree

    return [_tree(root, branches) for root, branches in _sync_forest(config)]


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
