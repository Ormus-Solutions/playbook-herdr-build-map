#!/usr/bin/env bash
# run.sh: see the build map on the synthetic build in examples/demo, with no setup.
# It prints one frame as plain text. The output must match examples/expected-output.txt
# (a test checks it). For the live pane on the same data: python3 src/build_map.py --demo
set -euo pipefail
cd "$(dirname "$0")/.."
python3 src/build_map.py --demo --once --no-color --width 78
