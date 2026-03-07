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

### `check` — Check status of volumes and syncs

Verifies that volumes are reachable, sentinel files exist, SSH connectivity works, and required tools are available. Use this before `run` to confirm everything is ready.

```bash
nbkp check
nbkp check --config backup.yaml
nbkp check --output json
nbkp check --strict              # exit non-zero on any inactive sync
nbkp check --locations home      # prefer endpoints tagged "home"
nbkp check --private             # prefer LAN endpoints
```

| Option | Short | Description |
|---|---|---|
| `--config` | `-c` | Path to config file |
| `--output` | `-o` | Output format: `human` (default) or `json` |
| `--strict` / `--no-strict` | | Exit non-zero on any inactive sync (default: `--no-strict`) |
| `--locations` | `-l` | Prefer endpoints at these locations (repeatable) |
| `--private` | | Prefer private (LAN) endpoints |
| `--public` | | Prefer public (WAN) endpoints |

### `run` — Run backup syncs

Executes all active syncs in dependency order. Supports dry-run, progress display, snapshot creation, and automatic pruning.

```bash
nbkp run
nbkp run --dry-run
nbkp run --sync photos-to-nas    # run only this sync
nbkp run --progress overall      # show overall progress
nbkp run --no-prune              # skip snapshot pruning
nbkp run --locations travel --public
```

| Option | Short | Description |
|---|---|---|
| `--config` | `-c` | Path to config file |
| `--dry-run` | `-n` | Preview without making changes |
| `--sync` | `-s` | Run only specific sync(s) (repeatable) |
| `--output` | `-o` | Output format: `human` (default) or `json` |
| `--progress` | `-p` | Progress mode: `none`, `overall`, `per-file`, or `full` |
| `--prune` / `--no-prune` | | Prune old snapshots after sync (default: `--prune`) |
| `--strict` / `--no-strict` | | Exit non-zero on any inactive sync (default: `--no-strict`) |
| `--locations` | `-l` | Prefer endpoints at these locations (repeatable) |
| `--private` | | Prefer private (LAN) endpoints |
| `--public` | | Prefer public (WAN) endpoints |

### `sh` — Generate a standalone backup shell script

Compiles the config into a self-contained bash script. The generated script performs the same operations as `run` without requiring Python or the config file at runtime.

```bash
nbkp sh                                      # print to stdout
nbkp sh -o backup.sh                         # write to file (made executable)
nbkp sh -o /mnt/backups/backup.sh --relative-dst  # portable paths
```

The generated script supports `--dry-run` (`-n`) and `--verbose` (`-v`, `-vv`, `-vvv`) as runtime flags.

| Option | Short | Description |
|---|---|---|
| `--config` | `-c` | Path to config file |
| `--output-file` | `-o` | Write script to file (made executable) |
| `--relative-src` | | Make source paths relative to script location (requires `-o`) |
| `--relative-dst` | | Make destination paths relative to script location (requires `-o`) |
| `--locations` | `-l` | Prefer endpoints at these locations (repeatable) |
| `--private` | | Prefer private (LAN) endpoints |
| `--public` | | Prefer public (WAN) endpoints |
| `--portable/--no-portable` | | Generate bash 3.2-compatible script (default: enabled) |

### `prune` — Prune old snapshots

Removes snapshots beyond the `max-snapshots` limit. Normally handled automatically by `run`, but can be invoked manually.

```bash
nbkp prune
nbkp prune --dry-run             # preview what would be deleted
nbkp prune --sync photos-to-usb  # prune only this sync
```

| Option | Short | Description |
|---|---|---|
| `--config` | `-c` | Path to config file |
| `--sync` | `-s` | Prune only specific sync(s) (repeatable) |
| `--dry-run` | `-n` | Preview without deleting |
| `--output` | `-o` | Output format: `human` (default) or `json` |
| `--locations` | `-l` | Prefer endpoints at these locations (repeatable) |
| `--private` | | Prefer private (LAN) endpoints |
| `--public` | | Prefer public (WAN) endpoints |

### `troubleshoot` — Diagnose issues

Runs the same checks as `check` but displays step-by-step fix instructions for every failure. Useful when `check` reports problems.

```bash
nbkp troubleshoot
nbkp troubleshoot --config backup.yaml
```

| Option | Short | Description |
|---|---|---|
| `--config` | `-c` | Path to config file |
| `--locations` | `-l` | Prefer endpoints at these locations (repeatable) |
| `--private` | | Prefer private (LAN) endpoints |
| `--public` | | Prefer public (WAN) endpoints |

