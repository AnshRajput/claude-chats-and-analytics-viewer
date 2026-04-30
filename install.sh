#!/bin/sh
set -e

echo ""
echo "  Claude Conversation Viewer — Installer"
echo "  ======================================="

# Check Python
if ! command -v python3 >/dev/null 2>&1; then
  echo "  [ERROR] Python 3 is required. Install it from https://python.org"
  exit 1
fi

echo "  Installing via pip..."
python3 -m pip install --quiet --upgrade claude-conversation-viewer

echo "  Done! Starting viewer..."
echo ""
ccv
