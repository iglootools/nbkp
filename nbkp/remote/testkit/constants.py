"""Constants for Docker test container paths and LUKS test setup.

Separated from ``docker.py`` so that modules without the optional
``docker`` pip package can import path constants without pulling in
heavy dependencies.
"""

REMOTE_BACKUP_PATH = "/srv/backups"
REMOTE_BTRFS_PATH = "/srv/btrfs-backups"
REMOTE_BTRFS_ENCRYPTED_PATH = "/srv/btrfs-encrypted-backups"
# Plain (unencrypted) ext4 volume for the udisks unencrypted-mount test.
REMOTE_UNENCRYPTED_PATH = "/srv/unencrypted-backups"

LUKS_PASSPHRASE = "test-passphrase"
LUKS_MAPPER_NAME = "test-encrypted"
