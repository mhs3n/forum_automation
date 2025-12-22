#!/usr/bin/env bash
set -euo pipefail

# Run the X11 event recorder inside a running CUA desktop container.
# Usage: scripts/run_recorder.sh [--container NAME] [--out /tmp/live_macro.json]

CONTAINER="cua-desktop"
OUT="/tmp/live_macro.json"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --container)
      CONTAINER="$2"; shift 2;;
    --out)
      OUT="$2"; shift 2;;
    *)
      echo "Unknown arg: $1" >&2; exit 1;;
  esac
done

echo "[recorder] container: $CONTAINER"
echo "[recorder] output   : $OUT"

# Ensure container is running
if ! docker inspect -f '{{.State.Running}}' "$CONTAINER" >/dev/null 2>&1; then
  echo "[recorder] container '$CONTAINER' is not running." >&2
  exit 1
fi

# Copy recorder script into container
docker cp "$(dirname "$0")/x11_event_recorder.py" "$CONTAINER:/tmp/x11_event_recorder.py"

# Install dependency and run recorder
echo "[recorder] starting recorder inside container (Ctrl+C to stop)..."
docker exec -it "$CONTAINER" bash -lc "
  set -e
  # Install into /tmp/recdeps to avoid touching system site-packages
  if [ ! -d /tmp/recdeps ]; then
    pip install -q --target /tmp/recdeps python-xlib
  fi
  echo '[recorder] recording on DISPLAY=:1 -> $OUT'
  PYTHONPATH=/tmp/recdeps DISPLAY=:1 python /tmp/x11_event_recorder.py --out '$OUT'
"

echo "[recorder] finished. Recorded actions at $OUT inside container."
echo "[recorder] To copy: docker cp $CONTAINER:$OUT trajectories/$(basename "$OUT")"
