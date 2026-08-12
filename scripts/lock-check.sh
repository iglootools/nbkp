#!/usr/bin/env bash
# Check that mise.lock is up to date with mise.toml.
#
# Shared across iglootools projects. The canonical copy is scripts/lock-check.sh in
# iglootools/common-guidelines, alongside the guideline that explains it; project copies are
# verbatim, so fix it there and re-copy rather than editing one in place.
#
# Unlike `uv lock --check`, mise has no read-only freshness check, so this regenerates the
# lockfile and compares. Doing that naively is a trap; each guard below was earned by hitting
# the corresponding failure:
#
#  - Regenerates into a scratch copy, never in place. `mise lock` always writes, and undoing
#    that with `git checkout mise.lock` discards the regenerated lockfile that the error
#    message tells you to commit.
#
#  - Reads its inputs from the git index, not the working tree. mise rewrites mise.lock on
#    its own: with [settings] lockfile = true, any tool-resolving command updates the version
#    stanza and drops the per-platform checksums it can no longer vouch for — which is
#    precisely the state that makes `mise install --locked` fail on a fresh runner. The
#    working copy is therefore not a stable reference, so "up to date" can only mean "matches
#    what is staged". Reading the index also makes this immune to CI's
#    `mise use python@<matrix>`, which rewrites the working copy of mise.toml.
#
#  - Unsets MISE_PYTHON_VERSION, which `mise lock` honours — otherwise an env-selected matrix
#    interpreter would be locked in place of the committed pin.
#
#  - Uses `set -euo pipefail`. Without it, a failing `mise lock` leaves the copied lockfile
#    untouched, the diff finds no difference, and the check *passes* — the guard defeated by
#    the very situation it exists to catch.
#
# Usage:
#     mise run lock-check        # the usual entry point
#     scripts/lock-check.sh      # equivalent, runs from any directory in the repo

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

git show :mise.toml > "$tmp/mise.toml"
git show :mise.lock > "$tmp/staged.lock"
cp "$tmp/staged.lock" "$tmp/mise.lock"

# MISE_TRUSTED_CONFIG_PATHS rather than `mise trust`, so the scratch config is not added to
# the user's persistent trust store on every run.
(
    cd "$tmp"
    MISE_TRUSTED_CONFIG_PATHS="$tmp" env -u MISE_PYTHON_VERSION mise lock
)

if ! diff -u "$tmp/staged.lock" "$tmp/mise.lock" >&2; then
    echo "mise.lock is out of date. Run 'mise lock' and commit the result." >&2
    exit 1
fi
