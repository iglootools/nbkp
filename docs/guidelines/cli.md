# CLI Conventions

- **CLI**
  - *Exception — options whose help is per command:* `--strictness` in `run`,
    `preflight check` and `preflight troubleshoot`, and the long-only
    `--dry-run` of `disks mount` and `disks umount` and the one of `demo seed`,
    are written inline in each command rather than as a shared `<NAME>_OPTION`
    constant. The flag is shared but what it does is not: each help text says
    what that command does at each strictness level, or what a dry run skips
    there, and one constant would carry one wording for all of them. The other
    shared options live in `cliwiring/options.py`, and those shared within one
    sub-app in its `cli/options.py`. Retire this exception for a flag once its
    commands behave alike, or if Typer lets a shared option take per-command
    help.
  - Use `typer` for CLI implementation (argument parsing, formatting, etc.)
  - Provide both human-readable and JSON output formats for all commands, with
    human-readable as the default.
    - *Exception — `sh`:* its output is a bash script, not a report, so there is
      no JSON mode, and `-o` means `--output-file` (where to write the script)
      rather than the `--output` format selector other commands use. Renaming
      the flag would break existing invocations and cron entries for no gain in
      function. Retire this exception if `sh` gains a structured report worth
      emitting (e.g. the list of generated syncs), at which point it takes
      `--output human|json` and the script path moves to a long-only
      `--output-file` in a major release.
    - *Exception — `-n` in `disks`:* in `run` and `snapshots prune` (and
      `demo seed`) `-n` is `--dry-run`, but in `disks mount|umount|status` it is
      `--name`, so their `--dry-run` is long-only. `-n` meant `--name` there
      first; swapping it would turn existing `disks mount -n <volume>`
      invocations into silent dry runs. Retire this exception in the next major
      release by moving `--name` to a long-only flag (or `-V`) and giving `-n`
      to `--dry-run` everywhere.
    - *Exception — `demo`:* `demo seed` and `demo output` take neither
      `--output` nor `--config`. They are manual-QA helpers, not backup
      commands: `seed` *writes* a config (to `<base-dir>/config.yaml`) rather
      than reading one, and `output` renders the human formatters with built-in
      fake data, so human output is its whole purpose and there is no config for
      it to load. `seed`'s report is a summary for the person running the demo,
      and the one scripted caller, `demo/demo.sh`, already knows where the
      config lands because it passes `--base-dir`. Retire this exception, by
      giving `seed` `--output json` (the base directory, config path and
      container ports), once a script or workflow has to read what `seed`
      created; drop it if `demo` leaves the user-facing `nbkp` app for a
      development-only entry point, where these rules no longer apply.
  - Provide ability to pass a config file to all commands (except `demo`, see
    its exception above)
  - Provide a dry-run parameter for all data-mutating or long-running operations
