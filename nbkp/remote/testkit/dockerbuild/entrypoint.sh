#!/bin/bash
set -e

# Set up authorized keys from mounted file
if [ -f /mnt/ssh-authorized-keys ]; then
    cp /mnt/ssh-authorized-keys /home/testuser/.ssh/authorized_keys
    chmod 600 /home/testuser/.ssh/authorized_keys
    chown testuser:testuser /home/testuser/.ssh/authorized_keys
fi

if [ -z "$NBKP_BASTION_ONLY" ]; then
    # Constants — passed via environment from Python test fixtures
    # and the demo CLI to avoid hardcoding values here.
    BACKUP_PATH="${NBKP_BACKUP_PATH:-/srv/backups}"
    BTRFS_PATH="${NBKP_BTRFS_PATH:-/srv/btrfs-backups}"

    # Docker Desktop shares the Linux kernel across containers, so loop
    # devices and device mapper entries from previous runs can persist.
    # Clean them up before creating our own.
    losetup -D 2>/dev/null || true

    # Create btrfs filesystem on a file-backed image
    BTRFS_IMG="${BTRFS_PATH}.img"
    truncate -s 256M "$BTRFS_IMG"
    mkfs.btrfs -f "$BTRFS_IMG"
    mkdir -p "$BTRFS_PATH"
    mount -o user_subvol_rm_allowed "$BTRFS_IMG" "$BTRFS_PATH"

    # Create base directories
    mkdir -p "$BACKUP_PATH"

    # Set ownership
    chown -R testuser:testuser "$BACKUP_PATH"
    chown -R testuser:testuser "$BTRFS_PATH"

    # ── LUKS-encrypted file-backed volume ───────────────────────
    # Disabled by default to speed up container startup (~5-10s).
    # Tests that need LUKS trigger setup lazily via SSH
    # (calling /setup-luks.sh), or set NBKP_LUKS_ENABLED=1 to
    # run it at startup.
    LUKS_ENABLED="${NBKP_LUKS_ENABLED:-0}"
    if [ "$LUKS_ENABLED" = "1" ]; then
        /setup-luks.sh
    else
        echo "0" > /srv/luks-available
    fi
fi

# Generate SSH host keys if not present
ssh-keygen -A

# Start sshd in foreground
echo "Server listening"
exec /usr/sbin/sshd -D -e
