"""Pompy command line. Commands are added phase by phase (see docs/STARTER_PLAN.md)."""

import argparse

from pompy import __version__


def main() -> None:
    parser = argparse.ArgumentParser(prog="pompy", description=__doc__)
    parser.add_argument("--version", action="version", version=f"pompy {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("ingest", help="(Phase 1) copy + parse Claude Code transcripts")
    sub.add_parser("stats", help="(Phase 1) usage analytics")
    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
    else:
        print(f"`pompy {args.command}` is planned for Phase 1. Not implemented yet.")


if __name__ == "__main__":
    main()
