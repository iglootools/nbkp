# Internals

How nbkp works at runtime: execution flow, mount lifecycle, pre-flight validation, snapshot management, and the design decisions behind them. For users and contributors who want to understand what happens when nbkp runs, or audit the external commands it invokes.

For the domain model (volumes, endpoints, syncs, snapshots) and configuration reference, see [Concepts](./concepts.md). For the module dependency graph, see [Architecture](./architecture.md).

## Runtime Behavior

### Sentinel Files

nbkp uses lightweight sentinel files to guard against syncing to the wrong location. This is especially important for removable drives that may not always be mounted at the expected path.

Three types of sentinel files are used:

| Sentinel | Location | Purpose |
|---|---|---|
| `.nbkp-vol` | Volume root | Confirms the volume is present and mounted |
| `.nbkp-src` | Source endpoint path | Confirms the source directory is ready |
| `.nbkp-dst` | Destination endpoint path | Confirms the destination directory is ready |

A sync is only considered **active** when all of its sentinel files are present: `.nbkp-vol` on both source and destination volumes, `.nbkp-src` on the source path, and `.nbkp-dst` on the destination path. For remote volumes, the SSH endpoint must also be reachable.

If any sentinel is missing, the sync is marked **inactive** and skipped. This prevents data loss from syncing to an unmounted drive or an incorrect path.

### Snapshot Lifecycle

Both snapshot backends follow the same directory layout and lifecycle. Snapshot directories are named with ISO 8601 UTC timestamps (e.g. `2026-03-06T14:30:00.000Z`). On macOS local volumes, colons are replaced with hyphens (`2026-03-06T14-30-00.000Z`) because APFS/HFS+ forbids colons in filenames — the OS silently converts them to slashes, corrupting the directory name.

```
{destination}/
  latest           → symlink to the most recent complete snapshot
  snapshots/
    2026-03-06T14:30:00.000Z/
    2026-03-05T10:00:00.000Z/
    ...
  staging/         (btrfs only — writable subvolume for rsync)
```

**Btrfs flow** — Rsync writes to a `staging/` subvolume. After a successful sync, a read-only btrfs snapshot is created at `snapshots/{timestamp}`, and the `latest` symlink is updated.

**Hard-link flow** — Rsync writes directly into a new `snapshots/{timestamp}/` directory, using `--link-dest` to hard-link unchanged files from the previous snapshot (saving disk space). On success, the `latest` symlink is updated. No special filesystem commands are needed for creation or pruning.

**Pruning** — When `max-snapshots` is set, old snapshots beyond the limit are removed after each `run`. The snapshot that `latest` points to is never pruned. Pruning can also be triggered manually with the `prune` command.

**Orphan cleanup** — Hard-link syncs detect and clean up orphaned snapshot directories before each run. An orphan is a `snapshots/{timestamp}/` directory left behind by a previously failed sync (one that `latest` does not point to and that is not the most recent by timestamp). Btrfs snapshots do not need orphan cleanup because snapshots are created atomically from a complete sync to `staging/` — a failed sync leaves `staging/` in a partial state but never creates a snapshot directory.

### The `latest` Symlink

Snapshot-enabled syncs (btrfs and hard-link) maintain a `latest` symlink at the destination endpoint root. It always uses a relative target:

- **`/dev/null`** — The canonical "no snapshot yet" marker. This is the initial state before the first successful sync. For hard-link syncs, `--link-dest` is not used when `latest` points to `/dev/null`.
- **`snapshots/{timestamp}`** — Points to the most recent complete snapshot.

The `latest` symlink is only updated after a successful sync. If a sync fails midway, `latest` still points to the previous complete snapshot (or `/dev/null` if no sync has ever succeeded). This guarantees that `latest` always references a consistent state.

When `latest` is used as a **source** (in chained syncs), `latest → /dev/null` is accepted only when an enabled upstream sync writes to that endpoint. Otherwise, it is flagged as an error during pre-flight checks.

### Volume Mount Management

Volumes can optionally declare a `mount` section to automate the mount/umount lifecycle. This is useful for removable drives (encrypted or not) that need to be mounted before syncing and umounted afterward.

