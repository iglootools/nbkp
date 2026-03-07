## Concepts

## Backup Config

Expressed in YAML. Sourced from the regular locations (e.g. `/etc/nbkp/config.yaml`, `~/.config/nbkp/config.yaml`, etc) and can also be provided as an argument when calling the backup tool.

### Sync

A sync describes a source and destination pair with the relevant config (type of the backup, source and destination paths, server, etc).
Both the source and destination can be local or remote, and can be on removable drives.
The only supported backup type for now is rsync, but other backup types will be added in the future (e.g. git, etc).

For a sync to be considered active, both the source and the destination must provide a `.nbkp-src` and `.nbkp-dst` file respectively.
For remote sources/destinations, the server must be reachable for the corresponding sync to be active.

This is to ensure that when using removable drives, both the source and destinations are currently mounted / available to prevent data loss
or backups to the wrong drives.

For the rsync backup type, the source and the destination can either be a rsync local or a rsync remote volume, and can specify a subdirectory on the volume.

Individual syncs can be enabled or disabled when calling the backup tool.

### Sync Dependencies and Failure Propagation

When one sync's destination matches another sync's source (same volume and subdir), a dependency exists between them. Syncs are automatically executed in topological order so that upstream data is always fresh before downstream syncs begin.

If a sync fails, all downstream syncs (directly or transitively) are automatically **cancelled** to prevent propagating partial or stale data through the chain. Cancelled syncs appear with a `CANCELLED` status in the output, along with the name of the failed upstream sync. Independent syncs (those with no dependency relationship) are unaffected and continue to run normally.

For example, in a chain `A → B → C` where A's destination is B's source and B's destination is C's source: if A fails, both B and C are cancelled. But a sync D that reads from an unrelated volume still executes.

The generated shell script (`nbkp sh`) implements the same failure propagation logic.

### BTRFS Snapshots

A sync can optionally enable btrfs snapshots, which will be used to perform incremental backups.
This is only supported for local sources and destinations that are on btrfs volumes.

Rsync writes to a staging area at `${destination}/staging/` (a writable btrfs subvolume). After each successful sync, a read-only btrfs snapshot is created at `${destination}/snapshots/${iso8601_timestamp}`, and a `${destination}/latest` symlink is updated to point to the new snapshot. Initially, `latest` points to `/dev/null` (meaning "no snapshot yet"); after the first successful sync it is updated to point to the real snapshot.

The `max-snapshots` field controls the maximum number of snapshots to keep. When set, old snapshots are automatically pruned after each `run`. The `prune` command can also be used to manually prune snapshots.

```yaml
destination:
  volume: usb-drive
  btrfs-snapshots:
    enabled: true
    max-snapshots: 10   # optional, omit for unlimited
```

### Hard-Link Snapshots

A sync can optionally enable hard-link-based snapshots as an alternative to btrfs snapshots. This works on any filesystem that supports hard links (ext4, xfs, btrfs, etc.) but not on FAT/exFAT.

Unlike btrfs snapshots (which sync to `${destination}/staging/` then snapshot it), hard-link snapshots sync **directly into a new snapshot directory**:

1. Create `${destination}/snapshots/${timestamp}/`
2. rsync into that directory with `--link-dest=../${previous-snapshot}` (unchanged files are hard-linked, saving disk space)
3. On success: update symlink `${destination}/latest` → `snapshots/${timestamp}`
4. Prune old snapshots with `rm -rf` (no btrfs commands needed)

**Safety:** The `latest` symlink is only updated after a successful sync. If a sync fails midway, `latest` still points to the previous complete snapshot. Orphaned snapshot directories (from failed syncs) are detected and cleaned up before the next sync. Pruning never removes the snapshot that `latest` points to. Initially, `latest` points to `/dev/null` (meaning "no snapshot yet"); `--link-dest` is not used for the first sync, and `latest` is updated to point to the real snapshot after success.

Only one of `btrfs-snapshots` and `hard-link-snapshots` can be enabled per sync (they are mutually exclusive).

```yaml
destination:
  volume: usb-drive
  hard-link-snapshots:
    enabled: true
    max-snapshots: 10   # optional, omit for unlimited
```

### Rsync Local Volume

A reusable configuration for a local source or destination that can be shared between multiple syncs.

To be considered active, a local volume must have a `.nbkp-vol` file in the root of the volume.

### SSH Endpoint

A reusable configuration for an SSH server that can be shared between multiple remote volumes.
Provides the host, port, user, key, structured connection options, and optional proxy-jump.

