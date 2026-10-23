#!/bin/bash
# Mac setup: double-click this file (first time: right-click, Open).
cd "$(dirname "$0")"
command -v python3 >/dev/null || { echo "Python 3.10 or newer is required. Install it from python.org, then run this again."; read -r -p "Press Enter"; exit 1; }
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt
.venv/bin/python setup.py
echo
echo "Done. Double-click record.command to record a meeting."
read -r -p "Press Enter to close"
