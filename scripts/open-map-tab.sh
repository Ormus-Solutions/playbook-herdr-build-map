#!/usr/bin/env bash
# open-map-tab.sh: open a Herdr tab named "Map" whose one pane runs the build map.
#
# Usage, from a pane inside Herdr:
#   scripts/open-map-tab.sh --demo          the synthetic build, to see it in Herdr first
#   scripts/open-map-tab.sh [config]        your build (default: the program's default config)
#
# What it does, with Herdr's own CLI (the same two commands you can type by hand):
#   1. herdr tab create --label Map --cwd <this repo> --no-focus   -> a new tab and its root pane
#   2. herdr pane run <that pane> "python3 src/build_map.py ..."   -> the program in that pane
# It creates one tab and changes nothing else. Close the tab to remove it.
#
# Settings: BUILD_MAP_TAB (the tab label, default Map), HERDR (the herdr command).
set -euo pipefail

herdr=${HERDR:-herdr}
label=${BUILD_MAP_TAB:-Map}
repo="$(cd "$(dirname "$0")/.." && pwd)"

if [ "${HERDR_ENV:-}" != 1 ]; then
  echo "open-map-tab: run this from a pane inside Herdr (HERDR_ENV=1 is not set)" >&2
  exit 2
fi
if [ "$#" -gt 1 ]; then
  echo "usage: scripts/open-map-tab.sh [--demo | config]" >&2
  exit 2
fi

if [ "${1:-}" = "--demo" ]; then
  printf -v run 'python3 %q --demo' "$repo/src/build_map.py"
elif [ -n "${1:-}" ]; then
  [ -f "$1" ] || { echo "open-map-tab: no config at $1" >&2; exit 2; }
  config="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"  # absolute, as the pane has its own cwd
  printf -v run 'python3 %q --config %q' "$repo/src/build_map.py" "$config"
else
  printf -v run 'python3 %q' "$repo/src/build_map.py"
fi

args=(tab create --label "$label" --cwd "$repo" --no-focus)
if [ -n "${HERDR_WORKSPACE_ID:-}" ]; then
  args+=(--workspace "$HERDR_WORKSPACE_ID")  # the workspace this pane is in
fi
created=$("$herdr" "${args[@]}")
# Read the pane id from the JSON reply instead of guessing it (Herdr's own advice).
pane=$(printf '%s' "$created" | python3 -c 'import json, sys; print(json.load(sys.stdin)["result"]["root_pane"]["pane_id"])')
"$herdr" pane run "$pane" "$run" >/dev/null
echo "open-map-tab: the tab $label runs the build map in pane $pane"
