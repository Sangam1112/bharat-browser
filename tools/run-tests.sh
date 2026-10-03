#!/bin/bash
# Run every automated check. The window tests need a display (they open a real window
# against local test servers and a throw-away profile, never your own).
set -e
cd "$(dirname "${BASH_SOURCE[0]}")/.."
python3 tools/check-version.py
python3 -W ignore -m unittest tools.test_update_signature tools.test_bharat_diagnose
python3 -W ignore -m unittest discover -s tests
