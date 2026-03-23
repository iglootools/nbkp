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

Install the following extensions:

- [Ruff](https://marketplace.visualstudio.com/items?itemName=charliermarsh.ruff) — formatting and linting (format-on-save is enabled in `.vscode/settings.json`)
- [Pylance](https://marketplace.visualstudio.com/items?itemName=ms-python.pylance) — type checking (uses pyright, configured via `[tool.pyright]` in `pyproject.toml`)
- [Tombi](https://marketplace.visualstudio.com/items?itemName=nicfit.tombi) — TOML formatting and validation

Select the Python interpreter from the `.venv` created by Poetry:

1. Open the Command Palette (`Cmd+Shift+P`)
2. Run **Python: Select Interpreter**
3. Choose the `.venv` entry (e.g., `./.venv/bin/python`)
