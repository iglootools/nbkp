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
VERSION="${1:-$(poetry version -s 2>/dev/null || echo "")}"
TITLE="nbkp ${VERSION:+ v$VERSION} demo"
CAST="$SCRIPT_DIR/demo.cast"
GIF="$SCRIPT_DIR/demo.gif"

echo "Running poetry install..."
poetry install

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
