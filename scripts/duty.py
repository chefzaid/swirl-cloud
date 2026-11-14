#!/usr/bin/env python3
"""Periodic duties from config/control-calendar.yaml.

  ./install.sh --duty list              every duty, when it was last done and whether it is overdue
  ./install.sh --duty done ID [--note]  record a duty done now (for duties no command records)
"""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import duties  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    action = parser.add_subparsers(dest="action", required=True)
    action.add_parser("list")
    done = action.add_parser("done")
    done.add_argument("duty")
    done.add_argument("--note", default="")
    args = parser.parse_args(argv)
    try:
        if args.action == "done":
            entry = duties.record(args.duty, args.note)
            print(f"Recorded {args.duty} as done on {entry['done']} by {entry['by']}.")
            return 0
        for duty, last, overdue in duties.status(duties.catalog(), duties.recorded()):
            state = "never done" if last is None else f"last {last:%Y-%m-%d}"
            flag = "OVERDUE" if overdue is not None else "ok"
            print(f"{flag:8} {duty['id']:20} {state:16} every {duty['every_days']} days: {duty['name']}")
            if overdue is not None:
                print(f"         how: {duty['done_by']}")
        return 0
    except (RuntimeError, ValueError) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