The `proxy-jump` field references another ssh-endpoint by slug, enabling connections through a bastion/jump host. For multi-hop chains, use `proxy-jumps` (a list of endpoint slugs); the two fields are mutually exclusive. Both map to SSH's `-J` flag (comma-separated) and Fabric's nested `gateway` parameter. Circular proxy-jump chains are detected and rejected at config load time.

The `location` field declares which network location this endpoint is accessible from (e.g. `home`, `office`, `travel`). Used with the `--location` CLI option for endpoint selection (see [Endpoint Filtering](#endpoint-filtering)).

The `extends` field references another ssh-endpoint by slug, inheriting all its fields. The child endpoint can override any inherited field. Circular extends chains are detected and rejected at config load time.

```yaml
ssh-endpoints:
  bastion:
    host: bastion.example.com
    user: admin

  nas:
    host: nas.internal
    user: backup
    proxy-jump: bastion
    location: home

  # Inherits user, key, port from nas; overrides host and location
  nas-public:
    extends: nas
    host: nas.public.example.com
    location: travel
```

#### SSH config integration

Fields not explicitly set in the nbkp config (or inherited via `extends`) are automatically filled from `~/.ssh/config`. This lets you define a minimal endpoint that relies on your existing SSH configuration:

```yaml
ssh-endpoints:
  nas:
    host: nas          # matches "Host nas" in ~/.ssh/config
    proxy-jump: bastion
```

If `~/.ssh/config` contains `Host nas` with `Port 5022`, `User backup`, and `IdentityFile ~/.ssh/nas_key`, the endpoint behaves as if those values were written directly in the nbkp config.

**Enriched fields:** `port`, `user`, `key` (first `IdentityFile` entry, with `~` expansion).

**Precedence order** (highest wins):
1. Explicit value in the nbkp config
2. Value inherited via `extends`
3. Value from `~/.ssh/config`
4. Pydantic default (e.g. `port: 22`)

**Not enriched:** `proxy-jump` and `connection-options` are not read from SSH config. Use nbkp's own `proxy-jump` field for proxy chains. For `proxy-jump` specifically, when no explicit value is set in nbkp config, Fabric and the SSH CLI both read `ProxyJump` from `~/.ssh/config` transparently, so proxy jumps defined in SSH config work without nbkp enrichment.

The `connection-options` field is an optional dictionary of typed SSH connection settings. These map to parameters across SSH (`ssh(1) -o`), Paramiko (`SSHClient.connect()`), and Fabric (`Connection()`). Available options:

| Field | Default | Description |
|---|---|---|
| `connect-timeout` | `10` | SSH connection timeout in seconds |
| `compress` | `false` | Enable SSH compression |
| `server-alive-interval` | `null` | Keepalive interval in seconds (prevents connection drops) |
| `allow-agent` | `true` | Use SSH agent for key lookup |
| `look-for-keys` | `true` | Search `~/.ssh/` for keys |
| `banner-timeout` | `null` | Wait time for SSH banner |
| `auth-timeout` | `null` | Wait time for auth response |
| `channel-timeout` | `null` | Wait time for channel open (Paramiko/Fabric only) |
| `strict-host-key-checking` | `true` | Verify remote host key |
| `known-hosts-file` | `null` | Custom known hosts file path |
| `forward-agent` | `false` | Enable SSH agent forwarding |
| `disabled-algorithms` | `null` | Disable specific SSH algorithms (Paramiko/Fabric only) |

Note: `channel-timeout` and `disabled-algorithms` are only used by the Fabric/Paramiko connection path (status checks, btrfs operations). They have no SSH CLI equivalent and do not affect rsync's `-e` option.

#### Disabling host key verification

Setting `known-hosts-file: /dev/null` translates to the SSH option `-o UserKnownHostsFile=/dev/null`. SSH normally records and verifies host keys in `~/.ssh/known_hosts`; pointing it to `/dev/null` means every connection starts with an empty known-hosts database.

Combined with `strict-host-key-checking: false`, SSH will never reject a host based on its key and never persist any host key it sees. This is commonly used for ephemeral or internal hosts (e.g. a NAS behind a bastion) whose keys may change after reprovisioning, where TOFU (trust-on-first-use) verification isn't practical.

Without `known-hosts-file: /dev/null`, setting only `strict-host-key-checking: false` would still write new host keys to `~/.ssh/known_hosts`, which could later cause "host key changed" warnings if the key rotates and strict checking is re-enabled.

```yaml
ssh-endpoints:
  nas:
    host: nas.internal
    proxy-jump: bastion
    connection-options:
      strict-host-key-checking: false
      known-hosts-file: /dev/null
```

### Rsync Remote Volume

A reusable configuration for a remote source or destination that can be shared between multiple syncs.
References an SSH endpoint by name and provides the path to the remote volume.

A remote volume must declare a primary endpoint via `ssh-endpoint`. It can optionally declare additional endpoints via `ssh-endpoints` (a list of endpoint slugs). When multiple endpoints are declared, the tool selects the best one based on endpoint filtering options (see [Endpoint Filtering](#endpoint-filtering)).

```yaml
volumes:
  nas-backup:
    type: remote
    ssh-endpoint: nas           # primary (required)
    ssh-endpoints:              # optional, additional candidates
      - nas
      - nas-public
    path: /volume1/backups
```

To be considered active, a remote volume must have a `.nbkp-vol` file in the root of the volume, and the selected endpoint must be reachable.

### Rsync Options

By default, every sync uses the following rsync flags: `-a --delete --delete-excluded --partial-dir=.rsync-partial --safe-links --checksum`. The `rsync-options` section lets you customise flags per sync:

```yaml
syncs:
  my-sync:
    rsync-options:
      compress: true              # default false — adds --compress
      checksum: false             # default true — adds --checksum
      default-options-override:   # replaces the default flags entirely
        - "-a"
        - "--delete"
      extra-options:              # appends additional flags
        - "--progress"
```

**`compress`** — enables rsync `--compress` for transfer compression. Useful for remote syncs over slow links. Default: `false`.

**`checksum`** — enables rsync `--checksum` to compare files by checksum instead of mod-time and size. More reliable but slower. Default: `true`.

**`default-options-override`** — replaces the default flags entirely. When omitted, the defaults are used unchanged.

**`extra-options`** — appends additional flags after the defaults (or after `default-options-override` when set).

When `rsync-options` is omitted entirely, the defaults are used with `checksum: true` and `compress: false`.

### Filters

A sync can optionally define rsync filters to control which files are included or excluded during the backup. There are three complementary mechanisms:

**Structured rules** — `include` / `exclude` dictionaries that are normalized into rsync filter syntax:

```yaml
filters:
  - include: "*.jpg"    # becomes "+ *.jpg"
  - exclude: "*.tmp"    # becomes "- *.tmp"
```

**Raw rsync filter strings** — passed directly to rsync's `--filter` option, supporting the full rsync filter syntax:

```yaml
filters:
  - "H .git"            # hide .git
  - "- __pycache__/"    # exclude __pycache__
```

Structured and raw filters can be mixed freely in the same list. They are applied in order as `--filter=RULE` arguments.

**External filter file** — a path to a file containing rsync filter rules in native rsync syntax, applied via `--filter=merge FILE`:

```yaml
filter-file: ~/.config/nbkp/filters/photos.rules
```

When both inline `filters` and `filter-file` are present, inline filters are applied first, followed by the filter file.

### Endpoint Filtering

When a remote volume declares multiple endpoints, the tool selects the best one at runtime. The following CLI options control endpoint selection (available on `check`, `run`, `sh`, `troubleshoot`, and `prune`):

| Option | Description |
|---|---|
| `--location SLUG` / `-l SLUG` | Prefer endpoints whose `location` field matches the given slug |
| `--private` | Prefer endpoints whose host resolves to private (LAN) IP addresses |
| `--public` | Prefer endpoints whose host resolves to public (WAN) IP addresses |

Selection logic:
1. Gather candidate endpoints from the volume's `ssh-endpoints` list (or the primary `ssh-endpoint` if no list is declared)
2. Exclude endpoints whose host cannot be DNS-resolved (unreachable)
3. If `--location` is set, prefer endpoints with matching `location` field
4. If `--private` or `--public` is set, prefer endpoints with matching network type
5. If no candidates remain after filtering, fall back to the primary endpoint

### Example Config

```yaml
ssh-endpoints:
  # Bastion/jump host for reaching internal servers
  bastion:
    host: bastion.example.com
    user: admin
    connection-options:
      server-alive-interval: 60  # keepalive every 60s

  # SSH connection details for the NAS (via bastion)
  nas:
    host: nas.internal
    port: 5022                  # optional, defaults to 22
    user: backup                # optional
    key: ~/.ssh/nas_ed25519     # optional
    proxy-jump: bastion         # connect through bastion
    location: home              # accessible from home network
    connection-options:         # optional, all fields have defaults
      connect-timeout: 30
      strict-host-key-checking: false
      known-hosts-file: /dev/null
      compress: true
      disabled-algorithms:      # Paramiko/Fabric only
        ciphers:
          - aes128-cbc

  # Public endpoint for the same NAS (inherits from nas)
  nas-public:
    extends: nas                # inherits user, key, port, connection-options
    host: nas.public.example.com
    location: travel            # accessible when traveling

volumes:
  # Local volume on a removable drive
  laptop:
    type: local
    path: /mnt/data

  # Local volume on a btrfs filesystem
  usb-drive:
    type: local
    path: /mnt/usb-backup

  # Remote volume with multiple endpoints for location-awareness
  nas-backups:
    type: remote
    ssh-endpoint: nas           # primary endpoint (required)
    ssh-endpoints:              # candidate endpoints for auto-selection
      - nas
      - nas-public
    path: /volume1/backups

  nas-photos:
    type: remote
    ssh-endpoint: nas
    ssh-endpoints:
      - nas
      - nas-public
    path: /volume2/photos

syncs:
  # Local-to-remote sync with filters
  photos-to-nas:
    source:
      volume: laptop
      subdir: photos            # optional subdirectory on the volume
    destination:
      volume: nas-photos
      subdir: photos-backup
    enabled: true               # optional, defaults to true
    filters:                    # optional rsync filters
      - include: "*.jpg"        # structured include rule
      - include: "*.png"
      - exclude: "*.tmp"        # structured exclude rule
      - "H .git"                # raw rsync filter string
    filter-file: ~/.config/nbkp/filters/photos.rules  # optional

  # Local-to-local sync with btrfs snapshots
  documents-to-usb:
    source:
      volume: laptop
      subdir: documents
    destination:
      volume: usb-drive
      btrfs-snapshots:
        enabled: true
        max-snapshots: 10       # optional, omit for unlimited

  # Local-to-local sync with hard-link snapshots
  music-to-usb:
    source:
      volume: laptop
      subdir: music
    destination:
      volume: usb-drive
      hard-link-snapshots:
        enabled: true
        max-snapshots: 5

  # Sync with custom rsync options
  music-to-nas:
    source:
      volume: laptop
      subdir: music
    destination:
      volume: nas-backups
      subdir: music-backup
    rsync-options:
      compress: true            # enable --compress for remote sync
```

**Location-aware usage:**

```bash
# At home — prefer private LAN endpoints
nbkp run --config backup.yaml --location home
nbkp run --config backup.yaml --private

# Traveling — prefer public endpoints
nbkp run --config backup.yaml --location travel
nbkp run --config backup.yaml --public
```

## Shell Script Generation (`sh` command)

The `nbkp sh` command compiles a config into a standalone bash script that performs the same backup operations as `nbkp run`, without requiring Python or the config file at runtime. All paths, SSH options, and rsync arguments are baked into the generated script.

```bash
# Generate and inspect the script
nbkp sh --config backup.yaml

# Generate, save to file (made executable), and validate syntax
nbkp sh --config backup.yaml -o backup.sh
bash -n backup.sh  # syntax check

# Run the generated script with flags
./backup.sh --dry-run
./backup.sh -v        # verbose
./backup.sh -v -v     # more verbose
```

The generated script supports `--dry-run` (`-n`) and `--verbose` (`-v`, `-vv`, `-vvv`) as runtime arguments — these are not baked in at generation time.

### Relative paths

The `--relative-src` and `--relative-dst` flags make local source and/or destination paths relative to the script location. These flags require `--output-file` so the script knows where it lives. Remote volume paths are always absolute (they live on remote hosts).

```bash
# Store the script next to the backups — destination paths become relative
nbkp sh --config backup.yaml -o /mnt/backups/backup.sh --relative-dst

# Both source and destination relative
nbkp sh --config backup.yaml -o /mnt/data/backup.sh --relative-src --relative-dst
```

The generated script resolves its own directory at runtime via `NBKP_SCRIPT_DIR` and uses it to compute the actual paths. This makes the script portable — it works regardless of where the drive is mounted.

**What is preserved from `nbkp run`:**
- All rsync command variants (local-to-local, local-to-remote, remote-to-local, remote-to-remote same server)
- SSH options (port, key, `-o` options, proxy jump `-J`)
- Rsync filters and filter-file support
- Btrfs snapshot creation and pruning
- Hard-link snapshots: incremental backups via `--link-dest`, symlink management, and pruning
- Pre-flight checks (volume sentinels, endpoint sentinels)
- Nonzero exit on any sync failure

**What is dropped:**
- Rich console output (spinners, progress bars) — replaced with simple log messages
- JSON output mode
- Python runtime / config parsing — all values are hardcoded
- Paramiko-only SSH options (`channel_timeout`, `disabled_algorithms`) — no `ssh` CLI equivalent

Disabled syncs appear in the generated script as commented-out blocks, allowing users to re-enable them by uncommenting.
