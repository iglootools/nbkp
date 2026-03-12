"""Generate a mermaid module dependency graph from pydeps DOT output.

Requires: pydeps (dev dependency), graphviz (system: brew install graphviz).

Usage:
    python scripts/depgraph.py [--format mermaid|ascii]
"""

from __future__ import annotations

import enum
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer

PACKAGE = "nbkp"


def _is_package(label: str) -> bool:
    """Check if a module label corresponds to a package (directory with __init__.py)."""
    return (Path(PACKAGE) / label / "__init__.py").exists()


def _run_pydeps(package: str = PACKAGE) -> str:
    """Run pydeps and return DOT output."""
    if not shutil.which("dot"):
        print(
            "Error: graphviz is not installed.\n"
            "  macOS:  brew install graphviz\n"
            "  Linux:  apt install graphviz",
            file=sys.stderr,
        )
        sys.exit(1)

    result = subprocess.run(
        [
            "pydeps",
            package,
            "--show-dot",
            "--no-output",
            "--no-show",
            "--max-module-depth",
            "2",
            "--only",
            package,
            "--rmprefix",
            f"{package}.",
            "--exclude",
            f"{package}.templates",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"pydeps failed:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    return result.stdout


def _dot_to_mermaid(dot: str) -> str:
    """Convert pydeps DOT output to mermaid graph TD syntax.

    pydeps edges point from dependency to dependent (A -> B means "A is used by B"),
    so we reverse them to match the convention "B depends on A" (B --> A).
    """
    # Extract node declarations: node_id [... label="name" ...]
    node_pattern = re.compile(r'^\s+(\w+)\s+\[.*?label="([^"]+)".*?\]', re.MULTILINE)
    nodes: dict[str, str] = {}
    for match in node_pattern.finditer(dot):
        node_id, label = match.group(1), match.group(2)
        nodes[node_id] = label

    # Extract edges: src -> dst
    edge_pattern = re.compile(r"^\s+(\w+)\s+->\s+(\w+)", re.MULTILINE)
    edges: list[tuple[str, str]] = []
    for match in edge_pattern.finditer(dot):
        dep, dependent = match.group(1), match.group(2)
        # Reverse: dependent depends on dep
        edges.append((nodes.get(dependent, dependent), nodes.get(dep, dep)))

    # Build mermaid
    lines = ["graph TD"]

    # Node declarations (sorted for determinism)
    for node_id in sorted(nodes):
        label = nodes[node_id]
        display = f"{label}/" if _is_package(label) else label
        lines.append(f'    {label}["{display}"]')

    lines.append("")

    # Edges (sorted for determinism)
    for src, dst in sorted(edges):
        lines.append(f"    {src} --> {dst}")

    return "\n".join(lines)


def _render_ascii(mermaid_src: str) -> str:
    """Render mermaid to ASCII via mermaid-ascii-diagrams."""
    from mermaid_ascii import parse_mermaid, render_ascii

    diagram = parse_mermaid(mermaid_src)
    return render_ascii(diagram)


class GraphFormat(str, enum.Enum):
    """Output format for the dependency graph."""

    MERMAID = "mermaid"
    ASCII = "ascii"


app = typer.Typer(add_completion=False)


@app.command()
def main(
    fmt: Annotated[
        GraphFormat,
        typer.Option("--format", "-f", help="Output format"),
    ] = GraphFormat.MERMAID,
) -> None:
    """Generate a module dependency graph from pydeps analysis."""
    dot = _run_pydeps()
    mermaid = _dot_to_mermaid(dot)

    match fmt:
        case GraphFormat.MERMAID:
            print(mermaid)
        case GraphFormat.ASCII:
            print(_render_ascii(mermaid))


if __name__ == "__main__":
    app()
