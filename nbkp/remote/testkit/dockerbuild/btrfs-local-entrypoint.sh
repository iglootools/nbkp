#!/bin/bash
set -e

# Create btrfs filesystem on a loopback device
truncate -s 128M /srv/btrfs-backups.img
mkfs.btrfs -f /srv/btrfs-backups.img
mkdir -p /srv/btrfs-backups
mount -o user_subvol_rm_allowed /srv/btrfs-backups.img /srv/btrfs-backups

# /app is a bind mount owned by the host user. On a Linux host, git refuses to read it as
# root ("detected dubious ownership") — and because fallback-version is set, the version
# lookup would then silently resolve to 0.0.0 instead of failing. Harmless on Docker Desktop
# for Mac, where bind-mounted files present as owned by the container user.
git config --global --add safe.directory /app

# Install project dependencies into UV_PROJECT_ENVIRONMENT (/tmp/venv, set in the Dockerfile),
# never the host's bind-mounted .venv. --frozen so the container cannot rewrite the host's
# uv.lock; --all-extras to match the host-side [deps.uv] command.
#
# import-linter and grimp are skipped: only the host-side boundaries-check uses them, and
# grimp publishes no linux-aarch64 wheel for Python 3.15, which this image (no C toolchain)
# cannot build — so on an Apple Silicon host the whole sync would fail. Drop the two
# --no-install-package flags once grimp ships that wheel.
uv sync --frozen --all-extras --quiet \
    --no-install-package grimp --no-install-package import-linter

# Run the command passed as arguments (default: pytest)
exec "$@"
