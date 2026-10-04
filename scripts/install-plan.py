#!/usr/bin/env python3
"""Print the read-only rerun plan for an input file (or installer defaults)."""
import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
from install_inputs import InputError, parse, read, VERSION_KEY  # noqa: E402
from install_plan import plan, render  # noqa: E402

STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "swirl-cloud"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="installation input file")
    args = parser.parse_args()
    values = {}
    if args.env_file:
        try:
            values = read(args.env_file)
        except InputError as error:
            print("Input file problems (the plan uses the parsed values):\n  - " + "\n  - ".join(error.problems) + "\n")
            values = {k: v for k, v in parse(args.env_file.read_text())[0].items() if k != VERSION_KEY}
    print(render(plan(ROOT, values, STATE)))


if __name__ == "__main__":
    main()
