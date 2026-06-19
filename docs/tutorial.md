# Tutorial: a complete laptop → Raspberry Pi backup

This walks through building a real, multi-drive backup setup from scratch:

- a **macOS laptop** that orchestrates everything (runs `nbkp`, holds the SSH keys and the LUKS passphrases),
- a **Raspberry Pi** (Ubuntu) hosting several **LUKS-encrypted btrfs** drives that nbkp unlocks, mounts, writes to, and locks again automatically,
- a couple of **local USB SSDs** on the laptop as well,
- **btrfs snapshots** on the Pi and **hard-link snapshots** on the SSDs for point-in-time recovery,
- **chained backups**, where a drive that *receives* a backup then serves as the *source* for further backups to other drives — the Pi's `seagate8tb` hub fans data out to the rest of the Pi's drives, all on the Pi itself (no laptop round-trip).

It mirrors [config-examples/personal-setup.yaml](config-examples/personal-setup.yaml), a real-world config that follows the same no-fstab model used here. The full reference material lives in [concepts.md](./concepts.md) (the data model), [internals.md](./internals.md) (runtime behavior), and [usage.md](./usage.md) (commands and the udisks mount-management reference) — this tutorial links to the relevant sections rather than repeating them.

> **Assumptions.** A Raspberry Pi running **Ubuntu 26.04** with networking up and SSH reachable from the laptop, the drives physically attached, and `sudo` on the Pi. On the laptop: macOS with [Homebrew](https://brew.sh/). Everything destructive (LUKS format, mkfs) is clearly marked.

---

## How nbkp thinks (60-second primer)

Four building blocks, defined once each in one YAML file. Full detail in [concepts.md](./concepts.md).

- **SSH endpoint** — how the laptop reaches a remote host (the Pi). You can give one host several endpoints with `location` tags (e.g. `home` LAN vs `travel` WAN); nbkp picks the reachable one at runtime.
- **Volume** — a named filesystem location: a `local` path on the laptop, or a `remote` path on the Pi reached via an SSH endpoint. A volume may declare a `mount` block so nbkp manages its unlock/mount lifecycle.
- **Sync endpoint** — a `(volume, subdir)` pair, optionally with snapshots enabled.
- **Sync** — a one-way rsync from a source endpoint to a destination endpoint.

Two more concepts you'll meet below:

- **Sentinels** — tiny marker files (`.nbkp-vol`, `.nbkp-src`, `.nbkp-dst`) that prove a volume is really mounted and a path is really the one you meant. A sync only runs when all of its sentinels are present, so an unmounted drive is *skipped*, never silently created. See [Sentinel Files](./internals.md#sentinel-files).
- **Snapshots** — point-in-time copies. **btrfs** snapshots (on the Pi's btrfs drives) are copy-on-write and cheap; **hard-link** snapshots (on the SSDs) work on any filesystem. See [Snapshots](./concepts.md#snapshots).

### The topology we're building

```
macOS laptop  (orchestrator: nbkp + SSH keys + Keychain)
│
│  Local volumes
│   ├─ laptop-home (~)              ┐ originals
│   ├─ laptop-docs (/Volumes/docs)  ┘
│   ├─ rocketnano1tb (USB SSD)  originals: applications, audio, education
│   └─ rocketnano2tb (USB SSD)  originals: photos
│       (the SSDs ALSO receive laptop docs + ~/.config backups — dual role)
│
│  Laptop → SSD backups            (hard-link snapshots)
│   docs         → rocketnano1tb, rocketnano2tb
│   home-config  → rocketnano1tb, rocketnano2tb   (~/.config/{nbkp,photree,mise})
│
└─ ssh ─► Raspberry Pi  —  5 × LUKS-encrypted btrfs   (home LAN / travel WAN)

   (1) Collect everything onto the hub          (btrfs snapshots)
        laptop-docs ─┐
        laptop-home  │
        rn1tb apps   ├─►  seagate8tb            "the hub"
        rn1tb audio  │     /backups/{applications, audio, docs,
        rn1tb edu    │                 education, home, photos}
        rn2tb photos ┘

   (2) Hub fans out to the other drives          (remote → remote, on the Pi)
        seagate8tb │ applications → seagate1tb, wd6tb
                   │ audio        → seagate1tb, wd6tb
                   │ docs         → seagate1tb, seagate2tb, wd6tb, iomega1tb
                   │ education    → seagate1tb, wd6tb
                   │ home         → wd6tb
                   │ photos       → seagate2tb, wd6tb, iomega1tb (recent only)
```

Two things make this more than a star of independent copies:

- **`seagate8tb` is a hub.** Laptop and SSD data land there first (step 1), then nbkp replicates each category onward to the other Pi drives (step 2). Those step-2 syncs are *remote→remote on the same host*, so they never round-trip through the laptop — and they only run after the matching step-1 sync succeeds, because a destination endpoint of one sync (`seagate8tb/backups/docs`) is the *source* of the next. nbkp orders these chains automatically and cancels the downstream copies if an upstream one fails.
- **The SSDs are dual-role.** `rocketnano1tb`/`rocketnano2tb` hold their own originals (applications, audio, education, photos) *and* receive backups of the laptop's docs and `~/.config` — so the same volume is a source for some syncs and a destination for others.

Not every drive gets everything: `seagate1tb` skips `home`/`photos`, `seagate2tb`/`iomega1tb` take only `docs`+`photos`, and `iomega1tb` keeps only recent photos (it's the smallest). That selectivity is just per-sync filters.

---

## Part A — Prepare the Raspberry Pi (the backup target)

All of Part A runs **on the Pi**, over SSH. It's a one-time filesystem setup; once done, nbkp drives the unlock/mount/lock lifecycle for you.

> **Already running an older crypttab/fstab setup?** You don't need to reformat — your data and sentinels stay put. Convert in place instead of running the destructive format steps (A3's `luksFormat`, A5's `mkfs`):
> 1. Do **A4** (the `mount_options.conf` step) so btrfs snapshot pruning keeps working without fstab.
> 2. On the Pi, remove each drive's `/etc/fstab` entry (and, optionally, its `/etc/crypttab` entry — harmless, just no longer needed). udisks will then mount at `/run/media/ubuntu/<label>`.
> 3. Make sure each btrfs filesystem has a **label** (it becomes that path). Set one live with `sudo btrfs filesystem label /mnt/<drive> <name>`, then unmount + lock the drive.
> 4. In the config, drop `path:` (and the old `mapper-name:`) from each remote volume — the LUKS container UUID is unchanged, so `device-uuid` stays the same.
> 5. Then do **A6** (polkit) and **Part B**, and verify with `nbkp preflight troubleshoot`.

### A1. Install packages

```bash
# Keep the system current
sudo apt update && sudo apt upgrade && sudo apt dist-upgrade

# A multiplexer is handy for long-running work over SSH
sudo apt install tmux

# Encryption + btrfs + exFAT + dedup tooling
sudo apt install cryptsetup btrfs-progs exfat-fuse exfatprogs duperemove

# Mount management: udisks2 drives unlock/mount/lock; the btrfs module lets it
# mount btrfs filesystems. nbkp talks to udisks — there is no sudo in its path.
sudo apt install udisks2 udisks2-btrfs

# Make sure the daemon is enabled and reachable
sudo systemctl enable --now udisks2.service
udisksctl status        # should list block devices
```

### A2. Identify the disks

```bash
lsblk -o NAME,SIZE,TYPE,FSTYPE,LABEL,MODEL
```

Pick the device node for each drive (e.g. `/dev/sda`). **Double-check** — the next step erases it.

### A3. Encrypt each drive with LUKS

> ⚠️ **Destructive.** `luksFormat` wipes the target device. Make sure it's the right one and holds no data you need.

Do this once per drive. Using `seagate8tb` as the running example:

```bash
sudo cryptsetup luksFormat /dev/sda      # type YES, set a strong passphrase

# Record the LUKS *container* UUID — it's the only thing nbkp needs to find
# this drive (mount.device-uuid in the config).
sudo cryptsetup luksUUID /dev/sda
# e.g. 5941f273-f73c-44c5-a3ef-fae7248db1b6
```

Collect the UUID of every drive — this is the only identifier that follows the disk regardless of which port it's plugged into, and it's what guarantees nbkp/udisks mount the *correct* physical device.

### A4. Let udisks apply `user_subvol_rm_allowed` to btrfs

This tutorial keeps the system side minimal: **no crypttab, no fstab.** udisks unlocks each LUKS container, names the cleartext device `/dev/mapper/luks-<uuid>`, and mounts the filesystem at `/run/media/ubuntu/<label>` — a path nbkp discovers at runtime. There's no per-drive system config to maintain.

There's exactly one piece of *global* config. nbkp prunes old btrfs snapshots, which needs the `user_subvol_rm_allowed` mount option — and nbkp never injects mount options itself (udisks rejects anything not on its allowlist). With no fstab line to carry it, you allow it through udisks once, for all btrfs volumes:

```ini
# /etc/udisks2/mount_options.conf
[defaults]
btrfs_allow=user_subvol_rm_allowed
btrfs_defaults=user_subvol_rm_allowed
```

Both keys are required — `btrfs_allow` *permits* the option, `btrfs_defaults` *applies* it by default — then reload the daemon:

```bash
sudo systemctl restart udisks2.service
```

Background on why this (and not `-o` at mount time): the [Mount options](./internals.md#volume-mount-management) principle. (Skip this step for non-btrfs drives.)

> **Advanced — fixed `/mnt/<drive>` paths via crypttab + fstab.** If you'd rather pin each drive to a stable path (for other tools, or to avoid `mount_options.conf`), set up `/etc/crypttab` (friendly mapper names) and `/etc/fstab` (carrying `user_subvol_rm_allowed`), and set `path:` on each volume. nbkp supports this fully, but it's more moving parts and the crypttab/fstab correctness is **on you** — nbkp's help for it is limited (it flags a `path` that no fstab entry maps, and little else). The worked examples live in [usage.md → Mount management](./usage.md#mount-management-with-udisks2).

### A5. Create each filesystem and seed its structure

Per drive (running example: `seagate8tb`). Unlock the container once, create a **labeled** btrfs filesystem — the label is exactly what udisks turns into the `/run/media/ubuntu/<label>` path — then seed the sentinels and snapshot layout, and lock it again.

```bash
UUID=5941f273-f73c-44c5-a3ef-fae7248db1b6   # the LUKS container UUID from A3

# Unlock + format. The LABEL (-L) becomes the /run/media/ubuntu/<label> path.
sudo cryptsetup open UUID=$UUID seed          # prompts for the passphrase
sudo mkfs.btrfs -L seagate8tb /dev/mapper/seed

# Mount somewhere throwaway to seed the layout. Sentinels and dirs live *inside*
# the filesystem, so this temporary mountpoint is irrelevant — they end up
# wherever udisks mounts the drive at runtime.
sudo mount /dev/mapper/seed /mnt

# (1) Volume sentinel at the filesystem root.
# (2) Per destination endpoint (a sync's subdir). seagate8tb-backups-docs uses
#     btrfs snapshots, so it needs a staging subvolume + snapshots dir. The hub
#     is ALSO a source for the downstream drives, so its backup paths carry both
#     .nbkp-dst (written here) and .nbkp-src (read by the fan-out syncs).
sudo mkdir -p /mnt/backups/docs
sudo btrfs subvolume create /mnt/backups/docs/staging
sudo mkdir /mnt/backups/docs/snapshots
sudo touch /mnt/.nbkp-vol \
           /mnt/backups/docs/.nbkp-dst \
           /mnt/backups/docs/.nbkp-src

# Hand everything to the backup user, then unmount + lock.
sudo chown -R ubuntu:ubuntu /mnt
sudo umount /mnt
sudo cryptsetup close seed
```

Repeat for every endpoint on every drive. The sentinel rules, in one place:

| Sentinel | Where | Meaning |
|---|---|---|
| `.nbkp-vol` | each volume root (`/run/media/ubuntu/<label>`) | the drive is mounted here |
| `.nbkp-src` | each path read *from* | this source is ready |
| `.nbkp-dst` | each path written *to* | this destination is ready |

Plus, per snapshot destination: a `staging` subvolume + `snapshots/` dir for **btrfs**, or just a `snapshots/` dir for **hard-link**. Details and the symlink lifecycle: [Snapshot Lifecycle](./internals.md#snapshot-lifecycle). (This is repetitive across many endpoints — a small shell loop over your subdir list is the easy way.)

### A6. Authorize udisks with a polkit rule

Because nbkp connects over SSH (an *inactive* login session), udisks would normally demand interactive admin authentication. A single polkit rule grants the backup user the udisks actions unconditionally. Generate it **on the laptop** from your finished config (Part B), then install it **on the Pi**:

```bash
# On the laptop, generate the rule for the Pi's backup user (-u ubuntu).
# setup-auth prints a short human-readable header (lines starting with `#`)
# above the rule; strip it so the saved file is a clean polkit rule.
nbkp disks setup-auth -c ~/.config/nbkp/config.yaml -u ubuntu \
  | grep -v '^#' > 50-nbkp.rules
scp 50-nbkp.rules ubuntu@10.0.0.42:/tmp/

# On the Pi:
sudo install -m 0644 /tmp/50-nbkp.rules /etc/polkit-1/rules.d/50-nbkp.rules
```

The rule grants the `ubuntu` user the udisks actions (mount, unlock, lock, …) so the unattended SSH path needs no password. It is the **only** authorization artifact — no sudoers. More: [usage.md → Prerequisites](./usage.md#prerequisites) and [Why polkit-only](./internals.md#why-polkit-only).

The Pi is now ready: each drive is formatted, seeded, and locked, and nbkp owns the unlock → mount → sync → umount → lock lifecycle from here on.

---

## Part B — Set up the laptop (the orchestrator)

### B1. Install nbkp

The `keyring` extra pulls in the macOS Keychain backend for passphrases:

```bash
brew install pipx && pipx ensurepath
pipx install 'nbkp[keyring]'
nbkp --version
```

Full options (other extras, shell completion): [installation.md](./installation.md).

### B2. SSH access to the Pi

nbkp uses your normal SSH setup. Install a key and confirm a passwordless login:

```bash
ssh-copy-id ubuntu@10.0.0.42
ssh ubuntu@10.0.0.42 true && echo "ssh ok"
```

The config declares two endpoints for the same Pi — one tagged `home` (LAN IP) and one `travel` (public hostname) — so the same config works from either network. nbkp fills in port/user/key from `~/.ssh/config` when not set explicitly. See [Endpoint Filtering](./internals.md#endpoint-filtering).

### B3. Store the LUKS passphrases in the Keychain

nbkp's default credential provider is `keyring`. Store one entry per `passphrase-id` in your config (the service name is always `nbkp`):

```bash
keyring set nbkp seagate8tb        # prompts; stored encrypted in the macOS Keychain
keyring set nbkp seagate1tb
keyring set nbkp seagate2tb
keyring set nbkp wd6tb
keyring set nbkp iomega1tb
```

Passphrases never live in the config. Other providers (`prompt`, `env`, `command`) are described in [concepts.md](./concepts.md#encryption--luks-encryption-config).

### B4. Write the config

Create `~/.config/nbkp/config.yaml`. The shape of an encrypted remote volume in the no-crypttab/no-fstab model is:

```yaml
ssh-endpoints:
  raspberry-pi4-lan:
    host: 10.0.0.42
    user: ubuntu
    location: home
  raspberry-pi4-wan:
    host: sami.example.com
    user: ubuntu
    location: travel

volumes:
  seagate8tb:                         # LUKS + btrfs on the Pi
    type: remote
    ssh-endpoint: raspberry-pi4-lan
    # No `path`: with no fstab entry, udisks mounts at /run/media/ubuntu/seagate8tb
    # (the filesystem label from A5), which nbkp discovers at runtime.
    mount:
      device-uuid: 5941f273-f73c-44c5-a3ef-fae7248db1b6   # the LUKS container UUID
      encryption:
        type: luks
        passphrase-id: seagate8tb     # the Keychain entry from B3

sync-endpoints:
  seagate8tb-backups-docs:
    volume: seagate8tb
    subdir: backups/docs
    btrfs-snapshots:
      enabled: true
      max-snapshots: 30

syncs:
  laptop-docs-to-seagate8tb:
    source: laptop-docs
    destination: seagate8tb-backups-docs
    filters:
      - dir-merge: .rsync-filter      # per-directory include/exclude rules
```

A few things worth knowing, each with a reference for the full story:

- **No `mapper-name`.** udisks names the unlocked device (or honors a crypttab name if you added one), and nbkp *discovers* it — the config only needs the LUKS container UUID and the passphrase id. See [Volume Mount Management](./internals.md#volume-mount-management).
- **No `path`.** Omitting it selects the discovered `/run/media/<user>/<label>` model used throughout this tutorial. Set `path` only if you went the fixed-path (fstab) route in A4's advanced note — see the [fstab × crypttab matrix](./usage.md#mount-point-models-fstab--crypttab).
- **Snapshots are per-endpoint.** btrfs on the Pi drives, hard-link on the SSDs. Add `max-snapshots` to prune automatically.
- **Filters** trim what each sync copies — `dir-merge: .rsync-filter` reads per-directory rule files; you can also inline `+`/`-` rules. See [Filters](./concepts.md#filters).
- **Chained syncs sort themselves.** When one sync's destination is another's source (the `seagate8tb` hub), nbkp orders them automatically and cancels downstream syncs if an upstream one fails. See [Sync Dependencies](./internals.md#sync-dependencies-and-execution-order).

### B5. Sentinels on the local volumes

The Pi drives got their sentinels in Part A. Do the same for the laptop's local volumes and SSDs — `.nbkp-vol` at each root, `.nbkp-src`/`.nbkp-dst` per endpoint, and a `snapshots/` dir for hard-link destinations:

```bash
touch /Volumes/docs/.nbkp-vol
touch /Volumes/docs/.nbkp-src                     # laptop-docs is a source
touch /Volumes/rocketnano1tb/.nbkp-vol
mkdir -p /Volumes/rocketnano1tb/backups/docs/snapshots
touch    /Volumes/rocketnano1tb/backups/docs/.nbkp-dst
```

---

## Part C — Verify and run

```bash
# 1. Diagnose anything not ready — prints step-by-step fixes for each problem
#    (missing sentinel, udisksd down, missing polkit rule, fstab mismatch, …)
nbkp preflight troubleshoot

# 2. When clean, run. nbkp unlocks + mounts each drive, syncs, then umounts + locks.
nbkp run
```

`nbkp run` is the everyday command. Useful variants (full list in the [CLI reference](./cli-reference.md)):

```bash
nbkp run --location home            # prefer the home-LAN endpoint
nbkp run --exclude-location home    # on the road: skip anything only reachable at home
nbkp run --no-mount --no-umount     # volumes are already mounted; skip mount management
nbkp disks mount   --name seagate8tb    # mount one drive by hand
nbkp disks umount  --name seagate8tb
```

Inactive syncs (a drive that isn't plugged in, a Pi that's unreachable) are **skipped**, not errors — that's the nomadic design. Tighten that with `--strictness` when you expect everything to be present; see [Strictness](./internals.md#strictness).

### Scheduling

Wrap `nbkp run` in a `launchd` agent (macOS) or cron job. Because syncs skip cleanly when their volumes are absent, a periodic run "just works" — it backs up whatever happens to be reachable at the time.

---

## Where to go next

- [usage.md](./usage.md) — every command, more examples, and the full udisks mount-management reference
- [concepts.md](./concepts.md) — the complete configuration reference (every field)
- [internals.md](./internals.md) — what nbkp does at runtime and why; the external commands it invokes
- [features.md](./features.md) — the full feature list
