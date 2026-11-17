#!/bin/sh
# Before clamd starts: a complete, current signature set on the pod's volume
# (mounted here at /signatures; clamd and freshclam see it at /var/lib/clamav).
# The image carries ClamAV's official set from its build day, so a new volume,
# or one older than the image, takes that set and never downloads the whole
# databases from the mirror, which refuses addresses that do so too often.
# freshclam then fetches only the differences since; when it cannot (mirror
# down or refusing), the set already on the volume stays.
set -u
bundled=/var/lib/clamav
db=/signatures
version() { sigtool --info "$1" 2>/dev/null | sed -n 's/^Version: //p'; }
# A killed download leaves its temporary directory behind.
rm -rf "$db"/tmp.*
for name in main daily bytecode; do
  shipped="$bundled/$name.cvd"
  [ -s "$shipped" ] || continue
  current=""
  for file in "$db/$name.cld" "$db/$name.cvd"; do
    if [ -s "$file" ]; then current="$file"; break; fi
  done
  have="$( [ -n "$current" ] && version "$current")"
  offered="$(version "$shipped")"
  if [ "${have:-0}" -lt "${offered:-0}" ]; then
    echo "Taking $name version $offered from the image (the volume had ${have:-none})"
    cp "$shipped" "$db/$name.cvd.new" && mv "$db/$name.cvd.new" "$db/$name.cvd" && rm -f "$db/$name.cld"
    rm -f "$db/$name"-*.cvd.sign
    cp "$bundled/$name"-*.cvd.sign "$db/" 2>/dev/null || true
  fi
done
freshclam --foreground --stdout --config-file=/etc/clamav-config/freshclam.conf --datadir="$db" || \
  echo "freshclam exited with $?; keeping the signatures on the volume"
missing=""
for name in main daily bytecode; do
  [ -s "$db/$name.cvd" ] || [ -s "$db/$name.cld" ] || missing="$missing $name"
done
if [ -n "$missing" ]; then
  # clamd stays down, and so out of the Service, rather than scan with less.
  echo "No complete signature set (missing:$missing); the pod retries after its back-off." >&2
  exit 1
fi