**Mount config** — The `mount` section specifies a `device-uuid` for drive detection (via `/dev/disk/by-uuid/`) and an optional `encryption` block for LUKS-encrypted volumes. The `device-uuid` is the LUKS container UUID for encrypted volumes, or the filesystem UUID for unencrypted volumes.

**Encrypted volumes** — For LUKS-encrypted volumes, the `encryption` block specifies a `mapper-name` (device mapper name, e.g. `seagate8tb`) and a `passphrase-id` (credential lookup key). The attach command is `sudo systemd-cryptsetup attach <mapper> /dev/disk/by-uuid/<uuid> /dev/stdin luks`, with the passphrase piped via stdin.

**Unencrypted volumes** — For unencrypted volumes, only `device-uuid` is needed. The mount command is `systemctl start <mount-unit>`, where the mount unit is derived from the volume path via `systemd-escape --path`.

**Credential providers** — LUKS passphrases are retrieved using the configured `credential-provider`:
- **keyring** (default): `keyring.get_password("nbkp", passphrase_id)` — uses macOS Keychain or Linux SecretService. Store via `keyring set nbkp <passphrase-id>`.
- **prompt**: interactive password prompt via Typer's hidden input.
- **env**: reads `NBKP_PASSPHRASE_<ID>` environment variable (uppercased, hyphens replaced with underscores).
- **command**: runs a configurable command template (e.g. `["pass", "show", "nbkp/{id}"]`) with `{id}` replaced by the passphrase-id.

Passphrases are cached in memory during a single run, so the user is only prompted once per unique passphrase-id even if multiple volumes share it.

**Lifecycle** — The `run`, `check`, and `troubleshoot` commands mount volumes before running and umount in a `finally` block (even on failure). Mount is idempotent: already-attached and already-mounted volumes are skipped. Umount always attempts to umount and close LUKS for all volumes with mount config, regardless of who mounted them — this avoids fragile action tracking across failed/restarted runs.

**Authorization** — Mount management uses polkit for `systemctl start/stop` (D-Bus authorization) and sudoers NOPASSWD for `sudo systemd-cryptsetup attach`. The `config setup-auth` command generates both rule files for review and manual installation.

**Shell script limitation** — Mount management is excluded from `sh` script generation because credential retrieval depends on Python-specific backends (keyring, prompt). Volumes with mount config must be manually mounted/umounted before/after running the generated script.

#### Why `systemd-cryptsetup` instead of raw `cryptsetup`

LUKS attach uses `sudo systemd-cryptsetup attach` rather than `sudo cryptsetup open` for two reasons:

1. **systemd integration** — `systemd-cryptsetup attach` registers the device mapper with systemd's unit tracking. This means `systemctl stop systemd-cryptsetup@<mapper>.service` cleanly tears down the device, and tools like `systemctl status` and `journalctl -u` work as expected. Raw `cryptsetup open` creates the mapper outside systemd's awareness, making close via `systemctl stop` unreliable.

2. **Consistent close path** — Closing via `systemctl stop systemd-cryptsetup@<mapper>.service` works regardless of whether the device was opened by nbkp or by the system (e.g. via crypttab at boot). If we used `cryptsetup open`, we'd need `cryptsetup close` to close, which would fight with systemd if it also manages the device.

#### Why polkit + sudoers (hybrid authorization)

Mount management uses two authorization mechanisms because `systemctl start/stop` and `systemd-cryptsetup attach` follow different privilege paths:

- **polkit** for `systemctl start/stop` — These commands go through D-Bus to systemd, which consults polkit for authorization. A polkit rule at `/etc/polkit-1/rules.d/50-nbkp.rules` grants the backup user permission to start/stop specific mount and cryptsetup units without a password.

- **sudoers** for `systemd-cryptsetup attach` — This is a direct binary invocation that needs root privileges (it accesses `/dev/disk/by-uuid/` and sets up device mapper). Polkit cannot authorize direct command execution; only sudo can. A sudoers rule at `/etc/sudoers.d/nbkp` grants `NOPASSWD` access to the specific `systemd-cryptsetup attach` commands.

Both are generated by `nbkp config setup-auth` from the config file, so the rules are always in sync with the configured volumes.

#### Why keyring as the default credential provider

