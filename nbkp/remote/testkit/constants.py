"""Constants for Docker test container paths and LUKS test setup.

Separated from ``docker.py`` so that modules without the optional
``docker`` pip package can import path constants without pulling in
heavy dependencies.
"""

# ── Standard remote paths inside the test container ──────────

REMOTE_BACKUP_PATH = "/srv/backups"
REMOTE_BTRFS_PATH = "/srv/btrfs-backups"
REMOTE_BTRFS_ENCRYPTED_PATH = "/srv/btrfs-encrypted-backups"

# ── LUKS test constants ──────────────────────────────────────

LUKS_PASSPHRASE = "test-passphrase"
LUKS_MAPPER_NAME = "test-encrypted"
