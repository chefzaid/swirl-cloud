#!/bin/sh
# clamd's Kubernetes probes. "ready": clamd answers with its daily signature
# version, so a complete set is loaded. "alive": clamd answers PING.
case "${1:-}" in
  ready) printf 'zVERSION\0' | nc -w 5 127.0.0.1 3310 | tr -d '\000' | grep -Eq '^ClamAV [0-9.]+/[0-9]+/' ;;
  alive) printf 'zPING\0' | nc -w 5 127.0.0.1 3310 | tr -d '\000' | grep -q '^PONG$' ;;
  *) echo "usage: probe.sh ready|alive" >&2; exit 2 ;;
esac
