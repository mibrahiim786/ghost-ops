#!/usr/bin/env bash
# install.sh — Idempotent installer for Ghost Ops
# Installs to ~/ghost-ops/ and registers the launchd agent.

set -euo pipefail

INSTALL_DIR="$HOME/ghost-ops"
PLIST_NAME="com.dubsopenhub.ghost-ops.plist"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_DEST="$LAUNCH_AGENTS_DIR/$PLIST_NAME"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Ghost Ops Installer"
echo "    Source : $SCRIPT_DIR"
echo "    Install: $INSTALL_DIR"

# ── 1. Create directories ────────────────────────────────────────────────────
echo "==> Creating directories..."
mkdir -p "$INSTALL_DIR/lib"
mkdir -p "$INSTALL_DIR/missions"
mkdir -p "$INSTALL_DIR/tests"
mkdir -p "$INSTALL_DIR/logs"
mkdir -p "$LAUNCH_AGENTS_DIR"

# ── 2. Copy source files ─────────────────────────────────────────────────────
echo "==> Copying source files..."
cp -f "$SCRIPT_DIR/ghost_ops.py"     "$INSTALL_DIR/ghost_ops.py"
cp -f "$SCRIPT_DIR/ghost_ops.toml"   "$INSTALL_DIR/ghost_ops.toml"

for f in lib/__init__.py lib/elo_router.py lib/llm_backend.py lib/state.py; do
    cp -f "$SCRIPT_DIR/$f" "$INSTALL_DIR/$f"
done

for f in missions/__init__.py missions/portfolio_watchdog.py missions/inbox_autopilot.py missions/fleet_evolution.py; do
    cp -f "$SCRIPT_DIR/$f" "$INSTALL_DIR/$f"
done

# ── 3. Initialise the database ────────────────────────────────────────────────
echo "==> Initialising database..."
python3 - <<'PYEOF'
import sys, os
sys.path.insert(0, os.path.expanduser("~/ghost-ops"))
from lib.state import StateStore
store = StateStore("~/ghost-ops/ghost_ops.db")
store.open()
store.close()
print("    Database ready.")
PYEOF

# ── 4. Install and load launchd plist ─────────────────────────────────────────
echo "==> Installing launchd plist..."
cp -f "$SCRIPT_DIR/$PLIST_NAME" "$PLIST_DEST"

# Unload first if already loaded (idempotent)
if launchctl list | grep -q "com.dubsopenhub.ghost-ops" 2>/dev/null; then
    echo "    Unloading existing service..."
    launchctl unload "$PLIST_DEST" 2>/dev/null || true
fi

echo "    Loading service..."
launchctl load -w "$PLIST_DEST"

# ── 5. Smoke test with --dry-run ──────────────────────────────────────────────
echo "==> Running smoke test (--dry-run)..."
cd "$INSTALL_DIR"
python3 ghost_ops.py --dry-run --once
echo "    Smoke test passed."

echo ""
echo "✅  Ghost Ops installed successfully."
echo "    Logs: $INSTALL_DIR/logs/ghost_ops.log"
echo "    DB  : $INSTALL_DIR/ghost_ops.db"
echo "    Plist: $PLIST_DEST"
