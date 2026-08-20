# Tutorial: backing up a laptop to a Raspberry Pi backup hub, with a relay drive that fans out

This walks through building a real, multi-drive backup setup from scratch:

- a **macOS laptop** that orchestrates everything (runs `nbkp`, holds the SSH keys and the LUKS passphrases),
- the **backup hub** — a **Raspberry Pi** (Ubuntu) hosting several **LUKS-encrypted btrfs** drives that nbkp unlocks, mounts, writes to, and locks again automatically,
- a couple of **local USB SSDs** on the laptop as well,
- **btrfs snapshots** on the hub's drives and **hard-link snapshots** on the local SSDs for point-in-time recovery,
- **chained backups**, where a drive that *receives* a backup then serves as the *source* for further backups to other drives — the hub's `seagate8tb` **relay** drive fans data out to the hub's remaining drives, all on the hub itself (no laptop round-trip).

It mirrors [config-examples/personal-setup.yaml](config-examples/personal-setup.yaml), a real-world config that follows the same no-fstab model used here. The full reference material lives in [concepts.md](./concepts.md) (the data model), [internals.md](./internals.md) (runtime behavior), and [usage.md](./usage.md) (commands and the udisks mount-management reference) — this tutorial links to the relevant sections rather than repeating them.

> **Assumptions.** A Raspberry Pi running **Ubuntu 26.04** with networking up and SSH reachable from the laptop, the drives physically attached, and `sudo` on the Pi. On the laptop: macOS with [Homebrew](https://brew.sh/). Everything destructive (LUKS format, mkfs) is clearly marked.

---

## How nbkp thinks (60-second primer)

Four building blocks, defined once each in one YAML file. Full detail in [concepts.md](./concepts.md).

- **SSH endpoint** — how the laptop reaches a remote host (the backup hub). You can give one host several endpoints with `location` tags (e.g. `home` LAN vs `travel` WAN); nbkp picks the reachable one at runtime.
- **Volume** — a named filesystem location: a `local` path on the laptop, or a `remote` path on the hub reached via an SSH endpoint. A volume may declare a `mount` block so nbkp manages its unlock/mount lifecycle.
- **Sync endpoint** — a `(volume, subdir)` pair, optionally with snapshots enabled.
- **Sync** — a one-way rsync from a source endpoint to a destination endpoint.

Two more concepts you'll meet below:

- **Sentinels** — tiny marker files (`.nbkp-vol`, `.nbkp-src`, `.nbkp-dst`) that prove a volume is really mounted and a path is really the one you meant. A sync only runs when all of its sentinels are present, so an unmounted drive is *skipped*, never silently created. See [Sentinel Files](./internals.md#sentinel-files).
- **Snapshots** — point-in-time copies. **btrfs** snapshots (on the hub's btrfs drives) are copy-on-write and cheap; **hard-link** snapshots (on the local SSDs) work on any filesystem. See [Snapshots](./concepts.md#snapshots).

---

## The topology we're building

```
ORIGINALS — where the only copy of something lives
   laptop          laptop-home (~), laptop-docs (/Volumes/docs)
   rocketnano1tb   applications, audio, education   ┐ local USB SSDs
   rocketnano2tb   photos                           ┘

──── ① LOCAL BACKUPS ─── laptop → USB SSD ─── hard-link snapshots ───────────
   docs         →  rocketnano1tb, rocketnano2tb
   home-config  →  rocketnano1tb, rocketnano2tb   (~/.config/{nbkp,photree,mise})
   ⇒ the SSDs are DUAL-ROLE: they hold originals *and* receive these backups

──── ② REMOTE BACKUPS ─── laptop + SSDs ─ssh─► the relay ─── btrfs snapshots ─
   laptop-docs, laptop-home   ┐
   rn1tb apps, audio, edu     ├─►  seagate8tb          ← "the relay"
   rn2tb photos               ┘    /backups/{applications, audio, docs,
                                              education, home, photos}

──── ③ RELAY FAN-OUT ─── hub → hub, never through the laptop ────────────────
   seagate8tb │ applications  →  seagate1tb, wd6tb
              │ audio         →  seagate1tb, wd6tb
              │ docs          →  seagate1tb, seagate2tb, wd6tb, iomega1tb
              │ education     →  seagate1tb, wd6tb
              │ home          →  wd6tb
              │ photos        →  seagate2tb, wd6tb, iomega1tb (recent only)

   ② and ③ both live on the backup hub — a Raspberry Pi holding 5 ×
   LUKS-encrypted btrfs drives, reachable on the home LAN or over the WAN.
```

Every arrow above is a `sync` in [config-examples/personal-setup.yaml](config-examples/personal-setup.yaml) — 24 of them across 30 sync endpoints. B4 has you start from that file rather than retype it.

Two things make this more than a star of independent copies:

- **③ chains off ②.** A fan-out sync only runs once the matching collect sync has succeeded, because a destination endpoint of one (`seagate8tb/backups/docs`) is the *source* of the next. nbkp derives that ordering itself and cancels the downstream copies when an upstream one fails. Being *remote→remote on the same host* is what keeps the data off the laptop in step ③.
- **Dual-role volumes need no special declaration.** The SSDs are the *source* of some syncs (their own originals, in ②) and the *destination* of others (the laptop's docs and `~/.config`, in ①). A volume is whatever the syncs referencing it make it.

Not every drive gets everything, as ③ shows: `seagate1tb` skips `home`/`photos`, `seagate2tb`/`iomega1tb` take only `docs`+`photos`, and `iomega1tb` keeps only recent photos (it's the smallest). That selectivity is just per-sync filters.

---

## Part A — Prepare the Raspberry Pi (the backup hub)

All of Part A runs **on the Pi**, over SSH. It's a one-time filesystem setup; once done, nbkp drives the unlock/mount/lock lifecycle for you.

> **Already running an older crypttab/fstab setup?** You don't need to reformat — your data and sentinels stay put. Convert in place instead of running the destructive format steps (A3's `mklabel`, `luksFormat` and `mkfs`):
> 1. Do **A4** (the `mount_options.conf` step) so btrfs snapshot pruning keeps working without fstab.
> 2. On the Pi, remove each drive's `/etc/fstab` entry (and, optionally, its `/etc/crypttab` entry — harmless, just no longer needed). udisks will then mount at `/run/media/ubuntu/<fs-label>`.
> 3. Make sure each btrfs **filesystem** has a label (it becomes that path). Set one live with `sudo btrfs filesystem label /mnt/<drive> <name>-btrfs`, then unmount + lock the drive. A LUKS label is **not** a substitute — it sits outside the encrypted container and udisks never uses it for the mountpoint, so a drive carrying only a LUKS label still lands on `/run/media/ubuntu/<fs-uuid>`. To label the container too (worth doing — it's the only name readable while the drive is locked): `sudo cryptsetup config /dev/sdX --label <name>-luks`, which needs no passphrase. See [A3](#a3-partitions-luks-containers-and-filesystems) for why the suffixes differ. If your partitions carry a generic GPT name, `sudo parted /dev/sdX name 1 <name>-part` renames them too — cosmetic, but it's what makes `lsblk` identify a drive without unlocking it.
> 4. In the config, drop `path:` from each remote volume — the LUKS container UUID is unchanged, so `device-uuid` stays the same.
> 5. Then install the polkit rule (second half of **A4**) and do **Part B**, verifying with `nbkp preflight troubleshoot`.

### A1. Install packages

```bash
# Keep the system current
sudo apt update && sudo apt upgrade && sudo apt dist-upgrade

# A multiplexer is handy for long-running work over SSH
sudo apt install tmux

# Partitioning: parted is usually already present; gdisk provides sgdisk
sudo apt install parted gdisk

# Encryption + btrfs + exFAT + dedup tooling
sudo apt install cryptsetup btrfs-progs exfat-fuse exfatprogs duperemove

# Mount management: udisks2 drives unlock/mount/lock; the btrfs module lets it
# mount btrfs filesystems. nbkp talks to udisks — there is no sudo in its path.
sudo apt install udisks2 udisks2-btrfs

# Make sure the daemon is enabled and reachable
sudo systemctl enable --now udisks2.service
udisksctl status        # should list block devices
```

### A2. Identify the disks and declare them

```bash
# What is attached, and what is already on it
lsblk -o NAME,SIZE,TYPE,FSTYPE,PARTLABEL,LABEL,MODEL,SERIAL

# Stable whole-disk paths — these are what go in the config below
ls -l /dev/disk/by-id/ | grep -v -- '-part'
```

`MODEL` and `SERIAL` come from the drive's firmware — or, for a USB drive, from its bridge — and are not settable. They are how you match a `/dev/sdX` to a physical drive on the shelf, which is worth doing before a destructive step.

**Address disks by `/dev/disk/by-id/`, not `/dev/sdX`.** A3 writes a partition table, and `/dev/sdX` is assigned in discovery order — a drive that re-enumerates between when you write the config and when you run A3 means `mklabel` lands on the wrong disk, and confirming the drive *name* won't catch it. The `by-id` symlinks encode vendor, model and serial, so they follow the physical drive across reboots and ports.

A disk usually has several `by-id` names — `usb-…`, `ata-…`, `wwn-…`, `scsi-…` — and they are not equivalent. An external drive is two things stacked: the **mechanism** (with its own model and serial) and the **enclosure** around it, whose bridge chip speaks USB to the host and SATA to the mechanism, reporting an identity of its own. Different `by-id` names come from different layers:

- **Prefer `usb-<vendor>_<model>_<serial>`** for a USB drive: the enclosure's view, which is what a per-drive-slot config wants.
- **Distrust `wwn-`/`scsi-` on USB.** Bridges often synthesize placeholders — `wwn-0x5000000000000001` is not a real WWN — and those are not unique across two enclosures of the same make.
- **`ata-…` means the bridge passes the drive's own ATA identification through.** That name follows the bare mechanism, not the enclosure, so it changes meaning if you ever re-house the drive.

Whichever you pick, match the symlink's target against the `lsblk` listing to confirm it's the drive you meant.

Then declare the whole set once, in a file the later steps source. A3's loops all read it, so each drive's facts live in exactly one place:

```bash
# drive-config.sh — associative arrays need bash 4+ (Ubuntu ships bash 5)

# Iteration order. The relay first, since the others receive from it.
DRIVES=(seagate8tb wd6tb seagate1tb seagate2tb iomega1tb)

# The drive that is also a SOURCE for the fan-out syncs, so its backup paths need
# .nbkp-src as well as .nbkp-dst. Leave empty if your setup has no relay.
RELAY_DRIVE=seagate8tb

# One entry per destination endpoint on that drive: the endpoint's `subdir`,
# copied from `sync-endpoints` in your config so the two can be diffed.
declare -A SUBDIRS=(
  [seagate8tb]="backups/applications backups/audio backups/docs backups/education backups/home backups/photos"
  [wd6tb]="backups/applications backups/audio backups/docs backups/education backups/home backups/photos"
  [seagate1tb]="backups/applications backups/audio backups/docs backups/education"
  [seagate2tb]="backups/docs backups/photos"
  [iomega1tb]="backups/docs backups/photos"
)

# Whole-disk path per drive, from the by-id listing above. Omitting a drive
# makes A3 skip it — that is how you provision one drive at a time, and how you
# leave an unattached drive for later.
declare -A DISK=(
  [seagate8tb]="/dev/disk/by-id/usb-Seagate_Backup+_Hub_BK_NA8TTE7F-0:0"
  [seagate2tb]="/dev/disk/by-id/usb-Seagate_BUP_Slim_BK_NA7S6FSH-0:0"
  [iomega1tb]="/dev/disk/by-id/usb-SAMSUNG_HM100UI_29DB20169FFF-0:0"
  [wd6tb]="/dev/disk/by-id/usb-WD_Elements_2620_575833324432354141565A55-0:0"

  # Not attached yet. To fill in: plug the drive in, run the by-id lookup
  # above, and copy the usb-<model>_<serial>-0:0 symlink whose target matches
  # the drive's new /dev/sdX.
  # [seagate1tb]="/dev/disk/by-id/usb-..."
)

# LUKS container UUIDs. Empty until A3's first loop creates the containers and
# prints them; its second loop and your nbkp config both need these.
declare -A LUKS_UUID=()
```

### A3. Partitions, LUKS containers and filesystems

> ⚠️ **Destructive.** `mklabel` discards the partition table, `luksFormat` wipes the partition, and `mkfs.btrfs` wipes the container. Make sure each disk is the one you mean and holds no data you need. This is the last destructive step; everything after it is configuration.

**Three names per drive, one per layer.** Each is set at a different step and shows up in a different `lsblk` column:

| Layer | Name | Set by | `lsblk` column | Visible when |
|---|---|---|---|---|
| GPT partition | `seagate8tb-part` | `parted mkpart` | `PARTLABEL` | always |
| LUKS2 header | `seagate8tb-luks` | `cryptsetup luksFormat --label` | `LABEL` | always |
| btrfs filesystem | `seagate8tb-btrfs` | `mkfs.btrfs -L` | `LABEL` | only once unlocked |

Once all three are set, the whole stack reads back at a glance:

```console
$ lsblk -o NAME,SIZE,PARTLABEL,FSTYPE,LABEL /dev/sda
NAME                  SIZE PARTLABEL        FSTYPE      LABEL
sda                   7.3T
└─sda1                7.3T seagate8tb-part  crypto_LUKS seagate8tb-luks
  └─seagate8tb-luks   7.3T                  btrfs       seagate8tb-btrfs
```

Note the cleartext device on the last row: udisks named it after the container's LUKS2 label, not `luks-<container-uuid>`. That is why nbkp discovers the mapper instead of deriving it — and why an `/etc/fstab` line, if you write one, has to reference the name `lsblk` actually reports.

Give the LUKS and btrfs names *different* strings. `blkid` reports a LUKS2 header label as `LABEL=` just like a filesystem label, so labelling both `seagate8tb` leaves the container partition and the cleartext device competing for the same `/dev/disk/by-label/seagate8tb` symlink — which one wins can change across unlock/lock cycles. The partition name is exempt: it lives in its own `/dev/disk/by-partlabel/` namespace and cannot collide with either, so the `-part` suffix is for symmetry rather than necessity.

nbkp needs none of the three — it addresses containers by UUID and discovers mountpoints at runtime. The `-btrfs` one buys you a readable mountpoint instead of a UUID; the other two just make `lsblk` answer "which disk is this?" without unlocking anything. Any `LABEL=` line in your own `/etc/fstab` or `/etc/crypttab`, though, is exactly what the collision above would break.

#### Partitions and LUKS containers

One GPT partition per disk, then a LUKS2 container inside it. The loop confirms each drive by name before touching it, and skips any drive absent from `DISK`:

```bash
#!/usr/bin/env bash
set -euo pipefail
source ./drive-config.sh

for drive in "${DRIVES[@]}"; do
  disk="${DISK[$drive]:-}"
  if [[ -z "$disk" ]]; then
    echo "== $drive: not in DISK — skipping"
    continue
  fi

  echo "== $drive -> $disk"
  lsblk -o NAME,SIZE,TYPE,FSTYPE,LABEL,MODEL,SERIAL "$disk"
  read -r -p "   ALL DATA on $disk will be destroyed. Type '$drive' to proceed: " ok
  if [[ "$ok" != "$drive" ]]; then
    echo "   skipped"
    continue
  fi

  # One GPT partition spanning the disk. 0%/100% keeps parted's 1 MiB alignment;
  # 8309 tags partition 1 "Linux LUKS".
  sudo parted -s "$disk" mklabel gpt
  sudo parted -s "$disk" mkpart "${drive}-part" 0% 100%
  sudo sgdisk -t 1:8309 "$disk"
  sudo udevadm settle
  # Resolve the partition rather than assuming ${disk}1 — nvme/mmc name it p1.
  part="/dev/$(lsblk -lnro NAME "$disk" | sed -n 2p)"

  # --label names the *container*. Prompts for the passphrase twice.
  sudo cryptsetup luksFormat --type luks2 --verify-passphrase \
    --label "${drive}-luks" "$part"

  # Printed ready to paste into drive-config.sh. The LUKS container UUID is the
  # only thing nbkp needs to find this drive (mount.device-uuid in the config).
  echo "   LUKS_UUID[$drive]=$(sudo cryptsetup luksUUID "$part")"
done
```

Paste each printed `LUKS_UUID[...]=...` line into `drive-config.sh` before running the second loop, and into your nbkp config as `mount.device-uuid`. The container UUID is the only identifier that follows a disk regardless of which port it's plugged into, and it's what guarantees nbkp/udisks mount the *correct* physical device.

Once every drive has been through A3, that stanza of `drive-config.sh` is complete — these are the values from [config-examples/personal-setup.yaml](config-examples/personal-setup.yaml):

```bash
declare -A LUKS_UUID=(
  [seagate8tb]=5941f273-f73c-44c5-a3ef-fae7248db1b6
  [wd6tb]=1467358c-d25f-44c7-a330-9cc6576075b7
  [seagate1tb]=256c3f2b-6f62-4213-b278-fae76c35b804
  [seagate2tb]=791586c3-5947-46bf-bb5e-fc1a35084a4b
  [iomega1tb]=ad5542e5-5365-4951-a1f2-fe81c4d6fe43
)
```

The name passed to `mkpart` is the **GPT partition name**, which is what `lsblk` shows in its `PARTLABEL` column. It's the outermost of the three names a drive ends up with, and the only one visible without even reading the LUKS header. Set it on an existing partition with `sudo parted "$disk" name 1 "${drive}-part"`.

> **Alternative — LUKS directly on the disk.** Skip the parted lines and run `luksFormat` on `"$disk"` itself. nbkp genuinely cannot tell the difference: it finds containers by UUID, and that resolves to a partition or a whole disk equally well, so nothing later in this tutorial changes. The partition is the default here for two reasons. A disk with no partition table reads as *blank* to Windows and macOS, which offer to "initialize" it — one click from overwriting the LUKS header; a GPT with an unrecognized partition type gets left alone. And the type tag makes the layout self-describing under `lsblk -o NAME,PARTTYPENAME`. If you already have whole-disk containers, there is nothing to fix — the two are equivalent as far as nbkp is concerned.

#### Filesystems and the sentinel layout

A second loop over the same `drive-config.sh`, now keyed by the UUIDs the first loop printed rather than by device node — so it needs no `/dev/sdX` at all, and a drive that isn't attached is skipped instead of failing:

```bash
#!/usr/bin/env bash
set -euo pipefail
source ./drive-config.sh

for drive in "${DRIVES[@]}"; do
  uuid="${LUKS_UUID[$drive]:-}"
  if [[ -z "$uuid" || ! -e "/dev/disk/by-uuid/$uuid" ]]; then
    echo "== $drive: no UUID recorded, or not attached — skipping"
    continue
  fi
  echo "== $drive"

  # The filesystem LABEL (-L) becomes the mountpoint:
  # /run/media/ubuntu/<drive>-btrfs. Distinct from the container's -luks label
  # above — see the note there.
  sudo cryptsetup open "UUID=$uuid" seed        # prompts for the passphrase
  sudo mkfs.btrfs -L "${drive}-btrfs" /dev/mapper/seed

  # Mount somewhere throwaway to seed the layout. Sentinels and dirs live
  # *inside* the filesystem, so this temporary mountpoint is irrelevant — they
  # end up wherever udisks mounts the drive at runtime.
  sudo mount /dev/mapper/seed /mnt
  sudo touch /mnt/.nbkp-vol                    # volume sentinel at the fs root

  # One destination endpoint per subdir. Each needs a staging subvolume + a
  # snapshots dir (btrfs snapshots) and .nbkp-dst. The relay is ALSO a source
  # for the downstream drives, so its paths additionally carry .nbkp-src.
  read -ra subdirs <<< "${SUBDIRS[$drive]}"
  for subdir in "${subdirs[@]}"; do
    path="/mnt/$subdir"
    sudo mkdir -p "$path/snapshots"
    sudo btrfs subvolume create "$path/staging"
    sudo touch "$path/.nbkp-dst"
    if [[ "$drive" == "$RELAY_DRIVE" ]]; then
      sudo touch "$path/.nbkp-src"
    fi
  done

  # Hand everything to the backup user, then unmount + lock.
  sudo chown -R ubuntu:ubuntu /mnt
  sudo umount /mnt
  sudo cryptsetup close seed
done
```

The sentinel rules the loop implements, in one place:

| Sentinel | Where | Meaning |
|---|---|---|
| `.nbkp-vol` | each volume root (`/run/media/ubuntu/<fs-label>`) | the drive is mounted here |
| `.nbkp-src` | each path read *from* | this source is ready |
| `.nbkp-dst` | each path written *to* | this destination is ready |

Plus, per snapshot destination: a `staging` subvolume + `snapshots/` dir for **btrfs**, or just a `snapshots/` dir for **hard-link** — which is why the loop creates both for every subdir. Details and the symlink lifecycle: [Snapshot Lifecycle](./internals.md#snapshot-lifecycle).

Read back what was created. Unlike the two loops above this one is **read-only** and needs no device nodes, so it's the one to re-run whenever you want to confirm a drive is still seeded correctly — including long after setup:

```bash
#!/usr/bin/env bash
set -euo pipefail
source ./drive-config.sh

for drive in "${DRIVES[@]}"; do
  uuid="${LUKS_UUID[$drive]:-}"
  if [[ -z "$uuid" || ! -e "/dev/disk/by-uuid/$uuid" ]]; then
    echo "== $drive: no UUID recorded, or not attached"
    continue
  fi
  sudo cryptsetup open "UUID=$uuid" check
  sudo mount /dev/mapper/check /mnt
  echo "== $drive  (fs label: $(lsblk -dnro LABEL /dev/mapper/check))"
  find /mnt -maxdepth 3 \( -name '.nbkp-*' -o -name snapshots -o -name staging \) \
    | sort | sed 's|^/mnt|  |'
  sudo umount /mnt
  sudo cryptsetup close check
done
```

Once the config exists (Part B), `nbkp preflight troubleshoot` checks the same things against it and names what to fix — this loop is for the window before that, when there is no config to check against yet.

The laptop's local volumes and SSDs need the same sentinels, but they are not LUKS containers and need no loop — see [B5](#b5-sentinels-on-the-local-volumes).

### A4. Configure udisks

This tutorial keeps the system side minimal: **no crypttab, no fstab.** udisks unlocks each LUKS container, names the cleartext device after the container's LUKS2 label (`/dev/mapper/seagate8tb-luks`, or `/dev/mapper/luks-<uuid>` for an unlabelled header), and mounts the filesystem at `/run/media/ubuntu/<fs-label>` — a path nbkp discovers at runtime. There's no per-drive system config to maintain.

Two pieces to set up, both one-time and neither per-drive: a mount option udisks must be willing to apply, and the authorization that lets nbkp call udisks at all.

#### Mount options for btrfs

nbkp prunes old btrfs snapshots, which needs the `user_subvol_rm_allowed` mount option — and nbkp never injects mount options itself (udisks rejects anything not on its allowlist). With no fstab line to carry it, you allow it through udisks once, for all btrfs volumes:

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

> **Scoping it to specific drives.** `[defaults]` grants the option to *every* btrfs filesystem udisks mounts on the host, including an unrelated stick plugged in later. To grant it per drive instead, use a device section keyed on the **cleartext** device — `[/dev/disk/by-uuid/<fs-uuid>]`, the UUID of the btrfs filesystem *inside* the container, not the LUKS container UUID from A3. nbkp is indifferent to which you choose: its check reads the live option string from `findmnt`, so it detects `user_subvol_rm_allowed` however it was granted — fstab, `[defaults]`, or a device section. The tutorial defaults to global because per-device means one more UUID to record per drive, re-collected whenever a filesystem is recreated, and because every btrfs volume here needs the option anyway. On a multi-user host, scope it. See `man udisks2.conf`.

> **Advanced — fixed `/mnt/<drive>` paths via crypttab + fstab.** If you'd rather pin each drive to a stable path (for other tools, or to avoid `mount_options.conf`), set up `/etc/crypttab` (friendly mapper names) and `/etc/fstab` (carrying `user_subvol_rm_allowed`), and set `path:` on each volume. nbkp supports this fully, but it's more moving parts and the crypttab/fstab correctness is **on you** — nbkp's help for it is limited (it flags a `path` that no fstab entry maps, and little else). The worked examples live in [usage.md → Mount management](./usage.md#mount-management-with-udisks2).

#### The polkit rule

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

The backup hub is now ready: each drive is formatted, seeded, and locked, and nbkp owns the unlock → mount → sync → umount → lock lifecycle from here on.

---

## Part B — Set up the laptop (the orchestrator)

### B1. Install nbkp

The `keyring` extra pulls in the macOS Keychain backend for passphrases:

```bash
brew install uv
uv tool install 'nbkp[keyring]'
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

nbkp's default credential provider is `keyring`. The `nbkp[keyring]` extra from B1 gives *nbkp* access to the Keychain, but it does not put the **`keyring` command** on your PATH — only the entry points of the package you installed are exposed, not its dependencies'. Install the CLI as its own app:

```bash
uv tool install keyring
keyring --version
```

Then store one entry per `passphrase-id` in your config (the service name is always `nbkp`):

```bash
keyring set nbkp seagate8tb        # prompts; stored encrypted in the macOS Keychain
keyring set nbkp seagate1tb
keyring set nbkp seagate2tb
keyring set nbkp wd6tb
keyring set nbkp iomega1tb
```

Both apps read the same macOS Keychain, so it doesn't matter that they live in separate tool environments. Once the config exists (B4), check that nbkp can retrieve every one of them:

```bash
nbkp credentials keyring-status     # one row per passphrase-id, found or missing
```

Passphrases never live in the config. Other providers (`prompt`, `env`, `command`) are described in [concepts.md](./concepts.md#encryption--luks-encryption-config).

### B4. Write the config

Everything in this tutorial is already written down in
[config-examples/personal-setup.yaml](config-examples/personal-setup.yaml) — the config
this walkthrough mirrors: both SSH endpoints, the five hub drives, the dual-role SSDs,
every sync endpoint, and the relay fan-out. Start from it rather than from a blank file:

```bash
mkdir -p ~/.config/nbkp
curl -fsSL -o ~/.config/nbkp/config.yaml \
  https://raw.githubusercontent.com/iglootools/nbkp/main/docs/config-examples/personal-setup.yaml
```

Then adapt it to your setup — at minimum:

| Change | To |
|---|---|
| `ssh-endpoints` hosts | your Pi's LAN address and, if you want the `travel` endpoint, its public hostname |
| `mount.device-uuid` | the LUKS container UUIDs A3 printed |
| `passphrase-id` | the Keychain entries you created in B3 |
| volume `path` values | where your laptop's own volumes actually live |
| drive names, `subdir`s, and syncs | your own drives and categories — delete the ones you don't have |

Check it parses and says what you meant before going further:

```bash
nbkp config show                    # the config as nbkp understands it
nbkp credentials keyring-status     # every passphrase-id resolvable
```

A few things worth knowing, each with a reference for the full story:

- **The cleartext device is discovered, not declared.** udisks names it after the LUKS2 header label, or `luks-<container-uuid>` for an unlabelled header, or a crypttab name if you added one — and nbkp finds whichever at runtime, so the config needs only the LUKS container UUID and the passphrase id. See [Volume Mount Management](./internals.md#volume-mount-management).
- **No `path`.** Omitting it selects the discovered `/run/media/<user>/<label>` model used throughout this tutorial. Set `path` only if you went the fixed-path (fstab) route in A4's advanced note on crypttab + fstab — see the [fstab × crypttab matrix](./usage.md#mount-point-models-fstab--crypttab).
- **Snapshots are per-endpoint.** btrfs on the hub's drives, hard-link on the local SSDs. Add `max-snapshots` to prune automatically.
- **Filters** trim what each sync copies — `dir-merge: .rsync-filter` reads per-directory rule files; you can also inline `+`/`-` rules. See [Filters](./concepts.md#filters).
- **Chained syncs sort themselves.** When one sync's destination is another's source (the `seagate8tb` relay), nbkp orders them automatically and cancels downstream syncs if an upstream one fails. See [Sync Dependencies](./internals.md#sync-dependencies-and-execution-order).

### B5. Sentinels on the local volumes

The hub's drives got their sentinels in Part A. Do the same for the laptop's local volumes and SSDs — `.nbkp-vol` at each root, `.nbkp-src`/`.nbkp-dst` per endpoint, and a `snapshots/` dir for hard-link destinations:

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

Inactive syncs (a drive that isn't plugged in, a hub that's unreachable) are **skipped**, not errors — that's the nomadic design. Tighten that with `--strictness` when you expect everything to be present; see [Strictness](./internals.md#strictness).

---

## Where to go next

- [usage.md](./usage.md) — every command, more examples, and the full udisks mount-management reference
- [concepts.md](./concepts.md) — the complete configuration reference (every field)
- [internals.md](./internals.md) — what nbkp does at runtime and why; the external commands it invokes
- [features.md](./features.md) — the full feature list
