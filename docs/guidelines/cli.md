# CLI Conventions

What nbkp's commands and options must respect beyond the shared
[general.md › Command Line](https://github.com/iglootools/common/blob/main/guidelines/coding/general.md#command-line)
rules, the helper modules that implement them, and the commands that are
exceptions. Read it before changing `nbkp/cli/`, any `nbkp/*/cli/`,
`nbkp/cliwiring/` or `nbkp/theme/`, and before adding a command or an option
anywhere. The Typer layout and names are
[python.md › Naming](https://github.com/iglootools/common/blob/main/guidelines/coding/python.md#naming)'s.

<!-- toc -->
**On this page**

- [Options](#options)
  - [Give every command JSON output](#give-every-command-json-output)
  - [Accept a config file on every command](#accept-a-config-file-on-every-command)
  - [Label a dry run through `nbkp/theme/dryrun.py`](#label-a-dry-run-through-nbkpthemedryrunpy)
  - [Declare each shared option once](#declare-each-shared-option-once)
- [Commands that are exceptions](#commands-that-are-exceptions)
  - [Exception — `demo` takes neither `--output` nor `--config`](#exception--demo-takes-neither---output-nor---config)
<!-- tocstop -->

## Options

### Give every command JSON output

Every command takes the `-o/--output human|json` that
[general.md › Select the output format with `-o/--output`, and write a file with `-f/--file`](https://github.com/iglootools/common/blob/main/guidelines/coding/general.md#select-the-output-format-with--o--output-and-write-a-file-with--f--file)
describes, not only the commands that print a report.

**Why:** a script parses any command's result without scraping the human
report.

#### Exception — `sh` writes its script with `-o/--output-file`

Overrides
[general.md › Select the output format with `-o/--output`, and write a file with `-f/--file`](https://github.com/iglootools/common/blob/main/guidelines/coding/general.md#select-the-output-format-with--o--output-and-write-a-file-with--f--file):
`sh` has no JSON mode, and its `-o` is `--output-file`, where to write the
script, rather than the format selector or `-f/--file`.

**Why:** its output is a bash script, not a report. Renaming the flag would
break existing invocations and cron entries for no gain in function.

**Retires when:** nbkp's next major release moves the script path to
`-f/--file`, leaving `-o/--output` to select a format if `sh` gains a
structured report (such as the list of generated syncs).

### Accept a config file on every command

Every command takes `--config`.

**Why:** a user keeps more than one configuration, and a test points a command
at its own.

### Label a dry run through `nbkp/theme/dryrun.py`

Every command that changes data or runs long takes the `--dry-run`/`-n` that
[general.md › Offer `--dry-run` on every command that changes data or runs long](https://github.com/iglootools/common/blob/main/guidelines/coding/general.md#offer---dry-run-on-every-command-that-changes-data-or-runs-long)
describes, and prints its `[dry-run]` label through `dry_run_labeled` and
`dry_run_table_title` in `nbkp/theme/dryrun.py`. They build the label as
`rich.text.Text`, as
[Wrap text nbkp did not author in `rich.text.Text`](../guidelines.md#wrap-text-nbkp-did-not-author-in-richtexttext)
explains.

**Why:** one label built in one place reads the same in every command, on
screen, in a log or in a script.

- **A command whose JSON is a list** prints an object in a dry run, the list
  under a key (`{"dry_run": true, "results": [...]}`), as `snapshots prune`
  does.
- **Another tool's output**, such as rsync's lines under `rsync --dry-run`, is
  left as that tool printed it; the label goes on nbkp's own title.

#### Exception — the label in the script `sh` generates

Overrides
[general.md › Offer `--dry-run` on every command that changes data or runs long](https://github.com/iglootools/common/blob/main/guidelines/coding/general.md#offer---dry-run-on-every-command-that-changes-data-or-runs-long):
the generated script's log lines in a dry run start with `[nbkp] [dry-run]`,
not with the label.

**Why:** every line the script logs starts with `[nbkp]`, which tells its lines
apart from rsync's on the same stderr, and a log filter or cron mail rule may
match on it.

**Retires when:** the script drops its `[nbkp]` prefix.

### Declare each shared option once

An option several commands share is the `<NAME>_OPTION` constant
[python.md](https://github.com/iglootools/common/blob/main/guidelines/coding/python.md#start-a-functions-name-with-the-verb-that-says-what-the-caller-gets)
names: in `cliwiring/options.py`, or in the sub-app's `cli/options.py` when only
that sub-app shares it.

**Why:** one declaration keeps the flag, its short form and its help the same in
every command.

#### Exception — options whose help is per command

Overrides
[Declare each shared option once](#declare-each-shared-option-once):
`--strictness` in `run` (`preflight check` and `preflight troubleshoot` share
one `STRICTNESS_OPTION`), and the long-only `--dry-run` of `disks mount` and
`disks umount` and the one of `demo seed`, are written inline in each command
rather than as a shared `<NAME>_OPTION` constant.

**Why:** the flag is shared but what it does is not: each help text says what
that command does at each strictness level, or what a dry run skips there, and
one constant would carry one wording for all of them.

**Retires when:** for a flag, once its commands behave alike; for every flag,
if Typer lets a shared option take per-command help.

## Commands that are exceptions

### Exception — `demo` takes neither `--output` nor `--config`

Overrides [Give every command JSON output](#give-every-command-json-output) and
[Accept a config file on every command](#accept-a-config-file-on-every-command):
`demo seed` and `demo output` take neither option.

**Why:** they are manual-QA helpers, not backup commands. `seed` *writes* a
config (to `<base-dir>/config.yaml`) rather than reading one, and `output`
renders the human formatters with built-in fake data, so human output is its
whole purpose and there is no config for it to load. `seed`'s report is a
summary for the person running the demo, and the one scripted caller,
`demo/demo.sh`, already knows where the config lands because it passes
`--base-dir`.

**Retires when:** a script or workflow has to read what `seed` created, by
giving `seed` `--output json` (the base directory, config path and container
ports); or `demo` leaves the user-facing `nbkp` app for a development-only entry
point, where these rules no longer apply.
