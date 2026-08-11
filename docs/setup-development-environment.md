# Setup Development Environment

## System Setup

1. Clone the [common-guidelines](https://github.com/iglootools/common-guidelines) repo as a sibling directory:
   ```bash
   # From the parent directory of nbkp (e.g. iglootools/)
   git clone git@github.com:iglootools/common-guidelines.git
   ```
   This is required for Claude Code to load shared coding guidelines via `@` imports in `CLAUDE.md`.

2. [Install and activate mise](https://mise.jdx.dev/installing-mise.html)

3. Install Docker Desktop (or Docker Engine on Linux)

4. Configure github CLI with `gh auth login` and ensure you have access to the repository (optional, for convenience).

5. Install the graphviz dependency
   - macOS: `brew install graphviz`
   - Linux: `apt install graphviz`

6. Activate the virtual environment:
   ```bash
   # - Install all the tools defined in mise.toml
   # - Set up the .venv with the correct Python version
   mise install

   # vscode and poetry should automatically detect and use the .venv created by mise
   poetry install

   # To recreate the virtualenv from scratch:
   poetry env remove --all
   ```

7. Upgrade rsync if you're on macOS, as the default version is too old and lacks some features nbkp relies on:
   ```bash
   brew install rsync
   ```

8. (Optional) If you want to record the demo, install pv:
   ```bash
   brew install pv
   ```

## VSCode Setup

Accept the recommended extensions when VSCode prompts on first open — the set is committed in
`.vscode/extensions.json`. See
[VSCode setup](https://github.com/iglootools/common-guidelines/blob/main/tooling.md#vscode) in the
shared guidelines for what each one is for, and which committed settings silently stop working
without it.

No interpreter selection is needed: `.vscode/settings.json` and `[tool.pyright]` in `pyproject.toml`
already point the editor at the `.venv` created by Poetry. See
[Pyright environment resolution](https://github.com/iglootools/common-guidelines/blob/main/tooling.md#pyright-environment-resolution)
in the shared guidelines for what those settings do, how to verify them, and why a window reload is
required after changing them.

## Claude Code Setup

Install the Pyright LSP plugin so Claude resolves symbols instead of grepping for them. Follow
[Claude Code setup in the shared guidelines](https://github.com/iglootools/common-guidelines/blob/main/tooling.md#claude-code)
once the virtualenv step above is done — it covers the install command, why `pyright-langserver` has
to come from this project's `.venv`, and how to verify that it does.
