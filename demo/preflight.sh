#!/usr/bin/env bash
# Check that everything `mise run demo-record` needs is installed, and say how to fix
# what is not. Every check runs, so one run lists every problem.
#
# The demo (demo/demo.sh) runs inside an asciinema session, which reports only that the
# session exited with status 1; a missing tool is otherwise visible only in the cast.

set -uo pipefail

problems=0

ok() { printf '  %-8s %s\n' ok "$1"; }
missing() {
    printf '  %-8s %s\n  %-8s %s\n' MISSING "$1" "" "$2"
    problems=$((problems + 1))
}

echo "Demo dependencies:"

for tool in asciinema pv; do
    if command -v "$tool" >/dev/null 2>&1; then
        ok "$tool"
    else
        missing "$tool" "Run: mise install (mise.toml provides it)"
    fi
done

if uv run --no-sync nbkp --version >/dev/null 2>&1; then
    ok "nbkp (project environment)"
else
    missing "nbkp (project environment)" "Run: mise run install"
fi

# nbkp demo seed --docker needs the docker extra (the Docker SDK and cryptography).
if uv run --no-sync python -c "import docker, cryptography" >/dev/null 2>&1; then
    ok "nbkp docker extra"
else
    missing "nbkp docker extra" "Run: mise run install (it installs every extra)"
fi

if ! command -v docker >/dev/null 2>&1; then
    missing "docker" "Install Docker (Docker Desktop, OrbStack, colima, ...)"
elif docker info >/dev/null 2>&1; then
    ok "docker (daemon running)"
else
    missing "docker daemon" "Start Docker: the docker CLI is installed but cannot reach a daemon"
fi

if [ "$problems" -gt 0 ]; then
    printf '\n%d demo dependenc%s missing.\n' "$problems" "$([ "$problems" -eq 1 ] && echo "y is" || echo "ies are")"
    exit 1
fi
echo
echo "All demo dependencies are installed."
