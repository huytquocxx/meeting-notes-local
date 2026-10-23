#!/bin/bash
# Mac: double-click to start recording. Press Ctrl+C to stop.
cd "$(dirname "$0")"
.venv/bin/python notes.py record
echo
read -r -p "Done. Press Enter to close"