The `keyring` library provides a cross-platform abstraction over OS-native secret stores (macOS Keychain, Linux SecretService/GNOME Keyring, KDE Wallet). This was chosen as the default because:

- **No plaintext secrets** — Passphrases are stored in the OS credential store, encrypted at rest.
- **Interactive setup** — `keyring set nbkp <passphrase-id>` provides a simple one-time setup that non-technical users can follow.
- **Session integration** — On desktop Linux, the keyring is typically unlocked at login and stays available for the session.

The `keyring` package is an optional dependency (`pip install nbkp[keyring]`) to avoid pulling in D-Bus/SecretStorage libraries on headless servers where `env` or `command` providers are more appropriate.

#### Why no action tracking for umount

`umount_volumes` always attempts to umount and close LUKS for every volume with mount config, rather than tracking which volumes were actually mounted by the current run. This avoids fragile state tracking across scenarios like:

- A `run` that fails partway through and is restarted
- A `volumes mount` followed by a `run --no-mount` followed by `volumes umount`
- A volume that was already mounted before nbkp started

The cost is negligible — `systemctl stop` on an already-stopped unit is a no-op — and the benefit is that cleanup is always complete regardless of how the session progressed.

#### Why no `dm-crypt` kernel module check

Pre-flight checks verify that userspace tools (`cryptsetup`, `systemd-cryptsetup`) are available, but deliberately do not probe for the `dm-crypt` kernel module:

- On any system where `cryptsetup` is installed, `dm-crypt` is almost always available (built-in or auto-loaded on first use).
- The kernel may auto-load the module when `cryptsetup` runs, so a pre-check could give false negatives.
- When `dm-crypt` is genuinely missing (e.g. Docker without `--privileged`), `cryptsetup`/`systemd-cryptsetup attach` fails with a clear error — the failure is not silent.

#### Why mount unit names are derived at runtime

Mount unit names (e.g. `/mnt/seagate8tb` → `mnt-seagate8tb.mount`) are derived by running `systemd-escape --path <volume-path>` on the target host rather than being hardcoded or computed in Python. This is because systemd's escaping rules are non-trivial (hyphens in path components become `\x2d`, among other edge cases), and `systemd-escape` is the canonical implementation. The result is cached in `VolumeCapabilities.mount_unit` after the first preflight probe.

### Pre-flight Checks

Before running any sync, nbkp validates that all required infrastructure is in place. The `check` command runs these validations independently; the `run` command runs them before executing syncs.

Checks include:

