#!/bin/sh
set -e

echo ""
echo "  Claude Chats & Analytics Viewer — Installer"
echo "  ============================================"

PKG="claude-chats-and-analytics-viewer"

# Prefer pipx (safe on PEP 668 / Homebrew systems)
if command -v pipx >/dev/null 2>&1; then
  echo "  Installing via pipx..."
  pipx install --quiet "$PKG" || pipx upgrade --quiet "$PKG"

# Fall back to uv
elif command -v uv >/dev/null 2>&1; then
  echo "  Installing via uv..."
  uv tool install "$PKG"

# Fall back to pip3 --user
elif command -v pip3 >/dev/null 2>&1; then
  echo "  Installing via pip3 --user..."
  pip3 install --quiet --user --upgrade "$PKG"

elif command -v python3 >/dev/null 2>&1; then
  echo "  Installing via python3 -m pip --user..."
  python3 -m pip install --quiet --user --upgrade "$PKG"

else
  echo "  [ERROR] No package manager found."
  echo "  Install pipx first:  brew install pipx"
  exit 1
fi

echo "  Done! Starting viewer..."
echo ""
ccv
