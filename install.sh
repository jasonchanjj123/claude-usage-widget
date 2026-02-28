#!/bin/bash
# Claude Usage Widget — one-command installer
set -e

echo "📦 Installing dependencies..."
pip3 install -r "$(dirname "$0")/requirements.txt" -q --upgrade

echo "✅ Done.  Launching widget..."
python3 "$(dirname "$0")/claude_usage_widget.py"
