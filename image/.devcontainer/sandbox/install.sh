#!/bin/sh
# The dev container specification always runs a feature's install.sh; the work
# is in install.py next to it.
set -eu
exec python3 "$(dirname "$0")/install.py"
