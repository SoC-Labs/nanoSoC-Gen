#!/usr/bin/env bash
# Install the nanosoc_gen configurator pre-commit hook into the current repo.
# Run from the project root (the repo whose commits you want gated):
#   bash <path-to-nanosoc_gen>/scripts/hooks/install.sh
# Symlinks .git/hooks/pre-commit at this script's sibling `pre-commit`, backing
# up any existing hook. Idempotent.
set -euo pipefail

HOOK_SRC="$(cd "$(dirname "$0")" && pwd)/pre-commit"
GIT_DIR="$(git rev-parse --git-dir 2>/dev/null)" || {
    echo "error: not inside a git repository" >&2; exit 1; }
DEST="$GIT_DIR/hooks/pre-commit"

mkdir -p "$GIT_DIR/hooks"
if [ -e "$DEST" ] && [ ! -L "$DEST" ]; then
    mv "$DEST" "$DEST.bak.$(git hash-object "$DEST" | cut -c1-8)"
    echo "backed up existing hook -> $DEST.bak.*"
fi
ln -sf "$HOOK_SRC" "$DEST"
echo "installed pre-commit hook: $DEST -> $HOOK_SRC"
