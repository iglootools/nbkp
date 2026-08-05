# Installation

## System Requirements

- Python 3.12–3.14
- rsync 3.0+ (macOS ships with openrsync which is not supported — install GNU rsync via `brew install rsync`)
- `btrfs-progs` (only if using btrfs snapshots)

The 3.12 floor tracks Ubuntu 24.04 LTS, whose system `python3` is 3.12 — so nbkp
installs on the current LTS without a PPA or a source build. Ubuntu 26.04 LTS ships
3.14, which is also supported. See the
[Python Version Policy](https://github.com/iglootools/common-guidelines/blob/main/python.md#python-version-policy)
for how both versions are maintained.

## Install with pipx

[pipx](https://pipx.pypa.io/) installs CLI tools in isolated environments, keeping your system Python clean:

```bash
pipx install nbkp
```

### Optional Extras

nbkp ships with optional extras that pull in additional dependencies:

| Extra | Pulls in | When you need it |
|---|---|---|
| `keyring` | `keyring` | Default `credential-provider: keyring` (LUKS passphrases from macOS Keychain / Linux SecretService). Not needed for `prompt`, `env`, or `command` providers. |
| `docker` | `docker` | `nbkp demo seed --docker` for manual testing against a Docker container. |

Install with a single extra:

```bash
pipx install 'nbkp[keyring]'
```

Install with all extras:

```bash
pipx install 'nbkp[keyring,docker]'
```

Add an extra to an existing install without reinstalling:

```bash
pipx inject nbkp keyring
```

Note that the `keyring` extra gives *nbkp* access to the Keychain / SecretService, but
does not put the `keyring` **command** on your PATH — pipx exposes only the entry
points of the package you installed. Storing a passphrase with `keyring set nbkp <id>`
needs the CLI as well, as its own app:

```bash
pipx install keyring
```

Both share the same OS credential store, so it does not matter that they live in
separate pipx venvs.

To upgrade to the latest version (extras are preserved):

```bash
pipx upgrade nbkp
```

## Shell Completion

nbkp supports tab completion for Bash, Zsh, Fish, and PowerShell.

Install completion for your current shell:

```bash
nbkp --install-completion
```

Or target a specific shell:

```bash
nbkp --install-completion bash
nbkp --install-completion zsh
nbkp --install-completion fish
nbkp --install-completion powershell
```

To preview the completion script without installing it:

```bash
nbkp --show-completion
```

Restart your shell (or source the relevant config file) for completions to take effect.
