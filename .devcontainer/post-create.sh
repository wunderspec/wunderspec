#!/usr/bin/env bash
set -euo pipefail

uv sync

uv run wunderspec --help >/dev/null
