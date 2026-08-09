#!/usr/bin/env sh
set -eu

repository=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
data_root=${VOXWEAVE_HOME:-${1:-}}
if [ -z "$data_root" ]; then
  data_root=$("${PYTHON:-python3}" -c "import json,pathlib; print(json.loads(pathlib.Path(r'$repository/.voxweave.local.json').read_text())['data_root'])")
fi
export VOXWEAVE_HOME="$data_root"
exec "$data_root/.venv/bin/python" -m voxweave.gui
