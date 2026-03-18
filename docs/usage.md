# Usage

## Installation

See [Installation instructions](./installation.md)

## Quick Start

1. Create a config file at `~/.config/nbkp/config.yaml` (see [examples](#examples) below)
2. Create sentinel files on each volume:

For regular destinations (without snapshots):

```bash
# On each volume root (source and destination)
touch /mnt/data/.nbkp-vol
touch /mnt/backups/.nbkp-vol

# On source subdirectories
touch /mnt/data/photos/.nbkp-src

# On destination subdirectories (or volume root if no subdir)
mkdir -p /mnt/backups/photos
touch /mnt/backups/photos/.nbkp-dst
```

For **btrfs snapshot** destinations, create the staging subvolume and snapshots directory:

```bash
sudo btrfs subvolume create /mnt/backups/photos/staging
sudo mkdir /mnt/backups/photos/snapshots
sudo chown <user>:<group> /mnt/backups/photos/staging /mnt/backups/photos/snapshots
```

The btrfs volume must also be mounted with `user_subvol_rm_allowed` for pruning to work:

```bash
sudo mount -o remount,user_subvol_rm_allowed /mnt/backups
```

For **hard-link snapshot** destinations, create the snapshots directory:

```bash
mkdir -p /mnt/backups/photos/snapshots
```


3. Verify everything is healthy:

```bash
nbkp check
```

4. Run the backup:

```bash
nbkp run
```

## Commands

See the [CLI Reference](./cli-reference.md) for the full list of commands and options.

## Config

### Config File Location

nbkp searches for config in this order:

1. Explicit `--config` path
2. `$XDG_CONFIG_HOME/nbkp/config.yaml` (typically `~/.config/nbkp/config.yaml`)
3. Platform user config dir (Linux: same as above; macOS: `~/Library/Application Support/nbkp/config.yaml`)
4. Platform site config dir (Linux: `/etc/xdg/nbkp/config.yaml`; macOS: `/Library/Application Support/nbkp/config.yaml`)

### Configuration Reference

See the [Configuration Reference](./config-reference.md) for the full schema documentation.


### Example 1: Home NAS backup

A laptop backing up photos and documents to a Linux-powered NAS, with a USB drive as a secondary destination. The NAS is accessible both on the home LAN and remotely via a public hostname.

See [config-examples/home-nas-backup.yaml](config-examples/home-nas-backup.yaml).

Usage:

```bash
# At home
nbkp run --location home
nbkp run --network private

# On the road — include travel endpoints
nbkp run --location travel
nbkp run --network public

# On the road — exclude home endpoints
nbkp run --exclude-location home

# Only backup photos
nbkp run --sync photos-to-nas

# Preview what would happen
nbkp run --dry-run

# Generate a portable script for the USB drive
nbkp sh -o /mnt/usb-backup/backup.sh --relative-dst
```

### Example 2: Multi-hop chained backups

A more complex setup with a bastion host, chained syncs across local and remote volumes, mixed snapshot modes (btrfs and hard-link), and strict connection options. Data flows through a 6-step pipeline: local source, through a bastion to a remote server, across different snapshot backends, and back to a local destination.

See [config-examples/multi-hop-chain.yaml](config-examples/multi-hop-chain.yaml).

The syncs form a chain: `step-1 → step-2 → step-3 → step-4 → step-5 → step-6`. nbkp detects these dependencies automatically and runs them in order. If any step fails, all downstream steps are cancelled.

Usage:

```bash
# Run the full chain
nbkp run

# Dry-run with per-file progress
nbkp run --dry-run --progress per-file

# Prune old snapshots manually
nbkp prune --sync step-1 --sync step-3

# Diagnose issues
nbkp troubleshoot

# Generate standalone script
nbkp sh -o backup.sh
```

### Example 3: Encrypted removable drives with mount management

A laptop backing up to two LUKS-encrypted external drives and one unencrypted USB drive. nbkp automatically unlocks, mounts, syncs, umounts, and locks the drives.

See [config-examples/encrypted-removable-drives.yaml](config-examples/encrypted-removable-drives.yaml).

System setup (one-time, on the target host):

```bash
# 1. Store passphrases in keyring
keyring set nbkp seagate8tb
keyring set nbkp backup-drives

# 2. Configure fstab and crypttab
# /etc/crypttab:
#   seagate8tb UUID=5941f273-f73c-44c5-a3ef-fae7248db1b6 none luks,noauto
#   iomega1tb  UUID=ad5542e5-5365-4951-a1f2-fe81c4d6fe43 none luks,noauto
# /etc/fstab:
#   /dev/mapper/seagate8tb /mnt/seagate8tb btrfs defaults,noauto 0 0
#   /dev/mapper/iomega1tb  /mnt/iomega1tb  ext4  defaults,noauto 0 0
#   UUID=8a3b2c1d-...      /mnt/usb-plain  ext4  defaults,noauto 0 0
sudo systemctl daemon-reload

# 3. Generate and install authorization rules
nbkp config setup-auth -c config.yaml
# Review output, then install:
# sudo cp polkit-rules /etc/polkit-1/rules.d/50-nbkp.rules
# sudo visudo -f /etc/sudoers.d/nbkp  # paste sudoers content

# 4. Create sentinel files
touch /mnt/seagate8tb/.nbkp-vol /mnt/iomega1tb/.nbkp-vol /mnt/usb-plain/.nbkp-vol
```

Usage:

```bash
# Run with automatic mount/umount (default)
nbkp run

# Run without mount management (volumes must be pre-mounted)
nbkp run --no-mount --no-umount

# Manually mount/umount specific volumes
nbkp volumes mount --name seagate8tb
nbkp volumes umount --name seagate8tb

# Diagnose mount issues
nbkp troubleshoot
```

### Example 4: Sami's Personal Config

A real-world example of a complex multi-volume, multi-snapshot backup setup.

See [config-examples/personal-setup.yaml](config-examples/personal-setup.yaml).