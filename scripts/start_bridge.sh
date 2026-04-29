#!/bin/bash
# Start adb_bridge — skips if already running on port 8765.
# Uses MOBILE_GUI_PYTHON env var, .venv, or system python3 (in that order).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUNDLE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PORT=8765

# Check config.yaml exists
if [ ! -f "$BUNDLE_ROOT/config.yaml" ]; then
    echo "[start_bridge] ERROR: config.yaml not found."
    echo "[start_bridge] Run the following to create it from the example:"
    echo ""
    echo "    cp $BUNDLE_ROOT/config.example.yaml $BUNDLE_ROOT/config.yaml"
    echo "    nano $BUNDLE_ROOT/config.yaml   # fill in llm.api_base and llm.model_name"
    echo ""
    exit 1
fi

# Resolve Python interpreter
if [ -n "$MOBILE_GUI_PYTHON" ]; then
    PYTHON="$MOBILE_GUI_PYTHON"
elif [ -f "$BUNDLE_ROOT/.venv/bin/python" ]; then
    PYTHON="$BUNDLE_ROOT/.venv/bin/python"
else
    PYTHON="python3"
fi

# Skip if already running
if lsof -ti tcp:$PORT >/dev/null 2>&1; then
    echo "[start_bridge] adb_bridge already running on port $PORT, skipping."
    exit 0
fi

cd "$BUNDLE_ROOT/adapter"
echo "[start_bridge] Using Python: $PYTHON"
echo "[start_bridge] Starting adb_bridge from $BUNDLE_ROOT/adapter ..."
PYTHONUNBUFFERED=1 "$PYTHON" adb_bridge.py
