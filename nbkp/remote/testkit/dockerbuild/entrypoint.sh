#!/bin/bash
set -e

# Set up authorized keys from mounted file
if [ -f /mnt/ssh-authorized-keys ]; then
    cp /mnt/ssh-authorized-keys /home/testuser/.ssh/authorized_keys
    chmod 600 /home/testuser/.ssh/authorized_keys
    chown testuser:testuser /home/testuser/.ssh/authorized_keys
fi

if [ -z "$NBKP_BASTION_ONLY" ]; then
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
fi

# Generate SSH host keys if not present
ssh-keygen -A

# Start sshd in foreground
exec /usr/sbin/sshd -D -e
