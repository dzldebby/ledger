#!/bin/sh
set -e

python scripts/migrate_with_lock.py

exec "$@"
