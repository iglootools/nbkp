# AGENTS.md

nbkp (Nomad Backup) is an rsync-based backup CLI for sources and destinations that are not always
available: laptops, removable drives, home servers. Python, uv, Typer; tasks run through mise.

## Commands

- `mise run check-all`: every check and test CI runs. `mise run check` is the static checks
  alone, `mise run test-unit` the fast tests; `mise tasks` lists the rest.
- Run tools through `uv run --no-sync <tool>`, never bare, so another project's virtualenv on
  `PATH` cannot stand in for this one.
- Generated documents are regenerated, never edited by hand: `mise run reference-doc` rewrites
  the CLI reference, the config tables and the Module Overview, and `mise run toc` the tables of
  contents. `check` fails when one is stale.

## Guidelines

The shared iglootools guidelines govern every change. Claude Code gets them from the `iglootools`
plugin; other agents read them at https://github.com/iglootools/common/tree/main/guidelines,
starting from its README. Read [docs/guidelines.md](docs/guidelines.md) before any change: it
records where nbkp deviates from them, its own rules, and the checklists for specific changes.

## Where things are

Read a document when the task reaches it, not up front.
[CONTRIBUTING.md](CONTRIBUTING.md) indexes all of them.

- [docs/domain.md](docs/domain.md): volumes, endpoints, syncs, snapshots, the backup graph and
  availability. Read before changing behaviour.
- [docs/architecture.md](docs/architecture.md): the modules and the layering rules
  import-linter enforces. Read before moving code or adding a module.
- [docs/internals.md](docs/internals.md): runtime behaviour, design decisions, and the external
  commands nbkp runs.
- [docs/building-and-testing.md](docs/building-and-testing.md): the test suites (unit,
  filesystem, Docker, end-to-end), what each needs, and the checks CI runs.
- [docs/config-reference.md](docs/config-reference.md) and
  [docs/cli-reference.md](docs/cli-reference.md): every config field and every command, both
  generated.
- [docs/releasing-and-publishing.md](docs/releasing-and-publishing.md): cutting a release.
