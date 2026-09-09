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
ROW_ID="dsh-pha"
ROW_NAME="@personal-historical-archive/dsh-pha"
if [ -f "$PATCH" ] && grep -q "id: $ROW_ID\|name: '$ROW_NAME'" "$PATCH"; then
  echo ">> row for dsh-pha already present in $PATCH"
else
  # The harness patch file is a top-level YAML ARRAY. The stock file is an
  # empty flow array `[]`; appending a block `- insert:` item AFTER that `[]`
  # would leave two document constructs and crash the profile on load. So: if
  # the file's substantive body is just `[]`, replace it with a block array;
  # otherwise append a new item to the existing block array.
  BODY="$(sed -e 's/#.*$//' -e '/^[[:space:]]*$/d' "$PATCH" 2>/dev/null | tr -d '[:space:]')"
  if [ "$BODY" = "[]" ] || [ -z "$BODY" ]; then
    echo ">> converting empty flow-array patch to a block sequence"
    cat > "$PATCH" <<YAML
# Your patch layer for this dsh profile, applied after every bundle layer:
# a top-level YAML array of loader patch entries (id-targeted config
# overrides, disables, and insert lists; \`!!js\` expressions allowed).

- insert:
    - id: dsh-pha
      name: '@personal-historical-archive/dsh-pha'
YAML
  else
    echo ">> appending composition row to $PATCH"
    printf '\n' >> "$PATCH"
    echo "- insert:" >> "$PATCH"
    echo "    - id: dsh-pha" >> "$PATCH"
    echo "      name: '@personal-historical-archive/dsh-pha'" >> "$PATCH"
  fi
fi

echo ""
echo ">> done. Restart the harness profile (e.g. \`dsh --profile $PROFILE ...\` or relaunch the app)."
echo "   After it boots, the nine pha_* tools are registered in every session."
