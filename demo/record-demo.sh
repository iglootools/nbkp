#!/usr/bin/env bash
# Record the demo and convert to GIF.
#
# Usage:
#   ./demo/record-demo.sh [VERSION]
#
# Requirements:
#   brew install asciinema agg pv   # macOS
#   # or: pip install asciinema && cargo install agg
#
# Output:
#   demo/demo.cast  — asciinema recording
#   demo/demo.gif   — GIF for README

set -euo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# The version is normally passed in by `mise run demo-record`. The fallback reads it from
# the installed distribution rather than asking the build tool, since the version is
# VCS-derived and only materializes in .dist-info at install time.
VERSION="${1:-$(uv run --no-sync python -c 'import nbkp; print(nbkp.__version__)' 2>/dev/null || echo "")}"
TITLE="nbkp ${VERSION:+ v$VERSION} demo"
CAST="$SCRIPT_DIR/demo.cast"
GIF="$SCRIPT_DIR/demo.gif"

echo "Syncing dependencies..."
uv sync --all-extras

echo "Recording demo..."
asciinema rec \
    --overwrite \
    --title "$TITLE" \
    --window-size 160x45 \
    --command "$SCRIPT_DIR/demo.sh" \
    "$CAST"

# echo "Converting to GIF..."
# agg \
#     --font-size 14 \
#     --theme asciinema \
#     "$CAST" \
#     "$GIF"

echo "Done: $GIF"
