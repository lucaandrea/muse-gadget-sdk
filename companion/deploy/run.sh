#!/bin/sh
set -eu
export MUSE_HOSTED=1
# New Replit apps already receive a persistent private SESSION_SECRET. Use it
# when no explicit migration/owner key has been supplied; never regenerate it
# on a deployment or the encrypted account grants would become unreadable.
export MUSE_OWNER_TOKEN="${MUSE_OWNER_TOKEN:-${SESSION_SECRET:-}}"
export MUSE_DATA_DIR="${MUSE_DATA_DIR:-/tmp/muse-companion}"
# Console-script shebangs contain the build workspace's absolute path. Replit
# relocates the deployment, so invoke the module through the relative interpreter.
exec .venv/bin/python -m muse_companion.cli serve --host 0.0.0.0 --port "${PORT:-8765}"
