# Setup Development Environment

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