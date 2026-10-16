#!/usr/bin/env bash
# verify.sh -- the checks that must pass before and after every commit.
#
#   scripts/verify.sh           tests and lint on the working tree
#   scripts/verify.sh --head    the tests again, from a clean export of HEAD
#
# The --head run exists because a passing working tree proves less than it seems: a file
# that is on disk but gitignored or never added makes every test pass here and fail for
# anyone who clones. Exporting HEAD into an empty directory and testing there catches it.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$REPO_ROOT/.venv/bin/python"
RUFF="$REPO_ROOT/.venv/bin/ruff"

case "${1:-}" in
    "")
        cd "$REPO_ROOT"
        "$PY" -m pytest
        "$RUFF" check .
        "$RUFF" format --check .
        ;;
    --head)
        export_dir="$(mktemp -d "${TMPDIR:-/tmp}/spotifixed-head.XXXXXX")"
        trap 'rm -rf "$export_dir"' EXIT
        git -C "$REPO_ROOT" archive HEAD | tar -x -C "$export_dir"
        cd "$export_dir"
        echo "Testing a clean export of $(git -C "$REPO_ROOT" rev-parse --short HEAD) in $export_dir"
        "$PY" -m pytest -p no:cacheprovider
        ;;
    *)
        sed -n '2,5p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
        exit 1
        ;;
esac