### `config show` — Display parsed configuration

Loads, validates, and renders the config as tables or JSON. Useful for verifying that inheritance, filters, and cross-references resolve correctly.

```bash
nbkp config show
nbkp config show --output json
```

| Option | Short | Description |
|---|---|---|
| `--config` | `-c` | Path to config file |
| `--output` | `-o` | Output format: `human` (default) or `json` |

### `demo` — Testing and QA helpers

```bash
nbkp demo output                 # render all output formats with sample data
nbkp demo seed                   # create local test environment
nbkp demo seed --docker          # create Docker environment with bastion + storage
```

## Examples

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
    path: /home/user

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

syncs:
  photos-to-nas:
    source:
      volume: laptop
      subdir: photos
    destination:
      volume: nas-backups
      subdir: photos
      hard-link-snapshots:
        enabled: true
        max-snapshots: 30
    filters:
      - include: "*.jpg"
      - include: "*.png"
      - include: "*.heic"
      - include: "*.mp4"
      - exclude: "*.tmp"
      - exclude: ".thumbs/"

  documents-to-nas:
    source:
      volume: laptop
      subdir: documents
    destination:
      volume: nas-backups
      subdir: documents
    rsync-options:
      compress: true

  documents-to-usb:
    source:
      volume: laptop
      subdir: documents
    destination:
      volume: usb-drive
      hard-link-snapshots:
        enabled: true
        max-snapshots: 10
```

Usage:

```bash
# At home
nbkp run --locations home
nbkp run --private

# On the road
nbkp run --locations travel
nbkp run --public

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

syncs:
  # Step 1: local → local with hard-link snapshots
  step-1:
    source:
      volume: src-local
    destination:
      volume: stage-local
      hard-link-snapshots:
        enabled: true
        max-snapshots: 7

  # Step 2: local → remote (through bastion), bare
  step-2:
    source:
      volume: stage-local
      hard-link-snapshots:       # read from latest/ snapshot
        enabled: true
    destination:
      volume: stage-remote-bare
    rsync-options:
      compress: true

  # Step 3: remote → remote (same server), btrfs snapshots
  step-3:
    source:
      volume: stage-remote-bare
    destination:
      volume: stage-remote-btrfs
      btrfs-snapshots:
        enabled: true
        max-snapshots: 14

  # Step 4: remote → remote (same server), bare on btrfs
  step-4:
    source:
      volume: stage-remote-btrfs
      btrfs-snapshots:           # read from latest/ snapshot
        enabled: true
    destination:
      volume: stage-remote-btrfs-bare

  # Step 5: remote → remote (same server), hard-link snapshots
  step-5:
    source:
      volume: stage-remote-btrfs-bare
    destination:
      volume: stage-remote-hl
      hard-link-snapshots:
        enabled: true
        max-snapshots: 5

  # Step 6: remote → local, bare
  step-6:
    source:
      volume: stage-remote-hl
      hard-link-snapshots:       # read from latest/ snapshot
        enabled: true
    destination:
      volume: dst-local
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

## Config File Location

nbkp searches for config in this order:

1. Explicit `--config` path
2. `$XDG_CONFIG_HOME/nbkp/config.yaml` (typically `~/.config/nbkp/config.yaml`)
3. `/etc/nbkp/config.yaml`

## Configuration Reference

### Top-level structure

```yaml
ssh-endpoints:
  <slug>: <SshEndpoint>

volumes:
  <slug>: <LocalVolume | RemoteVolume>

syncs:
  <slug>: <SyncConfig>
```

Slugs must be lowercase alphanumeric with hyphens (e.g. `my-nas`, `usb-drive`). Max 50 characters.

---

### `ssh-endpoints.<slug>` — SSH Endpoint

| Field | Type | Default | Description |
|---|---|---|---|
| `host` | string | **required** | Hostname or SSH config alias |
| `port` | integer (1-65535) | `22` | SSH port |
| `user` | string | `null` | SSH username |
| `key` | string | `null` | Path to private key file |
| `proxy-jump` | string | `null` | Slug of another endpoint to use as bastion |
| `proxy-jumps` | list of strings | `null` | Slugs for multi-hop proxy chain (mutually exclusive with `proxy-jump`) |
| `location` | string | `null` | Network location tag (e.g. `home`, `travel`) |
| `locations` | list of strings | `null` | Multiple location tags (mutually exclusive with `location`) |
| `extends` | string | `null` | Slug of parent endpoint to inherit from |
| `connection-options` | object | see below | SSH connection options |

