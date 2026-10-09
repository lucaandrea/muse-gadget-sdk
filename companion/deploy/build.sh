#!/bin/sh
set -eu
# Recreate cached environments: venv otherwise retains old interpreter symlinks,
# which may point to a workspace-only Python unavailable in production.
python3 -m venv --clear .venv
# Replit may set pip's user-install default globally. A virtual environment must
# install into its own site-packages, regardless of that host preference.
.venv/bin/python -m pip install --no-user --require-hashes -r companion/requirements-cloud.txt
.venv/bin/python -m pip install --no-user --no-deps ./companion
