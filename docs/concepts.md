# Concepts

## Configuration Model

nbkp backup configuration is expressed in a single YAML file. The config defines four top-level sections: **SSH endpoints**, **volumes**, **sync endpoints**, and **syncs**. 

The config file is searched in order: explicit `--config` path → `$XDG_CONFIG_HOME/nbkp/config.yaml` → `/etc/nbkp/config.yaml`. 

See [Usage](./usage.md) for the full configuration reference, examples, and command documentation.

### Volumes

A volume is a named, reusable reference to a filesystem location. Volumes come in two types:

- **Local volume** — an absolute path on the local machine (e.g. `/mnt/data`, `/mnt/usb-backup`). Can be on a removable drive.
- **Remote volume** — an absolute path on a remote host, accessed over SSH. References one or more SSH endpoints (see below).

Volumes are defined once and shared across multiple sync endpoints.

**Path normalization** — All volume paths and sync endpoint subdirectories are normalized at config load time: trailing slashes are stripped so that `path: /mnt/data/` and `path: /mnt/data` are equivalent. For local volumes, `~` is expanded to the current user's home directory (e.g. `path: ~/data` becomes `/home/user/data`). Note that bare `~` must be quoted in YAML (`path: "~"`) because YAML interprets unquoted `~` as `null`. Remote volume paths are not subject to `~` expansion because the tilde refers to the remote user's home directory, and expansion is handled by SSH/rsync on the remote side.

### SSH Endpoints

An SSH endpoint defines connection details for a remote host: hostname, port, user, key, proxy-jump configuration, and structured connection options. Endpoints are defined once and referenced by remote volumes.

**Inheritance** — An endpoint can `extend` another endpoint, inheriting all its fields and selectively overriding specific ones. Circular extends chains are detected and rejected at config load time.

**Proxy jumps** — The `proxy-jump` field references another endpoint by slug, enabling connections through a bastion/jump host. For multi-hop chains, `proxy-jumps` accepts a list of endpoint slugs. Both map to SSH's `-J` flag. Circular proxy-jump chains are detected and rejected at config load time.

**Location tags** — Each endpoint can declare a `location` (e.g. `home`, `travel`) indicating which network context it is accessible from. This is used for endpoint filtering at runtime (see [Endpoint Filtering](#endpoint-filtering)).

**SSH config integration** — Fields not explicitly set in the nbkp config (or inherited via `extends`) are automatically filled from `~/.ssh/config`. Enriched fields: `port`, `user`, `key` (first `IdentityFile` entry, with `~` expansion). Precedence (highest wins): explicit value > inherited via `extends` > `~/.ssh/config` > Pydantic default. `proxy-jump` and `connection-options` are not enriched from SSH config.

**Connection options** — An optional set of typed SSH settings (connect timeout, compression, keepalive, host key verification, agent forwarding, algorithm selection, etc.) that map to parameters across SSH CLI, Paramiko, and Fabric. Some options (`channel-timeout`, `disabled-algorithms`) only apply to the Fabric/Paramiko path and have no SSH CLI equivalent.

**Host key verification** — Setting `strict-host-key-checking: false` with `known-hosts-file: /dev/null` fully disables host key verification and persistence. This is useful for ephemeral or internal hosts whose keys may change after reprovisioning.

### Sync Endpoints

A sync endpoint is a named, reusable reference to a specific location within a volume. It combines a volume slug with an optional subdirectory and optional snapshot configuration (btrfs or hard-link). Sync endpoints are defined once at the top level and referenced by slug from syncs.

Each sync endpoint must target a unique (volume, subdir) pair — two endpoints cannot point to the same filesystem location. This prevents conflicting snapshot configurations for the same path.

### Syncs

A sync describes a one-way data transfer from a source sync endpoint to a destination sync endpoint. Each sync references its source and destination by endpoint slug.

**Direction combinations** — Both source and destination can be local or remote, supporting local-to-local, local-to-remote, remote-to-local, and remote-to-remote (same server) syncs. Cross-server remote-to-remote syncs are not supported; use two separate syncs through the local machine instead.

**Enabling and disabling** — Each sync has an `enabled` flag (default: `true`). Syncs can also be selectively run via CLI options.

**Rsync options** — Each sync uses a set of default rsync flags (`-a --delete --delete-excluded --partial-dir=.rsync-partial --safe-links --checksum`). These can be customized per sync: `compress` and `checksum` toggle specific flags, `default-options-override` replaces the defaults entirely, and `extra-options` appends additional flags.

**Filters** — Each sync can define rsync filter rules to control which files are included or excluded. Three mechanisms are available: structured `include`/`exclude` rules, raw rsync filter strings, and external filter files. They can be combined and are applied in order.

### Snapshots

Each sync endpoint can optionally enable point-in-time snapshots. Two mutually exclusive backends are available: `btrfs-snapshots` and `hard-link-snapshots`. Both are configured on the sync endpoint with two fields:

- **`enabled`** — Activates snapshot management for this endpoint (default: `false`).
- **`max-snapshots`** — Maximum number of snapshots to retain. When set, old snapshots are pruned automatically after each `run`. Omit for unlimited retention.

**Btrfs snapshots** require a btrfs filesystem with the `user_subvol_rm_allowed` mount option (for pruning). **Hard-link snapshots** work on any filesystem supporting hard links (ext4, xfs, btrfs, etc.) but not FAT/exFAT.

**Source snapshots** — When snapshots are enabled on a sync endpoint used as a source, rsync reads from the `latest` snapshot directory rather than the volume root. This is used in chained syncs where one sync's destination endpoint is also another sync's source endpoint.

See [Snapshot Lifecycle](#snapshot-lifecycle) for how snapshots are created, managed, and pruned at runtime.

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

Both snapshot backends follow the same directory layout and lifecycle. Snapshot directories are named with ISO 8601 UTC timestamps (e.g. `2026-03-06T14:30:00.000Z`):

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

### Pre-flight Checks

Before running any sync, nbkp validates that all required infrastructure is in place. The `check` command runs these validations independently; the `run` command runs them before executing syncs.

Checks include:

- **Sentinel files** — `.nbkp-vol`, `.nbkp-src`, `.nbkp-dst` must exist at the expected paths
- **SSH connectivity** — Remote endpoints must be reachable; DNS resolution must succeed
- **Rsync availability** — rsync must be installed and version 3.0+ (macOS `openrsync` is rejected)
- **Btrfs readiness** — Correct filesystem type, subvolume existence, mount options, required directories
- **Hard-link readiness** — Filesystem hard-link support, required directory structure
- **`latest` symlink validity** — Must exist and point to `/dev/null` or an existing snapshot (see [The `latest` Symlink](#the-latest-symlink))
- **Strict mode** — Optionally exit non-zero on any inactive sync

The `troubleshoot` command runs the same checks and displays step-by-step remediation instructions for each failure.

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
