# Setup Development Environment

## System Setup

1. [Install and activate mise](https://mise.jdx.dev/installing-mise.html)

2. Install Docker Desktop (or Docker Engine on Linux)

3. Configure github CLI with `gh auth login` and ensure you have access to the repository (optional, for convenience).

4. Install the graphviz dependency
   - macOS: `brew install graphviz`
   - Linux: `apt install graphviz`

5. Activate the virtual environment:
   ```bash
   # - Install all the tools defined in mise.toml (python, uv, ...)
   # - Create .venv on the pinned Python and activate it
   mise install

   # Usually unnecessary: [deps.uv] in mise.toml runs `uv sync --all-extras` automatically
   # before any `mise run` when uv.lock or pyproject.toml changed, or .venv is missing.
   mise run install

   # To recreate the virtualenv from scratch
   mise run reinstall
   ```

   Add and remove dependencies with `uv add` / `uv remove`, which update `pyproject.toml`
   and `uv.lock` together. (`mise deps add` does not support uv.)

   Run tools with `uv run <tool>` rather than bare — see
   [Building and Testing](building-and-testing.md) for why that matters when more than one
   project's virtualenv is in play.

6. Upgrade rsync if you're on macOS, as the default version is too old and lacks some features nbkp relies on:
   ```bash
   brew install rsync
   ```

7. (Optional) If you want to record the demo, install pv:
   ```bash
   brew install pv
   ```

## Editor and Claude Code Setup

The repository is already configured; what is left is per-machine installation.

- **Extensions** — accept the prompt on first open; the set is committed in
  [.vscode/extensions.json](../.vscode/extensions.json). [What each one is for, and which
  committed settings silently stop working without it][vscode].
- **Interpreter** — nothing to select. `.vscode/settings.json` and `[tool.pyright]` already
  point at `.venv`. [What those settings do, and why a reload is needed after changing
  them][pyright].
- **Plugins** — `.claude/settings.json` enables `iglootools` for the shared guidelines and
  `pyright-lsp` so Claude resolves symbols instead of grepping for them. A plugin the project
  enables still installs once per machine: [install per project][install], then
  [update one][update-one] or [all at once][update-all] on each release. For `pyright-lsp` see
  [Claude Code setup][claude-code] — it needs the virtualenv step above done first.

[vscode]: https://github.com/iglootools/common/blob/main/guidelines/project-setup/ide.md#vscode
[pyright]: https://github.com/iglootools/common/blob/main/guidelines/project-setup/ide.md#pyright-environment-resolution
[claude-code]: https://github.com/iglootools/common/blob/main/guidelines/project-setup/claude-code.md
[install]: https://github.com/iglootools/common#install-it-per-project-not-per-user
[update-one]: https://github.com/iglootools/common#updating-one-project
[update-all]: https://github.com/iglootools/common#updating-every-project
