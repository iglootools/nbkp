# Installation

## System Requirements

- Python 3.12+
- rsync 3.0+ (macOS ships with openrsync which is not supported — install GNU rsync via `brew install rsync`)
- `btrfs-progs` (only if using btrfs snapshots)

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
