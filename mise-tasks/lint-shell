#!/usr/bin/env bash
#MISE description="Lint the tracked shell scripts with shellcheck"
#
# Shared across iglootools projects. The canonical copy is scripts/lint-shell.sh in
# iglootools/common, alongside the guideline that explains it. A project copies it verbatim to
# mise-tasks/lint-shell, executable, where mise picks it up as the lint-shell task; fix it here
# and re-copy rather than editing one copy in place.
#
# Lints every tracked shell script: a .sh or .bash file, or a file whose shebang runs sh or
# bash. The shebang is how the file tasks under mise-tasks/ are found, since they have no
# extension. A file marked linguist-vendored or linguist-generated in .gitattributes is
# skipped: vendored code is not this project's to restyle, and generated code is fixed in its
# generator. Those are the attributes GitHub already reads to leave such files out of diffs
# and language statistics, so marking a file once serves both.
#
# Written for bash 3.2, which is the bash /usr/bin/env finds on macOS: no mapfile, and the
# array is never expanded while empty, which bash before 4.4 rejects under set -u.
#
# Usage:
#     mise run lint-shell        # the usual entry point
#     mise-tasks/lint-shell      # equivalent, runs from any directory in the repo

set -euo pipefail

# Byte-wise matching: the shebang test reads the first line of every tracked file, binary ones
# included, and a UTF-8 locale makes bash's regex matching fail on invalid sequences.
export LC_ALL=C

shebang='^#!(/usr/bin/env[[:space:]]+|/bin/|/usr/bin/)(ba)?sh([[:space:]]|$)'

is_shell_script() {
  local first=""
  case "$1" in
    *.sh | *.bash) return 0 ;;
  esac
  [[ -f "$1" ]] || return 1
  IFS= read -r first < "$1" || true
  [[ $first =~ $shebang ]]
}

is_vendored_or_generated() {
  # check-attr prints "<path>: <attribute>: <value>"; an attribute that is on reads "set", or
  # "true" when written as linguist-vendored=true.
  git check-attr linguist-vendored linguist-generated -- "$1" | grep -qE ': (set|true)$'
}

cd "$(git rev-parse --show-toplevel)"

scripts=()
while IFS= read -r -d '' file; do
  if is_shell_script "$file" && ! is_vendored_or_generated "$file"; then
    scripts+=("$file")
  fi
done < <(git ls-files -z)

if ((${#scripts[@]} == 0)); then
  echo "No shell scripts to lint."
  exit 0
fi

echo "Linting ${#scripts[@]} shell script(s) with shellcheck"
# --external-sources follows `source` into the sourced file, so a variable set here and read
# there is not reported as unused. Findings inside the sourced file are not reported: that file
# is linted on its own, or skipped as vendored.
shellcheck --external-sources "${scripts[@]}"
