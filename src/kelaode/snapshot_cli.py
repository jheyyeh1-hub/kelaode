"""Command-line interface for immutable snapshot packages."""
from __future__ import annotations

import argparse

from .snapshot_v2 import materialize


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("materialize")
    command.add_argument("--spec", required=True)
    command.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "materialize":
        materialize(args.spec, args.output)


if __name__ == "__main__":
    main()
