#!/bin/sh
set -eu
login=""
if [ -n "${REDIS_USERNAME:-}" ] && [ -n "${REDIS_PASSWORD:-}" ]; then
  login="${REDIS_USERNAME}:${REDIS_PASSWORD}@"
fi
REDIS_URL="redis://${login}redis.__INTERNAL_DNS_ZONE__:6379/2"
export REDIS_URL
exec "$@"
