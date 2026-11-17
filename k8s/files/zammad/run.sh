#!/bin/sh
set -eu
# Zammad's own Redis in corp (corp/zammad.yaml).
REDIS_URL="redis://zammad-redis:6379/0"
export REDIS_URL
exec "$@"
