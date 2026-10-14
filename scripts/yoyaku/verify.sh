#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
python3 scripts/yoyaku/test_local_launch.py
bash -n scripts/reload.sh scripts/yoyaku/local-launch.sh
base="$(cat scripts/yoyaku/upstream-base)"
python3 scripts/verify-local.py --swift-changed "$base"
python3 - <<'PY'
from pathlib import Path
assert {p.name for p in Path('.github/workflows').iterdir() if p.is_file()} == {'yoyaku-verify.yml'}, 'inherited workflows must stay archived'
PY
