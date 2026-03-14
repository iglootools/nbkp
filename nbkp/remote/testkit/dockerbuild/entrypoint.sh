#!/bin/bash
set -e

# Set up authorized keys from mounted file
if [ -f /mnt/ssh-authorized-keys ]; then
    cp /mnt/ssh-authorized-keys /home/testuser/.ssh/authorized_keys
    chmod 600 /home/testuser/.ssh/authorized_keys
    chown testuser:testuser /home/testuser/.ssh/authorized_keys
fi

if [ -z "$NBKP_BASTION_ONLY" ]; then
    # Docker Desktop shares the Linux kernel across containers, so loop
    # devices and device mapper entries from previous runs can persist.
    # Clean them up before creating our own.
    losetup -D 2>/dev/null || true

    # Create btrfs filesystem on a file-backed image
    truncate -s 256M /srv/btrfs-backups.img
    mkfs.btrfs -f /srv/btrfs-backups.img
    mkdir -p /srv/btrfs-backups
    mount -o user_subvol_rm_allowed /srv/btrfs-backups.img /srv/btrfs-backups

    # Create base directories
    mkdir -p /srv/backups

    # Set ownership
    chown -R testuser:testuser /srv/backups
    chown -R testuser:testuser /srv/btrfs-backups

    # ── LUKS-encrypted file-backed volume ───────────────────────
    # Creates a LUKS volume for mount management integration tests.
    # Requires dm-crypt kernel module (may not be available on all
    # Docker hosts, e.g. macOS Docker Desktop).
    #
    # Close any stale device mapper from a previous container —
    # Docker Desktop shares the kernel across containers, so
    # mapper entries can persist.
    cryptsetup close test-encrypted 2>/dev/null || true

    NBKP_LUKS_AVAILABLE=0
    if command -v cryptsetup >/dev/null 2>&1; then
        truncate -s 128M /srv/luks-encrypted.img
        # losetup may fail if loop device nodes are missing (Docker
        # Desktop on macOS).  Protect with || true since LUKS is
        # optional — tests skip when unavailable.
        LOOP_DEV=$(losetup --find --show /srv/luks-encrypted.img 2>/dev/null) || true
        if [ -n "$LOOP_DEV" ] && echo -n "test-passphrase" | cryptsetup luksFormat \
                --batch-mode --pbkdf pbkdf2 --pbkdf-force-iterations 1000 \
                "$LOOP_DEV" - 2>/dev/null; then
            LUKS_UUID=$(cryptsetup luksUUID "$LOOP_DEV")

            # Create /dev/disk/by-uuid symlink (normally maintained by udev)
            mkdir -p /dev/disk/by-uuid
            ln -sf "$LOOP_DEV" "/dev/disk/by-uuid/$LUKS_UUID"

            # Open LUKS, format as btrfs, set ownership, close — tests re-open
            echo -n "test-passphrase" | cryptsetup open \
                --type luks "$LOOP_DEV" test-encrypted -
            mkfs.btrfs -f /dev/mapper/test-encrypted

            # Create mount point, mount, prepare for testuser, umount
            mkdir -p /srv/btrfs-encrypted-backups
            mount -o user_subvol_rm_allowed \
                /dev/mapper/test-encrypted /srv/btrfs-encrypted-backups
            chown testuser:testuser /srv/btrfs-encrypted-backups
            umount /srv/btrfs-encrypted-backups
            cryptsetup close test-encrypted

            # Add fstab entry so `mount /srv/btrfs-encrypted-backups` picks up
            # device and options automatically (mirrors how systemd unit
            # files supply options for the systemd strategy).
            echo "/dev/mapper/test-encrypted /srv/btrfs-encrypted-backups btrfs user_subvol_rm_allowed 0 0" \
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
            cat > /etc/sudoers.d/nbkp <<'SUDOERS'
testuser ALL=(root) NOPASSWD: /sbin/cryptsetup *, /usr/sbin/cryptsetup *
testuser ALL=(root) NOPASSWD: /bin/mount /srv/btrfs-encrypted-backups
testuser ALL=(root) NOPASSWD: /bin/umount /srv/btrfs-encrypted-backups
SUDOERS
            chmod 440 /etc/sudoers.d/nbkp

            NBKP_LUKS_AVAILABLE=1
        else
            losetup -d "$LOOP_DEV" 2>/dev/null || true
            rm -f /srv/luks-encrypted.img
        fi
    fi
    echo "$NBKP_LUKS_AVAILABLE" > /srv/luks-available
fi

# Generate SSH host keys if not present
ssh-keygen -A

# Start sshd in foreground
exec /usr/sbin/sshd -D -e
