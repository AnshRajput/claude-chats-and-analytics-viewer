#!/bin/sh
set -e

PKG="claude-chats-and-analytics-viewer"

echo ""
echo "  Claude Chats & Analytics Viewer"
echo "  ================================"
echo ""

# ── 1. Try pipx (already installed) ──────────────────────────────────────────
if command -v pipx >/dev/null 2>&1; then
  echo "  [1/2] Installing via pipx..."
  pipx install "$PKG" 2>/dev/null || pipx upgrade "$PKG"
  echo "  [2/2] Done!"
  echo ""
  exec pipx run "$PKG"
fi

# ── 2. Try uv (already installed) ────────────────────────────────────────────
if command -v uv >/dev/null 2>&1; then
  echo "  [1/1] Launching via uv (no install needed)..."
  echo ""
  exec uv tool run "$PKG"
fi

# ── 3. macOS: install pipx via Homebrew ──────────────────────────────────────
if command -v brew >/dev/null 2>&1; then
  echo "  [1/3] Installing pipx via Homebrew..."
  brew install pipx --quiet
  pipx ensurepath --quiet 2>/dev/null || true
  echo "  [2/3] Installing $PKG..."
  pipx install "$PKG"
  echo "  [3/3] Done!"
  echo ""
  echo "  Note: run 'source ~/.zshrc' (or open a new terminal) to use 'ccv' directly next time."
  echo ""
  exec "$HOME/.local/bin/ccv"
fi

# ── 4. Linux / fallback: install pipx via pip then install pkg ───────────────
if command -v python3 >/dev/null 2>&1; then
  echo "  [1/3] Installing pipx..."
  python3 -m pip install --quiet --user pipx
  python3 -m pipx ensurepath --quiet 2>/dev/null || true
  echo "  [2/3] Installing $PKG..."
  python3 -m pipx install "$PKG"
  echo "  [3/3] Done!"
  echo ""
  echo "  Note: run 'source ~/.bashrc' (or open a new terminal) to use 'ccv' directly next time."
  echo ""
  exec "$HOME/.local/bin/ccv"
fi

echo "  [ERROR] Python 3 not found. Install it from https://python.org"
exit 1
