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
   # - Install all the tools defined in mise.toml
   # - Set up the .venv with the correct Python version
   mise install

   # vscode and poetry should automatically detect and use the .venv created by mise
   poetry install

   # To recreate the virtualenv from scratch:
   poetry env remove --all
   ```

6. Upgrade rsync if you're on macOS, as the default version is too old and lacks some features nbkp relies on:
   ```bash
   brew install rsync
   ```

7. (Optional) If you want to record the demo, install pv:
   ```bash
   brew install pv
   ```

## VSCode Setup

Install the following extensions:

- [Ruff](https://marketplace.visualstudio.com/items?itemName=charliermarsh.ruff) — formatting and linting (format-on-save is enabled in `.vscode/settings.json`)
- [Pylance](https://marketplace.visualstudio.com/items?itemName=ms-python.pylance) — type checking (uses pyright, configured via `[tool.pyright]` in `pyproject.toml`)

Select the Python interpreter from the `.venv` created by Poetry:

1. Open the Command Palette (`Cmd+Shift+P`)
2. Run **Python: Select Interpreter**
3. Choose the `.venv` entry (e.g., `./.venv/bin/python`)
