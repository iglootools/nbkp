# CLI Conventions

What nbkp's commands and options must respect, and the commands that are
exceptions. Read it before changing `nbkp/cli/`, any `nbkp/*/cli/`,
`nbkp/cliwiring/` or `nbkp/clidisplay/`, and before adding a command or an
option anywhere. The shared
[general.md › Command Line](https://github.com/iglootools/common/blob/main/guidelines/coding/general.md#command-line)
rules apply too, among them
[Expand `~` in every path a user writes](https://github.com/iglootools/common/blob/main/guidelines/coding/general.md#expand--in-every-path-a-user-writes);
the Typer layout and names are
[python.md › Naming](https://github.com/iglootools/common/blob/main/guidelines/coding/python.md#naming)'s.

<!-- toc -->
**On this page**

- [Options](#options)
  - [Give every command human-readable and JSON output, human by default](#give-every-command-human-readable-and-json-output-human-by-default)
  - [Accept a config file on every command](#accept-a-config-file-on-every-command)
  - [Give every data-mutating or long-running operation a dry run](#give-every-data-mutating-or-long-running-operation-a-dry-run)
  - [Declare each shared option once](#declare-each-shared-option-once)
- [Commands that are exceptions](#commands-that-are-exceptions)
  - [Exception — `demo` takes neither `--output` nor `--config`](#exception--demo-takes-neither---output-nor---config)
<!-- tocstop -->

## Options

### Give every command human-readable and JSON output, human by default

Every command takes `--output human|json` (`-o`), defaulting to `human`.

**Why:** a person reads the human report, and a script parses the JSON one
without scraping it.

#### Exception — `sh`

`sh` has no JSON mode, and its `-o` means `--output-file` (where to write the
script) rather than the `--output` format selector.

**Why:** its output is a bash script, not a report. Renaming the flag would
break existing invocations and cron entries for no gain in function.

- **Retire it** if `sh` gains a structured report worth emitting (e.g. the list
  of generated syncs), at which point it takes `--output human|json` and the
  script path moves to a long-only `--output-file` in a major release.

### Accept a config file on every command

Every command takes `--config`.

**Why:** a user keeps more than one configuration, and a test points a command
at its own.

### Give every data-mutating or long-running operation a dry run

Such a command takes `--dry-run`, with `-n` as its short flag.

**Why:** the user sees what a backup, a prune or a mount would do before it does
it.

#### Exception — `-n` in `disks`

In `disks mount|umount|status`, `-n` is `--name`, so their `--dry-run` is
long-only.

**Why:** `-n` meant `--name` there first; swapping it would turn existing
`disks mount -n <volume>` invocations into silent dry runs.

- **Retire it** in the next major release by moving `--name` to a long-only flag
  (or `-V`) and giving `-n` to `--dry-run` everywhere.

### Declare each shared option once

An option several commands share is the `<NAME>_OPTION` constant
[python.md](https://github.com/iglootools/common/blob/main/guidelines/coding/python.md#start-a-functions-name-with-the-verb-that-says-what-the-caller-gets)
names: in `cliwiring/options.py`, or in the sub-app's `cli/options.py` when only
that sub-app shares it.

**Why:** one declaration keeps the flag, its short form and its help the same in
every command.

#### Exception — options whose help is per command

`--strictness` in `run`, `preflight check` and `preflight troubleshoot`, and the
long-only `--dry-run` of `disks mount` and `disks umount` and the one of
`demo seed`, are written inline in each command rather than as a shared
`<NAME>_OPTION` constant.

**Why:** the flag is shared but what it does is not: each help text says what
that command does at each strictness level, or what a dry run skips there, and
one constant would carry one wording for all of them.

- **Retire it** for a flag once its commands behave alike, or if Typer lets a
  shared option take per-command help.

## Commands that are exceptions

### Exception — `demo` takes neither `--output` nor `--config`

`demo seed` and `demo output` take neither option, against
[Give every command human-readable and JSON output](#give-every-command-human-readable-and-json-output-human-by-default)
and
[Accept a config file on every command](#accept-a-config-file-on-every-command).

**Why:** they are manual-QA helpers, not backup commands. `seed` *writes* a
config (to `<base-dir>/config.yaml`) rather than reading one, and `output`
renders the human formatters with built-in fake data, so human output is its
whole purpose and there is no config for it to load. `seed`'s report is a
summary for the person running the demo, and the one scripted caller,
`demo/demo.sh`, already knows where the config lands because it passes
`--base-dir`.

- **Retire it**, by giving `seed` `--output json` (the base directory, config
  path and container ports), once a script or workflow has to read what `seed`
  created; drop it if `demo` leaves the user-facing `nbkp` app for a
  development-only entry point, where these rules no longer apply.
