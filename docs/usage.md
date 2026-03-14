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

```yaml
ssh-endpoints:
  nas:
    host: nas                     # matches "Host nas" in ~/.ssh/config
    location: home

  nas-public:
    extends: nas                  # inherits all fields from nas
    host: nas.example.com         # override with public hostname
    location: travel

volumes:
  laptop:
    type: local
    path: "~"                     # quote ~ (YAML treats bare ~ as null)

  usb-drive:
    type: local
    path: /mnt/usb-backup

  nas-backups:
    type: remote
    ssh-endpoint: nas             # or, for specifying multiple endpoints:
    ssh-endpoints:                # candidates for auto-selection
      - nas
      - nas-public
    path: /volume1/backups

sync-endpoints:
  laptop-photos:
    volume: laptop
    subdir: photos

  laptop-documents:
    volume: laptop
    subdir: documents

  nas-photos:
    volume: nas-backups
    subdir: photos
    hard-link-snapshots:
      enabled: true
      max-snapshots: 30

  nas-documents:
    volume: nas-backups
    subdir: documents

  usb-documents:
    volume: usb-drive
    hard-link-snapshots:
      enabled: true
      max-snapshots: 10

syncs:
  photos-to-nas:
    source: laptop-photos
    destination: nas-photos
    filters:
      - include: "*.jpg"
      - include: "*.png"
      - include: "*.heic"
      - include: "*.mp4"
      - exclude: "*.tmp"
      - exclude: ".thumbs/"

  documents-to-nas:
    source: laptop-documents
    destination: nas-documents
    rsync-options:
      compress: true

  documents-to-usb:
    source: laptop-documents
    destination: usb-documents
```

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

```yaml
ssh-endpoints:
  bastion:
    host: bastion.example.com
    user: admin
    connection-options:
      server-alive-interval: 60

  storage:
    host: storage.internal
    port: 2222
    user: backup
    key: ~/.ssh/storage_ed25519
    proxy-jump: bastion
    connection-options:
      strict-host-key-checking: false
      known-hosts-file: /dev/null
      connect-timeout: 30

volumes:
  # Local volumes
  src-local:
    type: local
    path: /mnt/data/source

  stage-local:
    type: local
    path: /mnt/data/stage

  dst-local:
    type: local
    path: /mnt/data/final

  # Remote volumes (all on the same server, via bastion)
  stage-remote-bare:
    type: remote
    ssh-endpoint: storage
    path: /srv/backups/bare

  stage-remote-btrfs:
    type: remote
    ssh-endpoint: storage
    path: /srv/btrfs-pool/snapshots

  stage-remote-btrfs-bare:
    type: remote
    ssh-endpoint: storage
    path: /srv/btrfs-pool/bare

  stage-remote-hl:
    type: remote
    ssh-endpoint: storage
    path: /srv/backups/hl

sync-endpoints:
  ep-src:
    volume: src-local

  # Hard-link snapshots on local stage — used as destination for step-1
  # and as source for step-2 (reads from latest/ snapshot)
  ep-stage-local:
    volume: stage-local
    hard-link-snapshots:
      enabled: true
      max-snapshots: 7

  ep-remote-bare:
    volume: stage-remote-bare

  # Btrfs snapshots on remote — used as destination for step-3
  # and as source for step-4 (reads from latest/ snapshot)
  ep-remote-btrfs:
    volume: stage-remote-btrfs
    btrfs-snapshots:
      enabled: true
      max-snapshots: 14

  ep-remote-btrfs-bare:
    volume: stage-remote-btrfs-bare

  # Hard-link snapshots on remote — used as destination for step-5
  # and as source for step-6 (reads from latest/ snapshot)
  ep-remote-hl:
    volume: stage-remote-hl
    hard-link-snapshots:
      enabled: true
      max-snapshots: 5

  ep-dst:
    volume: dst-local

syncs:
  # Step 1: local → local with hard-link snapshots
  step-1:
    source: ep-src
    destination: ep-stage-local

  # Step 2: local → remote (through bastion), bare
  step-2:
    source: ep-stage-local        # reads from latest/ snapshot
    destination: ep-remote-bare
    rsync-options:
      compress: true

  # Step 3: remote → remote (same server), btrfs snapshots
  step-3:
    source: ep-remote-bare
    destination: ep-remote-btrfs

  # Step 4: remote → remote (same server), bare on btrfs
  step-4:
    source: ep-remote-btrfs       # reads from latest/ snapshot
    destination: ep-remote-btrfs-bare

  # Step 5: remote → remote (same server), hard-link snapshots
  step-5:
    source: ep-remote-btrfs-bare
    destination: ep-remote-hl

  # Step 6: remote → local, bare
  step-6:
    source: ep-remote-hl          # reads from latest/ snapshot
    destination: ep-dst
    rsync-options:
      compress: true
```

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


