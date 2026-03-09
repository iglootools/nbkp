#!/bin/bash
set -e

# Create btrfs filesystem on a loopback device
truncate -s 128M /srv/btrfs-backups.img
mkfs.btrfs -f /srv/btrfs-backups.img
mkdir -p /srv/btrfs-backups
mount -o user_subvol_rm_allowed /srv/btrfs-backups.img /srv/btrfs-backups

# Install project dependencies
poetry install --quiet --no-interaction

# Run the command passed as arguments (default: pytest)
exec "$@"
