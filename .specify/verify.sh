#!/usr/bin/env bash
# The gate is `bench verify` — one implementation for every project, versioned
# in bench and tested there. This file exists only because callers invoke a
# path, not a command; it holds no logic and never needs editing.
exec bench verify --cwd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
