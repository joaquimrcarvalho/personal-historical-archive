#!/usr/bin/env bash
#
# Attach the dsh-pha plugin to a DeepSeek Harness profile.
#
# Usage:  ./install.sh [profile] [repo-root]
#   [profile]    the harness profile to attach to (default: the profile a running
#                harness booted with, else `desktop`). DSH Desktop / `dsh web`
#                run the `web` profile — installing into the wrong one looks
#                successful and does nothing.
#   [repo-root]  absolute path to this repository (default: the parent of this file)
#
# Requires: pnpm (the bundled profiles use pnpm), and the `dsh` CLI on PATH.
set -euo pipefail

REPO="${2:-$(cd "$(dirname "$0")/.." && pwd)}"
PKG="$REPO/dsh-pha"
DSH_HOME="${DSH_HOME:-$HOME/.dsh}"
PROFILES="$DSH_HOME/profiles"

# The plugin is only composed from the profile its process booted with, so prefer
# the profile of a harness that is actually running (its command line ends with
# the profile name, e.g. `.../desktop-cli.js web`).
running_profile() {
  local name args
  for name in $(ls -1 "$PROFILES" 2>/dev/null); do
    [ -d "$PROFILES/$name" ] || continue
    while IFS= read -r args; do
      case "$args" in
        *dsh*|*desktop-cli*|*bin.js*)
          case "$args" in
            *" $name"|*" $name "*) printf '%s' "$name"; return 0 ;;
          esac
          ;;
      esac
    done < <(ps -Ao args= 2>/dev/null)
  done
  return 1
}

if [ -n "${1:-}" ]; then
  PROFILE="$1"
  ORIGIN="argument"
elif PROFILE="$(running_profile)"; then
  ORIGIN="detected from a running harness"
else
  PROFILE="desktop"
  ORIGIN="default"
fi
PROF="$PROFILES/$PROFILE"

echo ">> profile: $PROFILE  ($ORIGIN)"

if [ ! -d "$PROF" ]; then
  echo "profile not found: $PROF" >&2
  echo "profiles:" >&2
  ls "$PROFILES" >&2 2>/dev/null || true
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
echo "   Verify the host half: curl -s -H 'Origin: http://127.0.0.1:3080' http://127.0.0.1:3080/pha/documents"
echo "   (JSON = the row activated; 404 = wrong profile, or this profile has not restarted yet)"
