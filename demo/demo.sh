#!/usr/bin/env bash
# Demo script for recording with asciinema + demo-magic.
#
# This script runs nbkp commands against a deterministic
# seed directory. It is designed to be executed inside
# an asciinema recording session:
#
#   asciinema rec --command ./demo/demo.sh demo/demo.cast
#   agg demo/demo.cast demo/demo.gif
#
# Requirements:
#   pip install nbkp  # or: poetry install (dev)
#   brew install pv   # for simulated typing

set -euo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=demo-magic.sh
source "$SCRIPT_DIR/demo-magic.sh"

# Auto-advance (no ENTER required) for scripted recording
NO_WAIT=true
TYPE_SPEED=40
DEMO_COMMENT_COLOR=$CYAN

DEMO_DIR="/tmp/nbkp-demo"
CFG="$DEMO_DIR/config.yaml"
SH="$DEMO_DIR/backup.sh"

# Pause between commands so the viewer can read the output
pause() { sleep "${1:-2}"; }

# ── Setup ────────────────────────────────────────────────
rm -rf "$DEMO_DIR"

p "# Seed demo data"
pe "nbkp demo seed --base-dir $DEMO_DIR --docker"
pause

p "# Show parsed configuration"
pe "nbkp config show --config $CFG"
pause 3

p "# Volume and sync health checks"
pe "nbkp check --config $CFG"
pause 3

p "# Preview what rsync would do (dry run)"
pe "nbkp run --config $CFG --dry-run"
pause 3

p "# Execute backup syncs"
pe "nbkp run --config $CFG"
pause 3

p "# Prune old snapshots"
pe "nbkp prune --config $CFG"
pause 3

p "# Generate standalone bash script"
pe "nbkp sh --config $CFG -o $SH"
pause

p "# Validate and run the generated script"
pe "bash -n $SH"
pe "$SH --dry-run"
pause 3
pe "$SH"
pause 3
