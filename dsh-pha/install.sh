#!/usr/bin/env bash
#
# Attach the dsh-pha plugin to a DeepSeek Harness profile.
#
# Usage:  ./install.sh <profile> [repo-root]
#   <profile>    the harness profile to attach to (default: desktop)
#   [repo-root]  absolute path to this repository (default: the parent of this file)
#
# Requires: pnpm (the bundled profiles use pnpm), and the `dsh` CLI on PATH.
set -euo pipefail

PROFILE="${1:-desktop}"
REPO="${2:-$(cd "$(dirname "$0")/.." && pwd)}"
PKG="$REPO/dsh-pha"
DSH_HOME="${DSH_HOME:-$HOME/.dsh}"
PROF="$DSH_HOME/profiles/$PROFILE"

if [ ! -d "$PROF" ]; then
  echo "profile not found: $PROF" >&2
  echo "profiles:" >&2
  ls "$DSH_HOME/profiles" >&2 2>/dev/null || true
  exit 1
fi

echo ">> adding $PKG to profile $PROFILE (workspace node_modules)"
if [ -f "$PROF/pnpm-workspace.yaml" ]; then
  (cd "$PROF" && pnpm add "$PKG")
else
  mkdir -p "$PROF/node_modules/@personal-historical-archive"
  ln -sfn "$PKG" "$PROF/node_modules/@personal-historical-archive/dsh-pha"
  echo "   (no pnpm workspace; symlinked into node_modules)"
fi

PATCH="$PROF/cordis.patch.yml"
if [ -f "$PATCH" ] && grep -q "dsh-pha" "$PATCH"; then
  echo ">> row for dsh-pha already present in $PATCH"
else
  echo ">> appending composition row to $PATCH"
  {
    echo ""
    echo "- insert:"
    echo "    - id: dsh-pha"
    echo "      name: '@personal-historical-archive/dsh-pha'"
  } >> "$PATCH"
fi

echo ""
echo ">> done. Restart the harness profile (e.g. \`dsh --profile $PROFILE ...\` or relaunch the app)."
echo "   After it boots, the nine pha_* tools are registered in every session."
