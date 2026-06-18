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

Volumes can optionally declare a `mount` section to automate the mount/umount lifecycle. This is useful for removable drives (encrypted or not) that need to be mounted before syncing and umounted afterward. nbkp drives the lifecycle entirely through [udisks2](https://www.freedesktop.org/wiki/Software/udisks/) (`udisksctl`), a single D-Bus daemon authorized by polkit — there is no `sudo`, no `systemd-cryptsetup`, and no `systemctl` in the mount path.

**Mount config** — The `mount` section specifies a `device-uuid` for drive detection (via `/dev/disk/by-uuid/`) and an optional `encryption` block for LUKS-encrypted volumes. The `device-uuid` is the LUKS container UUID for encrypted volumes, or the filesystem UUID for unencrypted volumes. There is no `strategy` field — udisks is the sole backend.

**Encrypted volumes** — For LUKS-encrypted volumes, the `encryption` block specifies only a `passphrase-id` (credential lookup key). nbkp does not assume a mapper name: it unlocks the container with `udisksctl unlock -b /dev/disk/by-uuid/<luks-uuid> --key-file /dev/stdin --no-user-interaction` (passphrase piped without a trailing newline) and then **discovers** the cleartext device with `lsblk -rno NAME,TYPE /dev/disk/by-uuid/<luks-uuid>` (the `crypt` child → `/dev/mapper/<name>`). udisks names the unlocked device `luks-<luks-uuid>` by default, but any crypttab custom name (e.g. `seagate8tb`) is supported transparently because the name is discovered rather than hardcoded. The discovered cleartext device is then mounted with `udisksctl mount -b <device>` and, after the run, unmounted with `udisksctl unmount -b <device>` and locked with `udisksctl lock -b /dev/disk/by-uuid/<luks-uuid>`.

**Unencrypted volumes** — For unencrypted volumes, only `device-uuid` (the filesystem UUID) is needed. The volume is mounted with `udisksctl mount -b /dev/disk/by-uuid/<fs-uuid>` and unmounted with `udisksctl unmount -b /dev/disk/by-uuid/<fs-uuid>`.

**Mount options (e.g. `user_subvol_rm_allowed`)** — nbkp invokes `udisksctl mount` **without `-o`**: it never injects mount options. udisks validates every `-o` option against an allowlist and rejects anything not on it (`OptionNotPermitted`), so injecting an option like btrfs's `user_subvol_rm_allowed` (needed for snapshot pruning, and not allowlisted by default) would fail the mount outright on an un-configured host. Mount options must therefore be supplied by operator configuration, through whichever of the two mount-point models applies:

- **fstab route (declared `path`)** — include the option directly in the volume's `/etc/fstab` line (e.g. `/dev/mapper/luks-<uuid>  /mnt/x  btrfs  noauto,nofail,x-udisks-auth,user_subvol_rm_allowed  0 0`). udisks honors fstab options verbatim, bypassing the allowlist. This is the simplest and most robust route.
- **discovered route (omitted `path`)** — extend udisks's own allowlist in `/etc/udisks2/mount_options.conf`. The option must appear in **both** `<fstype>_allow` (to *permit* it) **and** `<fstype>_defaults` (to *apply* it by default) — `_defaults` alone still yields `OptionNotPermitted` — and `udisksd` must be restarted to reload the file. udisks then applies it when mounting at `/run/media/<user>/<label>`. The section can be global (`[defaults]`) or per-device (`[/dev/disk/by-uuid/<uuid>]`, matching the *mounted* device — the unlocked cleartext mapper for encrypted volumes).

Regardless of route, nbkp **verifies** the result rather than trusting the configuration: a preflight check runs `findmnt -T <path> -n -o OPTIONS` against the live mount and flags `VOL_NOT_MOUNTED_USER_SUBVOL_RM` if a btrfs volume is missing `user_subvol_rm_allowed`. This route-agnostic verification is the safety net — the option is guaranteed present at run time however it was configured.

**Credential providers** — LUKS passphrases are retrieved using the configured `credential-provider`:
- **keyring** (default): `keyring.get_password("nbkp", passphrase_id)` — uses macOS Keychain or Linux SecretService. Store via `keyring set nbkp <passphrase-id>`.
- **prompt**: interactive password prompt via Typer's hidden input.
- **env**: reads `NBKP_PASSPHRASE_<ID>` environment variable (uppercased, hyphens replaced with underscores).
- **command**: runs a configurable command template (e.g. `["pass", "show", "nbkp/{id}"]`) with `{id}` replaced by the passphrase-id.

Passphrases are cached in memory during a single run, so the user is only prompted once per unique passphrase-id even if multiple volumes share it.

**Lifecycle** — The `run`, `preflight check`, and `preflight troubleshoot` commands mount volumes before running and umount in a `finally` block (even on failure). Mount is idempotent: already-unlocked and already-mounted volumes are skipped. Umount always attempts to unmount and lock for all volumes with mount config, regardless of who mounted them — this avoids fragile action tracking across failed/restarted runs.

**Authorization** — Mount management is authorized by **polkit only** (no sudoers). The `disks setup-auth` command generates a single polkit rule at `/etc/polkit-1/rules.d/50-nbkp.rules` for review and manual installation. See [Why polkit-only](#why-polkit-only).

**Shell script limitation** — Mount management is excluded from `sh` script generation because credential retrieval depends on Python-specific backends (keyring, prompt). Volumes with mount config must be manually mounted/umounted before/after running the generated script.

For a complete end-to-end setup guide, including the fstab × crypttab combinations and worked examples, see [Mount management with udisks2](./usage.md#mount-management-with-udisks2).

#### Why udisks2

Earlier versions of nbkp shipped two hand-rolled mount backends — a systemd strategy (`systemctl start/stop` + `sudo systemd-cryptsetup attach`) and a direct strategy (raw `sudo mount/umount` + `sudo cryptsetup open/close`) — selected by a `strategy: auto|systemd|direct` config field. udisks2 already solves the entire problem through a single D-Bus daemon, so nbkp now delegates to it exclusively:

- **One backend** — udisks handles unlocking, mounting, unmounting, and locking uniformly across removable and system devices, on systemd and non-systemd hosts alike. The strategy split and its config field are gone.
- **No `sudo` anywhere** — udisks operations are authorized purely by polkit (see below), eliminating the sudoers half of the old hybrid authorization model.
- **No mapper-name in config** — udisks names the unlocked device `luks-<luks-uuid>` deterministically, and nbkp discovers the actual name at runtime, so the user-chosen `mapper-name` field is no longer needed.
- **Smaller config and code** — one authorization artifact (a single polkit rule), no `systemd-escape` mount-unit derivation, no `systemd-cryptsetup` binary-path detection.

#### Why polkit-only

udisks consults polkit (over D-Bus) for every privileged operation, so there is exactly one authorization mechanism. A polkit rule at `/etc/polkit-1/rules.d/50-nbkp.rules` grants the backup user — without a password — the following udisks actions (all prefixed `org.freedesktop.udisks2.`). The set is deliberately scoped to the unlock → mount → umount → lock lifecycle across removable, system (fstab-listed), and multi-seat devices; it includes no configuration-modifying or destructive actions.

| Action | What it grants | Why nbkp needs it |
|---|---|---|
| `filesystem-mount` | Mount a filesystem on a *removable* device | mount step for removable drives |
| `filesystem-mount-system` | Mount a filesystem on a device udisks classifies as *system* (internal/fixed) | mount step for fixed/internal drives |
| `filesystem-mount-other-seat` | Mount a filesystem on a device attached to another login *seat* | nbkp runs headless / over SSH, not bound to the device's seat |
| `filesystem-fstab` | Act on a device configured in `/etc/fstab` / `/etc/crypttab` (the action udisks checks for an `x-udisks-auth` entry) | the fixed-path (Option A) mount model |
| `filesystem-unmount-others` | Unmount a filesystem mounted by a *different* user | umount drives left mounted by another session or a prior run |
| `encrypted-unlock` | Unlock a LUKS container on a *removable* device | unlock step for encrypted removable drives |
| `encrypted-unlock-system` | Unlock a LUKS container on a *system* device | unlock step for fixed/internal encrypted drives |
| `encrypted-lock-others` | Lock a LUKS container unlocked by a *different* user | lock drives left unlocked by another session or a prior run |

The `-system` variants cover devices udisks treats as internal/fixed rather than removable; the `-others` / `-other-seat` variants cover acting on a device that another user or seat set up — which is exactly nbkp's situation when it runs in an inactive SSH/timer session. Each action's upstream description and prompt text live in the udisks2 polkit policy file, [`org.freedesktop.UDisks2.policy`](https://github.com/storaged-project/udisks/blob/master/data/org.freedesktop.UDisks2.policy.in) (see also the [udisks2 reference manual](https://storaged.org/doc/udisks2-api/latest/)).

udisks defines many **other** actions that nbkp deliberately does *not* grant — among them `manage-configuration` (edit `/etc/fstab` and `/etc/crypttab`), `encrypted-change-passphrase`, `filesystem-take-ownership`, `power-off-drive` / `eject-media`, `modify-device`, and the partition/format/SMART actions. Limiting the grant to the lifecycle subset above means the backup user can mount, unmount, unlock, and lock the configured drives, but cannot reconfigure, repartition, reformat, re-key, or power them off.

This rule is **required** even though udisks normally allows an interactively-logged-in user to mount removable media without authentication: nbkp typically runs over SSH or from a cron/timer in an *inactive* login session, where udisks would otherwise demand administrator authentication. The polkit rule grants the backup user the actions unconditionally so the unattended path works.

The rule is generated by `nbkp disks setup-auth` from the config file, so it is always in sync with the configured volumes.

#### Mount-point resolution (fstab vs discovered)

`path` is **optional** for mount-managed volumes, which gives two mount-point models:

- **Declared `path` (fstab)** — When `volume.path` is set, the operator is expected to have an `/etc/fstab` entry mapping the device to that path (`noauto,nofail`). udisks honors the fstab entry and mounts the volume at the declared path. Preflight verifies the fstab entry maps the device to `path`; if it does not, the volume is flagged with `FSTAB_MOUNTPOINT_MISMATCH` (udisks would otherwise land it elsewhere).
- **Omitted `path` (discovered)** — When `volume.path` is omitted, udisks mounts the volume at its default `/run/media/<user>/<label>` location. nbkp **discovers** the effective mountpoint after mounting (via `findmnt --source <device> -n -o TARGET`) and uses it as the volume's path for the rest of the run — sentinels, rsync source/destination, snapshot directories, and directory checks all use the discovered path. This requires zero system configuration beyond the polkit rule.

**Safety model** — In both models, udisks mounts strictly by UUID, so the *correct physical device* is guaranteed regardless of where it lands in the mount tree. The `.nbkp-vol` sentinel then confirms the mounted content is the expected volume. Together, UUID matching plus the sentinel replace the old reliance on a fixed, pre-configured mount unit for the wrong-drive guarantee.

#### Why keyring as the default credential provider

The `keyring` library provides a cross-platform abstraction over OS-native secret stores (macOS Keychain, Linux SecretService/GNOME Keyring, KDE Wallet). This was chosen as the default because:

- **No plaintext secrets** — Passphrases are stored in the OS credential store, encrypted at rest.
- **Interactive setup** — `keyring set nbkp <passphrase-id>` provides a simple one-time setup that non-technical users can follow.
- **Session integration** — On desktop Linux, the keyring is typically unlocked at login and stays available for the session.

The `keyring` package is an optional dependency (`pip install nbkp[keyring]`) to avoid pulling in D-Bus/SecretStorage libraries on headless servers where `env` or `command` providers are more appropriate.

#### Why no action tracking for umount

`umount_volumes` always attempts to unmount and lock for every volume with mount config, rather than tracking which volumes were actually mounted by the current run. This avoids fragile state tracking across scenarios like:

- A `run` that fails partway through and is restarted
- A `disks mount` followed by a `run --no-mount` followed by `disks umount`
- A volume that was already mounted before nbkp started

The cost is negligible — `udisksctl unmount`/`lock` on an already-unmounted or already-locked device is effectively a no-op — and the benefit is that cleanup is always complete regardless of how the session progressed.

#### Why no `dm-crypt` kernel module check

Pre-flight checks verify that udisks (`udisksctl` + a running `udisksd`) is available, but deliberately do not probe for the `dm-crypt` kernel module:

- On any system where udisks/LUKS tooling is installed, `dm-crypt` is almost always available (built-in or auto-loaded on first use).
- The kernel may auto-load the module when udisks unlocks a container, so a pre-check could give false negatives.
- When `dm-crypt` is genuinely missing (e.g. Docker without `--privileged`), `udisksctl unlock` fails with a clear error — the failure is not silent.

### Pre-flight Checks

Before running any sync, nbkp validates that all required infrastructure is in place. The `preflight check` command runs these validations independently; the `run` command runs them before executing syncs.

Checks include:

- **Sentinel files** — `.nbkp-vol`, `.nbkp-src`, `.nbkp-dst` must exist at the expected paths
- **SSH connectivity** — Remote endpoints must be reachable; DNS resolution must succeed
- **Rsync availability** — rsync must be installed and version 3.0+ (macOS `openrsync` is rejected)
- **Btrfs readiness** — Correct filesystem type, subvolume existence, mount options, required directories
- **Hard-link readiness** — Filesystem hard-link support, required directory structure
- **`latest` symlink validity** — Must exist and point to `/dev/null` or an existing snapshot (see [The `latest` Symlink](#the-latest-symlink))
- **Mount infrastructure** — For volumes with mount config: `udisksctl` availability and a running `udisksd` daemon, the `50-nbkp.rules` polkit rule, and (for volumes with a declared `path`) an `/etc/fstab` entry that maps the device to that path. For btrfs volumes, a warning if the `udisks2-btrfs` module is missing.
- **Strictness mode** — See below

The `preflight troubleshoot` command runs the same checks and displays step-by-step remediation instructions for each failure.

#### Strictness

Pre-flight checks distinguish between two categories of errors:

- **Inactive errors** — Missing sentinel files (`.nbkp-vol`, `.nbkp-src`, `.nbkp-dst`), unavailable volumes, and pending snapshots in dry-run mode. These represent expected situations where a sync is not ready to run (e.g. a removable drive is not plugged in, a remote host is unreachable, or a volume is not mounted at the expected path).
- **Infrastructure errors** — Everything else: missing rsync, wrong filesystem type, broken symlinks, a missing polkit rule or fstab entry, an unreachable udisks daemon, etc. These indicate real problems that need fixing.

The `--strictness` flag controls how preflight errors affect the exit code:

| | `ignore-inactive` (default) | `ignore-none` | `ignore-all` |
|---|---|---|---|
| **Inactive errors** | Sync is silently skipped; other syncs still run | Fatal — aborts the entire run before any sync executes | Ignored |
| **Infrastructure errors** | Fatal | Fatal | Ignored |
| **Exit code** | 0 if only inactive syncs were skipped | 1 if any sync is inactive | 0 unless sync execution itself fails |

**`ignore-inactive`** (default) is designed for configs that include syncs which are not always runnable — for example, a backup to a USB drive that is only connected on weekends, or a remote server that is only reachable from a specific network. The run succeeds as long as all *active* syncs complete, and inactive ones are skipped without noise.

**`ignore-none`** is useful for scheduled/automated runs where every sync is expected to be active. A missing sentinel or unreachable volume likely indicates a problem (drive not mounted, server down) rather than an expected absence, and the operator wants to be alerted.

**`ignore-all`** ignores all preflight errors — only sync execution failures cause a non-zero exit. This can be useful when you want to attempt syncs regardless of preflight check results.

#### Probe real state, not proxies

Checks observe the actual condition that matters rather than an indirect stand-in:

- **Daemon reachability** via `udisksctl status` (can we actually talk to udisks?), not `systemctl is-active udisks2`.
- **Live mount options** (e.g. `user_subvol_rm_allowed`) via `findmnt -o OPTIONS` on the mounted filesystem, not the `/etc/fstab` line or the config.
- **Authorization** by attempting the operation (`udisksctl unlock`/`mount --no-user-interaction`) and classifying a `NotAuthorized` failure, not by stat-ing the `50-nbkp.rules` polkit file. This stays correct however the grant is configured (a differently-named rule, a group-scoped rule, …) and avoids guessing the file's name or location on a remote host.
- **The unlocked cleartext device** by discovering it via `lsblk` (the `crypt` child of the LUKS container), not by assuming the `luks-<uuid>` mapper name.
- **Filesystem type** via `stat -f` on the real filesystem, not a config hint.

The deliberate exception is the **fstab-mapping** check (`findmnt --fstab --target <path>`), which reads `/etc/fstab` — the configuration that *determines* where udisks will mount — rather than performing a trial mount. So the principle is "observe the actual state or outcome; only fall back to reading the input config when attempting the real operation would be destructive or premature." The cost is that detection does real work (and real SSH round-trips) during preflight, in exchange for accuracy that doesn't depend on how the host happens to be configured.

#### Preflight Conditional Probing

The preflight check system uses two layers: an **observation layer** (`volume_checks.py`, `endpoint_checks.py`) that probes raw state, and an **error interpretation layer** (`status.py`) that decides what constitutes a problem based on config. Not all capabilities are checked for every volume or endpoint — probing is selective. This is an intentional design choice driven by three categories of conditional logic.

##### Physical cascade dependencies

Probe B requires the result of probe A as input. These conditions live in the observation layer because they represent physical impossibilities, not policy decisions:

- `rsync_version_ok` requires `has_rsync` — can't run `rsync --version` if rsync isn't installed
- `is_btrfs_filesystem` requires `has_stat` — needs `stat -f -c %T`
- `btrfs_user_subvol_rm` requires `has_findmnt AND is_btrfs`
- the cleartext device discovery (`lsblk` of the LUKS container) requires `has_udisksctl` and a present LUKS container — can't mount/unmount/lock without the discovered device
- `staging_writable` requires `staging_exists`

##### Config-as-input probing

The probe itself needs config values as parameters — without them, the probe cannot be formulated. These also live in the observation layer:

- Device-present and unlock/discovery probes need `device_uuid` from `mount` — the LUKS container or filesystem UUID is the only thing to query
- The fstab-mapping check needs `volume.path` — without a declared path there is no expected mountpoint to verify (the discovery model applies instead)

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
| `which <command>` | Command availability (rsync, btrfs, stat, findmnt, udisksctl, lsblk) |
| `rsync --version` | Rsync version check (>= 3.0, reject macOS openrsync) |
| `stat -f -c %T <path>` | Detect btrfs filesystem type |
| `stat -c %i <path>` | Check btrfs subvolume (inode == 256) |
| `findmnt -T <path> -n -o OPTIONS` | Check btrfs mount options (e.g. `user_subvol_rm_allowed`) |
| `readlink <path>` | Read `latest` symlink target |
| `udisksctl status` | Check the udisks daemon is reachable |
| `lsblk -rno NAME,TYPE <dev>` | Discover the unlocked cleartext device (LUKS unlocked check) |
| `findmnt --source <dev> -n -o TARGET` | Check if a device is mounted and discover its mountpoint |

### Rsync synchronization

| Command | Purpose |
|---|---|
| `rsync -a --delete --delete-excluded --partial-dir=.rsync-partial --safe-links ...` | Data sync with default flags |
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

### Mount management (udisks)

All mount operations go through `udisksctl`, authorized by polkit (no `sudo`):

| Command | Purpose |
|---|---|
| `udisksctl unlock -b /dev/disk/by-uuid/<luks-uuid> --key-file /dev/stdin --no-user-interaction` | Unlock LUKS container (passphrase piped via stdin, without a trailing newline) |
| `lsblk -rno NAME,TYPE /dev/disk/by-uuid/<luks-uuid>` | Discover the unlocked cleartext device (`crypt` child → `/dev/mapper/<name>`) |
| `udisksctl mount -b <device> --no-user-interaction` | Mount volume (`<device>` = discovered cleartext mapper for encrypted, or `/dev/disk/by-uuid/<fs-uuid>` for unencrypted) |
| `findmnt --source <device> -n -o TARGET` | Discover the effective mountpoint (fstab path or `/run/media/<user>/<label>`) |
| `udisksctl unmount -b <device> --no-user-interaction` | Umount volume |
| `udisksctl lock -b /dev/disk/by-uuid/<luks-uuid> --no-user-interaction` | Lock LUKS container |

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
