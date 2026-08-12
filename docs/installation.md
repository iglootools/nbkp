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

## Install with uv

[uv](https://docs.astral.sh/uv/) installs CLI tools in isolated environments, keeping your system Python clean:

```bash
uv tool install nbkp
```

[pipx](https://pipx.pypa.io/) does the same thing and is a fine alternative if you already
have it — substitute `pipx install` for `uv tool install` throughout this page, and
`pipx upgrade` for `uv tool upgrade`. The one command that differs in name is noted below.

### Optional Extras

nbkp ships with optional extras that pull in additional dependencies:

| Extra | Pulls in | When you need it |
|---|---|---|
| `keyring` | `keyring` | Default `credential-provider: keyring` (LUKS passphrases from macOS Keychain / Linux SecretService). Not needed for `prompt`, `env`, or `command` providers. |
| `docker` | `docker` | `nbkp demo seed --docker` for manual testing against a Docker container. |

Install with a single extra:

```bash
uv tool install 'nbkp[keyring]'
```

Install with all extras:

```bash
uv tool install 'nbkp[keyring,docker]'
```

Add a package to an existing install without reinstalling (`pipx inject nbkp keyring`):

```bash
uv tool install nbkp --with keyring
```

Note that the `keyring` extra gives *nbkp* access to the Keychain / SecretService, but
does not put the `keyring` **command** on your PATH — only the entry points of the package
you installed are exposed, not its dependencies'. Storing a passphrase with
`keyring set nbkp <id>` needs the CLI as well, as its own app:

```bash
uv tool install keyring
```

Both share the same OS credential store, so it does not matter that they live in
separate tool environments.

To upgrade to the latest version (extras are preserved):

```bash
uv tool upgrade nbkp
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
