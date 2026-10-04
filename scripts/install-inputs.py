#!/usr/bin/env python3
"""Validate or import a Swirl Cloud installation-input file without executing it.

  check FILE   print every problem, one per line; exit 1 when any exist
  pairs FILE   emit NUL-separated NAME/VALUE pairs of the file's inputs, with
               NAME_FILE secret references resolved, for the installer to assign
               as data (problems are reported by `check`)
  names        list every schema input and legacy alias the file replaces
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from install_inputs import BY_NAME, INPUTS, SECRET_FILES, InputError, parse, read, resolve  # noqa: E402

# Older aliases an operator's shell may still export; the file replaces them.
ALIASES = ("CLOUDFLARE_TLS_SECRET_NAME", "GITHUB_OWNER", "CLOUDFLARE_ZONE")


def main(argv):
    if argv[:1] == ["names"] and len(argv) == 1:
        print("\n".join([item.name for item in INPUTS] + list(SECRET_FILES) + list(ALIASES)))
        return 0
    if len(argv) != 2 or argv[0] not in ("check", "pairs"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    command, path = argv
    if command == "check":
        try:
            read(path)
        except InputError as error:
            print("\n".join(error.problems))
            return 1
        return 0
    try:
        values, _ = parse(Path(path).read_text())
    except (OSError, UnicodeError):
        return 1
    # Secret file references become values here; unreadable ones were reported by `check`.
    values, _ = resolve(values)
    output = sys.stdout.buffer
    for name, value in values.items():
        if name in BY_NAME and "\0" not in value:
            output.write(name.encode() + b"\0" + value.encode() + b"\0")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
