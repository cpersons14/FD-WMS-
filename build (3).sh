#!/usr/bin/env bash
# Temporary compatibility for the existing Render build command.
# Application source lives directly in config/, warehouse/, templates/, static/.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
exec bash build.sh "$@"
