# Architecture

## Module Overview

```
nbkp/
  config/
    protocol.py      Config model: volumes, SSH endpoints, sync endpoints, syncs
    loader.py         YAML loading, search order, validation
    resolution.py     SSH endpoint resolution (enrichment, proxy chains)
  remote/
    ssh.py            SSH CLI argument building (-e "ssh -p PORT -i KEY ...")
    fabricssh.py      Fabric/Paramiko connections for status checks and btrfs ops
    resolution.py     Endpoint filtering (location, private/public, DNS)
  sync/
    ordering.py       Topological sort, dependency graph, failure propagation
    rsync.py          Rsync command building and execution
    btrfs.py          Btrfs snapshot creation, listing, pruning
    hardlinks.py      Hard-link snapshot creation, orphan cleanup, pruning
    symlink.py        latest symlink management (read/update)
    runner.py         Orchestrator: check → sort → dispatch per snapshot mode
  check.py            Pre-flight validation (sentinels, SSH, rsync, btrfs, hard-link)
  scriptgen.py        Compile config into standalone bash script
  output.py           Rich/JSON formatting for all commands
  cli.py              Typer CLI: check, run, sh, prune, troubleshoot, config show
  democli.py          Demo/QA helpers: seed environments, render sample output
```

## Execution Flow

```mermaid
flowchart TD
    CLI["CLI (cli.py)"]

    CLI --> Resolve["resolve_all_endpoints()
    config/resolution.py
    ─────────────────────
    extends inheritance
    ~/.ssh/config enrichment
    proxy-jump chain resolution
    endpoint filtering"]

    Resolve --> Check["check_all_syncs()
    check.py
    ─────────────────────
    Volume sentinels, SSH reachability
    Endpoint sentinels, rsync version
    btrfs/hard-link readiness
    latest symlink validity"]

    Check --> Sort["sort_syncs()
    sync/ordering.py
    ─────────────────────
    Build dependency graph
    from endpoint slugs
    Topological sort, detect cycles"]

    Sort --> Run["run_all_syncs()
    sync/runner.py"]

    Run --> Skip{Inactive or
    cancelled?}
    Skip -- Yes --> NextSync[Next sync]
    Skip -- No --> ResolveEP["Resolve endpoints
    config.source_endpoint(sync)
    config.destination_endpoint(sync)"]

    ResolveEP --> Dispatch{snapshot_mode?}

    Dispatch -- none --> Rsync["run_rsync()"]
    Rsync --> Done[Done]

    Dispatch -- btrfs --> RsyncBtrfs["run_rsync()
    → staging/"]
    RsyncBtrfs --> Snapshot["create_snapshot()"]
    Snapshot --> Symlink["update_latest_symlink()"]
    Symlink --> Prune["prune_snapshots()"]

    Dispatch -- hard-link --> Cleanup["cleanup_orphans()"]
    Cleanup --> LinkDest["resolve --link-dest
    from previous snapshot"]
    LinkDest --> CreateDir["create_snapshot_dir()"]
    CreateDir --> RsyncHL["run_rsync()
    → snapshots/timestamp/"]
    RsyncHL --> SymlinkHL["update_latest_symlink()"]
    SymlinkHL --> PruneHL["prune_snapshots()"]

    Done --> NextSync
    Prune --> NextSync
    PruneHL --> NextSync

    RsyncBtrfs -- failure --> Cancel["Cancel all
    downstream syncs"]
    RsyncHL -- failure --> Cancel
    Rsync -- failure --> Cancel
    Cancel --> NextSync
```