Fields not explicitly set (`port`, `user`, `key`) are automatically filled from `~/.ssh/config`. See [SSH Endpoints](./concepts.md#ssh-endpoints).

---

### `connection-options` — SSH Connection Options

| Field | Type | Default | Description |
|---|---|---|---|
| `connect-timeout` | integer (>= 1) | `10` | Connection timeout in seconds |
| `compress` | boolean | `false` | Enable SSH compression |
| `server-alive-interval` | integer (>= 1) | `null` | Keepalive interval in seconds |
| `allow-agent` | boolean | `true` | Use SSH agent for authentication |
| `look-for-keys` | boolean | `true` | Search `~/.ssh/` for keys |
| `banner-timeout` | float (>= 0) | `null` | Wait time for SSH banner |
| `auth-timeout` | float (>= 0) | `null` | Wait time for auth response |
| `channel-timeout` | float (>= 0) | `null` | Wait time for channel open (Paramiko/Fabric only) |
| `strict-host-key-checking` | boolean | `true` | Verify remote host key |
| `known-hosts-file` | string | `null` | Custom known hosts file path |
| `forward-agent` | boolean | `false` | Enable SSH agent forwarding |
| `disabled-algorithms` | object | `null` | Disable specific SSH algorithms (Paramiko/Fabric only) |

---

### `volumes.<slug>` — Local Volume

| Field | Type | Default | Description |
|---|---|---|---|
| `type` | `"local"` | **required** | Volume type discriminator |
| `path` | string | **required** | Absolute path to the volume |

### `volumes.<slug>` — Remote Volume

| Field | Type | Default | Description |
|---|---|---|---|
| `type` | `"remote"` | **required** | Volume type discriminator |
| `ssh-endpoint` | string | **required** | Primary SSH endpoint slug |
| `ssh-endpoints` | list of strings | `null` | Candidate endpoints for auto-selection |
| `path` | string | **required** | Absolute path on the remote host |

---

### `syncs.<slug>` — Sync Config

| Field | Type | Default | Description |
|---|---|---|---|
| `source` | object | **required** | Source endpoint |
| `destination` | object | **required** | Destination endpoint |
| `enabled` | boolean | `true` | Whether this sync is active |
| `rsync-options` | object | see below | Rsync flag configuration |
| `filters` | list | `[]` | Rsync filter rules (see below) |
| `filter-file` | string | `null` | Path to external rsync filter file |

---

### Sync Endpoint (source / destination)

| Field | Type | Default | Description |
|---|---|---|---|
| `volume` | string | **required** | Volume slug |
| `subdir` | string | `null` | Subdirectory within the volume |
| `btrfs-snapshots` | object | disabled | Btrfs snapshot config |
| `hard-link-snapshots` | object | disabled | Hard-link snapshot config |

Only one of `btrfs-snapshots` and `hard-link-snapshots` can be enabled per endpoint.

When used as a **source**, enabling snapshots tells rsync to read from the `latest/` directory instead of the volume root. When used as a **destination**, enabling snapshots activates snapshot creation after each successful sync.

---

### `btrfs-snapshots` / `hard-link-snapshots`

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | boolean | `false` | Enable snapshot management |
| `max-snapshots` | integer (>= 1) | `null` | Maximum snapshots to keep (`null` = unlimited) |

---

### `rsync-options` — Rsync Options

| Field | Type | Default | Description |
|---|---|---|---|
| `compress` | boolean | `false` | Enable rsync `--compress` |
| `checksum` | boolean | `true` | Enable rsync `--checksum` |
| `default-options-override` | list of strings | `null` | Replace default rsync flags entirely |
| `extra-options` | list of strings | `[]` | Additional flags appended after defaults |

Default rsync flags (when `default-options-override` is not set):

```
-a --delete --delete-excluded --partial-dir=.rsync-partial --safe-links
```

---

### Filters

Filters can be specified as structured rules or raw rsync filter strings, mixed freely:

```yaml
filters:
  # Structured rules
  - include: "*.jpg"          # becomes "+ *.jpg"
  - exclude: "*.tmp"          # becomes "- *.tmp"

  # Raw rsync filter strings
  - "H .git"                  # hide .git from transfer
  - "- __pycache__/"          # exclude __pycache__
```

Filters are applied in order as `--filter=RULE` arguments. When `filter-file` is also set, inline filters are applied first.
