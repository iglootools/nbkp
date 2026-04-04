#!/bin/bash
# Set up a LUKS-encrypted file-backed btrfs volume for testing.
#
# Idempotent: exits immediately if LUKS was already set up
# (checks /srv/luks-available marker).
#
# Requires: cryptsetup, dm-crypt kernel module, privileged container.
# Called lazily by test fixtures that need encryption, or at container
# startup when NBKP_LUKS_ENABLED=1.
set -e

# Already set up?
if [ -f /srv/luks-available ] && [ "$(cat /srv/luks-available)" = "1" ]; then
    exit 0
fi

BTRFS_ENCRYPTED_PATH="${NBKP_BTRFS_ENCRYPTED_PATH:-/srv/btrfs-encrypted-backups}"
LUKS_PASSPHRASE="${NBKP_LUKS_PASSPHRASE:-test-passphrase}"
LUKS_MAPPER="${NBKP_LUKS_MAPPER_NAME:-test-encrypted}"

# Close any stale device mapper from a previous container —
# Docker Desktop shares the kernel across containers, so
# mapper entries can persist.
cryptsetup close "$LUKS_MAPPER" 2>/dev/null || true

NBKP_LUKS_AVAILABLE=0
if command -v cryptsetup >/dev/null 2>&1; then
    LUKS_IMG="${BTRFS_ENCRYPTED_PATH}.img"
    truncate -s 128M "$LUKS_IMG"
    # losetup may fail if loop device nodes are missing (Docker
    # Desktop on macOS).  Protect with || true since LUKS is
    # optional — tests skip when unavailable.
    LOOP_DEV=$(losetup --find --show "$LUKS_IMG" 2>/dev/null) || true
    if [ -n "$LOOP_DEV" ] && echo -n "$LUKS_PASSPHRASE" | cryptsetup luksFormat \
            --batch-mode --pbkdf pbkdf2 --pbkdf-force-iterations 1000 \
            "$LOOP_DEV" - 2>/dev/null; then
        LUKS_UUID=$(cryptsetup luksUUID "$LOOP_DEV")

        # Create /dev/disk/by-uuid symlink (normally maintained by udev)
        mkdir -p /dev/disk/by-uuid
        ln -sf "$LOOP_DEV" "/dev/disk/by-uuid/$LUKS_UUID"

        # Open LUKS, format as btrfs, set ownership, close — tests re-open
        echo -n "$LUKS_PASSPHRASE" | cryptsetup open \
            --type luks "$LOOP_DEV" "$LUKS_MAPPER" -
        mkfs.btrfs -f "/dev/mapper/$LUKS_MAPPER"

        # Create mount point, mount, prepare for testuser, umount
        mkdir -p "$BTRFS_ENCRYPTED_PATH"
        mount -o user_subvol_rm_allowed \
            "/dev/mapper/$LUKS_MAPPER" "$BTRFS_ENCRYPTED_PATH"
        chown testuser:testuser "$BTRFS_ENCRYPTED_PATH"
        umount "$BTRFS_ENCRYPTED_PATH"
        cryptsetup close "$LUKS_MAPPER"

        # Add fstab entry so `mount $BTRFS_ENCRYPTED_PATH` picks up
        # device and options automatically (mirrors how systemd unit
        # files supply options for the systemd strategy).
        echo "/dev/mapper/$LUKS_MAPPER $BTRFS_ENCRYPTED_PATH btrfs user_subvol_rm_allowed 0 0" \
            >> /etc/fstab

        # Save metadata for tests to read via SSH
        echo "$LUKS_UUID" > /srv/luks-uuid
        echo "$LOOP_DEV" > /srv/luks-loop-device

        # Ensure cryptsetup is in the default PATH for non-root users
        # (it lives in /sbin/ or /usr/sbin/ which may not be in PATH)
        CRYPTSETUP_BIN=$(command -v cryptsetup)
        if [ -n "$CRYPTSETUP_BIN" ] && [ ! -e /usr/bin/cryptsetup ]; then
            ln -s "$CRYPTSETUP_BIN" /usr/bin/cryptsetup
        fi

        # Allow testuser passwordless sudo for mount operations.
        # File name must match SUDOERS_RULES_PATH in nbkp/mount/auth.py.
        cat > /etc/sudoers.d/nbkp <<SUDOERS
testuser ALL=(root) NOPASSWD: /sbin/cryptsetup *, /usr/sbin/cryptsetup *
testuser ALL=(root) NOPASSWD: /bin/mount $BTRFS_ENCRYPTED_PATH
testuser ALL=(root) NOPASSWD: /bin/umount $BTRFS_ENCRYPTED_PATH
SUDOERS
        chmod 440 /etc/sudoers.d/nbkp

        NBKP_LUKS_AVAILABLE=1
    else
        losetup -d "$LOOP_DEV" 2>/dev/null || true
        rm -f "$LUKS_IMG"
    fi
fi
echo "$NBKP_LUKS_AVAILABLE" > /srv/luks-available
