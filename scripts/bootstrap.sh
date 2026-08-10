#!/usr/bin/env sh
set -eu

repository=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
data_root=${1:-}
python_cmd=${PYTHON:-python3.12}
if [ -z "$data_root" ]; then
  printf 'VoxWeave data/runtime directory: '
  read -r data_root
fi
case "$data_root" in
  /*) ;;
  *) echo 'Data root must be absolute.' >&2; exit 2 ;;
esac
mkdir -p "$data_root/pip-cache" "$data_root/temp"
export PIP_CACHE_DIR="$data_root/pip-cache"
export TMPDIR="$data_root/temp"
if [ ! -x "$data_root/.venv/bin/python" ]; then
  "$python_cmd" -m venv "$data_root/.venv"
fi
export PIP_CONSTRAINT="$repository/requirements.lock"
"$data_root/.venv/bin/python" -m pip install 'pip==26.2.1'
"$data_root/.venv/bin/python" -m pip install -c "$PIP_CONSTRAINT" -e "$repository[dev]"
"$data_root/.venv/bin/python" -m voxweave.bootstrap --data-root "$data_root"
printf 'VoxWeave source environment is ready: %s\n' "$data_root/.venv"