- **Sentinel files** — `.nbkp-vol`, `.nbkp-src`, `.nbkp-dst` must exist at the expected paths
- **SSH connectivity** — Remote endpoints must be reachable; DNS resolution must succeed
- **Rsync availability** — rsync must be installed and version 3.0+ (macOS `openrsync` is rejected)
- **Btrfs readiness** — Correct filesystem type, subvolume existence, mount options, required directories
- **Hard-link readiness** — Filesystem hard-link support, required directory structure
- **`latest` symlink validity** — Must exist and point to `/dev/null` or an existing snapshot (see [The `latest` Symlink](#the-latest-symlink))
- **Mount infrastructure** — For volumes with mount config: systemctl/systemd-escape availability, mount unit configured in systemd (fstab/native .mount), mount unit What/Where match nbkp config, polkit rules. For encrypted volumes: cryptsetup/systemd-cryptsetup availability, cryptsetup service configured, sudoers rules.
- **Strictness mode** — See below

The `troubleshoot` command runs the same checks and displays step-by-step remediation instructions for each failure.

#### Strictness

Pre-flight checks distinguish between two categories of errors:

- **Inactive errors** — Missing sentinel files (`.nbkp-vol`, `.nbkp-src`, `.nbkp-dst`), unavailable volumes, and pending snapshots in dry-run mode. These represent expected situations where a sync is not ready to run (e.g. a removable drive is not plugged in, a remote host is unreachable, or a volume is not mounted at the expected path).
- **Infrastructure errors** — Everything else: missing rsync, wrong filesystem type, broken symlinks, misconfigured systemd units, etc. These indicate real problems that need fixing.

The `--strictness` flag controls how preflight errors affect the exit code:

| | `ignore-inactive` (default) | `ignore-none` | `ignore-all` |
|---|---|---|---|
| **Inactive errors** | Sync is silently skipped; other syncs still run | Fatal — aborts the entire run before any sync executes | Ignored |
| **Infrastructure errors** | Fatal | Fatal | Ignored |
| **Exit code** | 0 if only inactive syncs were skipped | 1 if any sync is inactive | 0 unless sync execution itself fails |

**`ignore-inactive`** (default) is designed for configs that include syncs which are not always runnable — for example, a backup to a USB drive that is only connected on weekends, or a remote server that is only reachable from a specific network. The run succeeds as long as all *active* syncs complete, and inactive ones are skipped without noise.

**`ignore-none`** is useful for scheduled/automated runs where every sync is expected to be active. A missing sentinel or unreachable volume likely indicates a problem (drive not mounted, server down) rather than an expected absence, and the operator wants to be alerted.

**`ignore-all`** ignores all preflight errors — only sync execution failures cause a non-zero exit. This can be useful when you want to attempt syncs regardless of preflight check results.

#### Preflight Conditional Probing

The preflight check system uses two layers: an **observation layer** (`volume_checks.py`, `endpoint_checks.py`) that probes raw state, and an **error interpretation layer** (`status.py`) that decides what constitutes a problem based on config. Not all capabilities are checked for every volume or endpoint — probing is selective. This is an intentional design choice driven by three categories of conditional logic.

##### Physical cascade dependencies

Probe B requires the result of probe A as input. These conditions live in the observation layer because they represent physical impossibilities, not policy decisions:

- `rsync_version_ok` requires `has_rsync` — can't run `rsync --version` if rsync isn't installed
- `is_btrfs_filesystem` requires `has_stat` — needs `stat -f -c %T`
- `btrfs_user_subvol_rm` requires `has_findmnt AND is_btrfs`
- `mount_unit` derivation requires `has_systemd_escape` — needs the tool to compute the unit name
- `has_mount_unit_config` requires `mount_unit` — can't query a systemd unit without its name
- `staging_writable` requires `staging_exists`

##### Config-as-input probing

The probe itself needs config values as parameters — without them, the probe cannot be formulated. These also live in the observation layer:

- Encryption checks need `mapper_name` from `mount.encryption` — no mapper name means nothing to query
- `systemd-cryptsetup@{mapper}.service` lookup needs the mapper name from config
- `systemctl show {mount-unit}` needs the derived mount unit name

##### Config-as-filter probing

The probe is independent of config values, but only relevant when a feature is enabled. These are skipped in the observation layer to avoid unnecessary SSH round-trips (each check is a remote call):

- `snapshot_dirs` and `latest` symlink checks only run when `endpoint.snapshot_mode != "none"`
- `BtrfsStagingSubvolumeDiagnostics` only probed when `btrfs_snapshots.enabled` (plus physical prerequisites)

The error interpretation layer already filters based on these same config flags, so these probes could theoretically always run. However, always-probing would add 2–5 extra SSH calls per non-snapshot endpoint, which adds up across configs with many endpoints.

##### Why not always-probe

Consolidating all conditional logic in the error interpretation layer was considered and rejected:

- **Categories 1 and 2 cannot move** — they encode physical prerequisites, not policy. Moving them downstream would just replace cascade conditionals with null-checks in the error layer.
- **Category 3 saves real SSH round-trips** with minimal code complexity (2–3 lines of guards per check site).
- **The `| None` type convention** (meaning "not probed / not applicable") is consistently applied across all diagnostics models and well-understood by the error interpretation layer.

### Sync Dependencies and Execution Order

When one sync's destination endpoint is the same as another sync's source endpoint (same endpoint slug), a dependency exists between them. The sync whose destination feeds the other is called the **upstream** sync; the one that reads from it is the **downstream** sync.

Syncs are automatically sorted in topological order so that upstream syncs always complete before their downstream dependents begin.

### Failure Propagation

If a sync fails, all downstream syncs (directly or transitively) are automatically **cancelled** to prevent propagating partial or stale data through the chain. Cancelled syncs appear with a `CANCELLED` status and the name of the failed upstream sync. Independent syncs (those with no dependency relationship to the failed sync) continue to run normally.

For example, in a chain `A → B → C` where A's destination is B's source and B's destination is C's source: if A fails, both B and C are cancelled. A sync D that reads from an unrelated volume still executes.

Inactive (skipped) syncs also trigger cancellation of their downstream dependents.

### Endpoint Filtering

When a remote volume declares multiple SSH endpoints, nbkp selects the best reachable one at runtime. This enables nomadic usage where the same config works across different network contexts (e.g. home LAN vs public internet).

**Volume-level exclusion** — When `--exclude-location` is set and *all* candidate endpoints for a volume have a matching location tag, the volume is skipped entirely (no SSH connection is attempted). Syncs referencing the skipped volume are marked inactive with a `LOCATION_EXCLUDED` reason. This is the only hard filter — it prevents slow SSH timeouts when a location is known to be unreachable.

**Per-endpoint selection** — For volumes that are not excluded, the best endpoint is selected using a soft filter chain:

1. Gather candidate endpoints from the volume's endpoint list
2. Exclude endpoints whose host cannot be DNS-resolved
3. If `--exclude-location` is set, remove endpoints with a matching location tag
4. If `--location` is set, prefer endpoints with a matching location tag
5. If `--network` is set, prefer endpoints with matching network type (`private` for LAN, `public` for WAN)

Each filter step is a **soft filter**: if it would eliminate all remaining candidates, it is silently skipped and the previous candidate list is preserved. This means filters degrade gracefully — `--location office` has no effect if no endpoint is tagged `office`, rather than causing a failure.

Both `--location` and `--exclude-location` can be used together. Exclude is applied first, then include narrows further. This is useful when most endpoints lack location tags — instead of listing every location to include, you can exclude the ones you want to skip (e.g. `--exclude-location home`).

### Shell Script Generation

The `sh` command compiles a config into a self-contained bash script that reproduces the same backup operations as `run`, without requiring Python or the config file at runtime. The generated script preserves all sync functionality: rsync commands, SSH options, filters, snapshot creation and pruning, pre-flight checks, dependency ordering, and failure propagation. See [Usage](./usage.md#sh--generate-a-standalone-backup-shell-script) for details.

### Outputs

All commands support both human-readable output (Rich-formatted tables, spinners, progress bars) and machine-readable JSON output for scripting and automation. The `run` command additionally supports four progress display modes: `none`, `overall`, `per-file`, and `full`.

### Rsync Defaults

#### Why `--hard-links` is not enabled by default

The default rsync flags use `-a` (archive mode), which expands to `-rlptgoD`. This preserves symlinks (`-l`) but does **not** include `-H` (`--hard-links`). Hard-link preservation is deliberately left opt-in for four reasons:

1. **Memory overhead** — rsync must build an in-memory hash table of every `(device, inode)` pair on the source to detect hard-link relationships. For large file trees with millions of files, this can consume significant RAM on both the sending and receiving side.

2. **Slower transfer startup** — The hard-link detection pass adds time proportional to the number of files, even when no hard links exist in the source.

3. **Interaction with `--link-dest` in hard-link snapshots** — nbkp's hard-link snapshot mode uses `--link-dest` to deduplicate unchanged files across snapshots. Adding `-H` on top means rsync must also track *source-side* hard-link groups, which can interact unpredictably with `--link-dest` (e.g. files that are hard-linked on the source might get deduplicated differently than intended across snapshots).

4. **No benefit for most backup workloads** — Typical user data (documents, photos, media) rarely contains intentional hard links. The overhead would be paid on every run for no practical gain.

Users who need hard-link preservation for a specific sync (e.g. backing up a filesystem that heavily uses hard links like a mail spool or package cache) can enable it per-sync via `extra-options: ["--hard-links"]`.

## External Commands Reference

This section documents every external command nbkp invokes on local or remote hosts, organized by category. This gives users full visibility into what nbkp does on their system.

### Preflight checks

| Command | Purpose |
|---|---|
| `test -f <path>` | Sentinel file existence (`.nbkp-vol`, `.nbkp-src`, `.nbkp-dst`) |
| `test -d <path>` | Directory existence |
| `test -w <path>` | Directory writability |
| `test -L <path>` | Symlink existence (`latest`) |
| `test -e /dev/disk/by-uuid/<UUID>` | Drive detection (mount management) |
| `test -b /dev/mapper/<name>` | LUKS device unlocked check |
| `which <command>` | Command availability (rsync, btrfs, stat, findmnt, systemctl, systemd-escape, cryptsetup) |
| `rsync --version` | Rsync version check (>= 3.0, reject macOS openrsync) |
| `stat -f -c %T <path>` | Detect btrfs filesystem type |
| `stat -c %i <path>` | Check btrfs subvolume (inode == 256) |
| `findmnt -T <path> -n -o OPTIONS` | Check btrfs mount options (e.g. `user_subvol_rm_allowed`) |
| `readlink <path>` | Read `latest` symlink target |
| `systemd-escape --path <path>` | Derive mount unit name from volume path |
| `systemctl is-active <mount-unit> --quiet` | Check if volume is mounted |
| `systemctl cat <unit>` | Check if systemd unit is known (mount unit, cryptsetup service) |
| `systemctl show <unit> -p <props> --no-pager` | Read systemd unit properties (What, Where, ExecStart) |

### Rsync synchronization

| Command | Purpose |
|---|---|
| `rsync -a --delete --delete-excluded --partial-dir=.rsync-partial --safe-links --checksum ...` | Data sync with default flags |
| `--filter=H .nbkp-*` / `--filter=P .nbkp-*` | Hide/protect sentinel files during transfer |
| `--compress`, `--checksum` | Optional per-sync flags |
| `--link-dest=<prev-snapshot>` | Hard-link snapshots: reference previous snapshot for deduplication |
| `-e ssh ...` | SSH transport for remote syncs |

### Btrfs snapshot operations

| Command | Purpose |
|---|---|
| `btrfs subvolume snapshot -r <staging> <snapshots/timestamp>` | Create read-only snapshot |
| `btrfs property set <snapshot> ro false` | Make snapshot writable (for deletion) |
| `btrfs subvolume delete <snapshot>` | Delete snapshot |

### Hard-link snapshot operations

| Command | Purpose |
|---|---|
| `mkdir -p <snapshots/timestamp>` | Create snapshot directory |
| `rm -rf <snapshot>` | Delete snapshot directory (remote only; local uses Python `shutil.rmtree`) |
| `ls <snapshots/>` | List snapshots for pruning/ordering |

### Symlink management

| Command | Purpose |
|---|---|
| `readlink <latest>` | Read current latest snapshot |
| `ln -sfn <target> <latest>` | Update latest symlink (remote only; local uses Python `pathlib`) |

### Mount management (systemd strategy)

| Command | Purpose |
|---|---|
| `sudo <systemd-cryptsetup-path> attach <mapper> /dev/disk/by-uuid/<uuid> /dev/stdin luks` | Attach LUKS volume (passphrase piped via stdin) |
| `systemctl start <mount-unit>` | Mount volume |
| `systemctl stop <mount-unit>` | Umount volume |
| `systemctl stop systemd-cryptsetup@<mapper>.service` | Close LUKS volume |

### Mount management (direct strategy)

| Command | Purpose |
|---|---|
| `sudo cryptsetup open --type luks /dev/disk/by-uuid/<uuid> <mapper> -` | Attach LUKS volume (passphrase read from stdin via `-` key-file argument) |
| `sudo mount <volume-path>` | Mount volume (device and options from fstab) |
| `sudo umount <volume-path>` | Umount volume |
| `sudo cryptsetup close <mapper>` | Close LUKS volume |

### SSH transport

SSH arguments are constructed dynamically per endpoint. Common options:

| Option | Purpose |
|---|---|
| `-o ConnectTimeout=X` | Connection timeout |
| `-o BatchMode=yes` | Disable interactive prompts |
| `-o Compression=yes` | Enable SSH compression |
| `-o ServerAliveInterval=X` | Keepalive interval |
| `-o StrictHostKeyChecking=no` | Disable host key verification (when configured) |
| `-o UserKnownHostsFile=X` | Custom known hosts file |
| `-o LogLevel=ERROR` | Suppress SSH warnings |
| `-o ForwardAgent=yes` | Forward SSH agent |
| `-p PORT` | Custom SSH port |
| `-i KEY_FILE` | SSH identity file |
| `-o ProxyCommand=...` | Proxy jump chains |
