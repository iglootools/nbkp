# AGENTS.md

nbkp (Nomad Backup) is an rsync-based backup CLI for sources and destinations
that are not always available: laptops, removable drives, home servers. Python,
uv, Typer; tasks run through mise.

## Commands

- `mise run preflight-quick`: every lint and the unit tests, before each
  commit.
- `mise run preflight`: every lint and test CI runs, every few commits and
  before review.
- `mise run fmt` after editing any file.
- `mise tasks` lists the rest.
- An agent has no mise-activated shell, so run a mise task when one does the
  job, `uv run --no-sync <tool>` for a tool in the project's environment, and
  `mise exec -- <tool>` for one mise installs
  ([why](https://github.com/iglootools/common/blob/main/guidelines/project-setup/python-tooling.md#invoke-tools-through-uv-run---no-sync-tool-in-mise-tasks-not-bare)).
- `mise run reference-doc` regenerates the generated references (the CLI
  reference, the config tables, the Module Overview and the task reference), and
  `mise run toc` the tables of contents.

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
