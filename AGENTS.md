# AGENTS.md

nbkp (Nomad Backup) is an rsync-based backup CLI for sources and destinations
that are not always available: laptops, removable drives, home servers. Python,
uv, Typer; tasks run through mise.

## Commands

- `mise run preflight-quick`: every lint and the fast tests (no Docker suites),
  before each commit.
- `mise run preflight`: every lint and test CI runs, every few commits and
  before review.
- `mise tasks` lists the rest.
- Run a mise task when one does the job, and `uv run --no-sync <tool>`
  otherwise, never a bare tool.
- Generated documents are regenerated, never edited by hand:
  `mise run reference-doc` rewrites the CLI reference, the config tables and the
  Module Overview, and `mise run toc` the tables of contents. `lint` fails when
  one is stale.

## Guidelines

The shared iglootools guidelines govern every change. Claude Code loads them
through the `iglootools` plugin. Other agents: read `coding/general.md`,
`coding/python.md` and `coding/git.md` before any change, and the guideline
whose trigger matches the files you are changing, using
[iglootools/common's index](https://github.com/iglootools/common/blob/main/guidelines/README.md).
Read [docs/guidelines.md](docs/guidelines.md) before any change too: it holds
nbkp's deviations and its own rules, and links to the project's other guidelines
files.

## Where things are

[CONTRIBUTING.md](CONTRIBUTING.md) indexes every document, from setup to the
domain and the architecture. Read a document when the task reaches it, not up
front.
