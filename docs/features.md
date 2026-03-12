# Features

This section provides a high-level overview of nbkp's core capabilities.

To understand how to use these features, refer to the [Usage](./usage.md) documentation. 
To understand how these features are implemented, refer to the [Concepts](./concepts.md) documentation.

## Sync Engine

nbkp uses rsync under the hood to synchronize files between volumes. Syncs can flow in any direction between local and remote filesystems, are automatically ordered based on their dependencies, and support fine-grained control over rsync behavior.

- **Rsync-based backups** with configurable flags, filters, and compression
- **All direction combinations**: local-to-local, local-to-remote, remote-to-local, remote-to-remote (same server)
- **Sync dependencies**: when one sync's destination endpoint is another's source endpoint, syncs are automatically ordered topologically
- **Failure propagation**: a failed sync cancels all downstream dependents; independent syncs continue normally
- **Dry-run mode**: preview what would happen without making changes
- **Real-time progress**: four display modes (none, overall, per-file, full)
- **Per-sync rsync options**: checksum, compression, custom flags, default overrides, extra options
- **Rsync filters**: structured include/exclude rules, `merge`/`dir-merge` directives (per-directory filter files), raw rsync filter strings, and external filter files

## Snapshots

Each sync can optionally maintain point-in-time snapshots of the destination. Two snapshot backends are supported: btrfs (for btrfs filesystems) and hard-link (for any filesystem with hard-link support). Both provide space-efficient incremental history with automatic pruning.

- **Btrfs snapshots**: rsync to staging area, then create read-only btrfs snapshot with `latest` symlink
- **Hard-link snapshots**: rsync with `--link-dest` for space-efficient incremental snapshots on any filesystem supporting hard links (ext4, xfs, btrfs, etc.)
- **Automatic pruning**: configurable `max-snapshots` limit, pruned after each run
- **Safety**: `latest` symlink only updated after successful sync; orphaned snapshot directories cleaned up automatically

## SSH and Remote Access

Remote volumes are accessed over SSH. Endpoints are defined once and shared across volumes, with support for bastion hosts, agent forwarding, and automatic enrichment from `~/.ssh/config`. Connection options are fully configurable per endpoint.

- **Reusable SSH endpoints** shared across multiple remote volumes
- **Proxy jump / bastion hosts**: single-hop (`proxy-jump`) or multi-hop chains (`proxy-jumps`) through bastion hosts
- **SSH agent forwarding**: forward the local agent to the destination host
- **`~/.ssh/config` integration**: unset endpoint fields (`port`, `user`, `key`) are automatically filled from SSH config
- **Endpoint inheritance** (`extends`): child endpoints inherit and selectively override parent fields
- **Structured connection options**: connect timeout, compression, keepalive, host key verification, known hosts file, agent forwarding, algorithm selection
- **Dual SSH paths**: SSH CLI for rsync, Fabric/Paramiko for status checks and btrfs operations

## Nomadic usage / Endpoint Filtering

A single remote volume can declare multiple SSH endpoints for different network contexts (home network access vs WAN). At runtime, nbkp selects the best reachable endpoint based on location tags or network type, so the same config works whether you're on the home LAN or on the road.

- **Location-aware endpoints**: tag endpoints with a `location` (e.g. `home`, `travel`) and select at runtime with `--locations`
- **Network-aware selection**: `--private` prefers LAN endpoints, `--public` prefers WAN endpoints
- **Multiple endpoints per volume**: declare candidate endpoints and let the tool pick the best reachable one
- **DNS-based filtering**: unreachable endpoints (failed DNS resolution) are automatically excluded

## Volume Safety

nbkp uses lightweight sentinel files to guard against syncing to the wrong place. A sync only activates when both its source and destination volumes are confirmed present, which is especially important for removable drives that may not always be mounted.

- **Sentinel files**: volumes require `.nbkp-vol`, sources require `.nbkp-src`, destinations require `.nbkp-dst`
- **Removable drive awareness**: syncs only activate when both source and destination sentinels are present, preventing data loss from unmounted drives
- **Remote reachability checks**: SSH connectivity verified before attempting any sync

## Pre-flight Checks

Before running any sync, nbkp validates that all required infrastructure is in place: volumes are reachable, tools are installed at the right version, and filesystem capabilities match the configured snapshot mode.

- **Volume reachability**: sentinel files, SSH connectivity, DNS resolution
- **Rsync verification**: checks rsync is installed and version is 3.0+ (rejects macOS openrsync)
- **Btrfs readiness**: filesystem type, subvolume existence, mount options (`user_subvol_rm_allowed`), required directories
- **Hard-link readiness**: filesystem hard-link support, required directory structure
- **Strict mode**: optionally exit non-zero on any inactive sync

## Configuration

Backup configuration is expressed in a single YAML file that defines SSH endpoints, volumes, sync endpoints, and syncs. The config is validated at load time with detailed error messages.

- **YAML config** with standard search order: explicit path, `$XDG_CONFIG_HOME/nbkp/config.yaml`, `/etc/nbkp/config.yaml`
- **Reusable sync endpoints**: define (volume, subdir, snapshot config) once, reference by slug from syncs — prevents duplication and conflicting configurations
- **Pydantic validation**: structured errors with context for invalid configs
- **Config display**: `config show` renders parsed config as tables or JSON
- **Graph visualization**: `config graph` displays the backup chain as a Rich tree, ASCII art (mermaid-ascii), raw mermaid syntax, or JSON
- **Cross-reference validation**: circular `extends` and `proxy-jump` chains, unique (volume, subdir) per endpoint, unique destination per sync — all detected at load time

## Shell Script Generation (`sh`)

The `sh` command compiles a config into a self-contained bash script that reproduces the same backup operations as `run`, without requiring Python or the config file at runtime. Useful for deploying backups on minimal systems or embedding scripts alongside the data they protect.

- **Standalone bash scripts**: no Python or config file required at runtime
- **Portable paths**: `--relative-src` / `--relative-dst` make paths relative to the script location
- **Runtime flags**: generated scripts support `--dry-run` and `-v` / `-vv` / `-vvv`
- **Full feature parity**: rsync, SSH options, filters, btrfs snapshots, hard-link snapshots, pre-flight checks, dependency ordering, failure propagation
- **Disabled syncs preserved**: appear as commented-out blocks for easy re-enabling

## Outputs

Every command supports both human-friendly and machine-readable output, making nbkp suitable for interactive use and scripting alike.

All commands provide:

- **Human-readable output**: Rich-formatted tables, spinners, and progress bars
- **JSON output**: machine-readable format for scripting and automation

## Testing and QA

Built-in helpers make it easy to stand up a realistic test environment or preview all output formats without a real backup config.

- **Demo/Docker environment setup**: Easily create a reproducible test environment with volumes, syncs, optional Docker containers (bastion + storage with btrfs), and test data
- **Output rendering demo**: Render all formatting functions with sample data for visual QA and documentation examples

## Supported Commands

See [Usage](./usage.md) for detailed command descriptions and examples.