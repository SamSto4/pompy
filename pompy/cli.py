"""Pompy command line. Commands are added phase by phase (see docs/STARTER_PLAN.md)."""

import argparse
import time
from pathlib import Path

from pompy import __version__, config


def _cmd_ingest(args: argparse.Namespace) -> None:
    from pompy.ingest import ingest, rebuild, summary

    data = config.data_dir()
    start = time.perf_counter()
    source = Path(args.source) if args.source else config.source_dir()
    report = rebuild(data, source) if args.rebuild else ingest(source, data)
    elapsed = time.perf_counter() - start
    if args.quiet:
        return

    s = summary(data)
    new = report.new
    print(f"Pompy ingest finished in {elapsed:.2f}s")
    print(
        f"  read {report.files_read} of {report.files_seen} transcripts "
        f"({report.bytes_read / 1_000_000:.1f} MB new)"
    )
    print(f"  new: {new['prompt']} prompts, {new['turn']} assistant records, {new['event']} events")
    if report.bad_lines:
        print(f"  skipped {report.bad_lines} unreadable lines")
    print("All-time memory:")
    print(f"  {s['sessions']} sessions across {s['projects']} projects")
    kinds = ", ".join(f"{n} {k}" for k, n in s["prompts_by_kind"].items())
    print(f"  {s['prompts']} prompts ({kinds})")
    print(f"  {s['assistant_turns']} assistant messages")
    if s["events"]:
        print("  friction events: " + ", ".join(f"{n} {k}" for k, n in s["events"].items()))
    if s["first_prompt"]:
        print(f"  from {s['first_prompt'][:10]} to {s['last_prompt'][:10]} (UTC)")


def _cmd_stats(args: argparse.Namespace) -> None:
    import json
    from zoneinfo import ZoneInfo

    from pompy import stats, store

    conn = store.connect(config.data_dir() / "pompy.db")
    result = stats.compute(conn, ZoneInfo(args.tz) if args.tz else None)
    conn.close()
    if not result["totals"]["prompts"]:
        print("No prompts yet. Run `pompy ingest` first.")
        return
    out = config.out_dir()
    out.mkdir(parents=True, exist_ok=True)
    (out / "stats.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2) if args.json else stats.render(result))
    if not args.json:
        print(f"\n(saved to {out / 'stats.json'})")


def main() -> None:
    parser = argparse.ArgumentParser(prog="pompy", description=__doc__)
    parser.add_argument("--version", action="version", version=f"pompy {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_ingest = sub.add_parser("ingest", help="copy + parse Claude Code transcripts (incremental)")
    p_ingest.add_argument("--source", help="transcript folder (default: ~/.claude/projects)")
    p_ingest.add_argument(
        "--rebuild", action="store_true", help="recreate the database from Pompy's raw copies"
    )
    p_ingest.add_argument("--quiet", action="store_true", help="print nothing (for hooks)")
    p_ingest.set_defaults(func=_cmd_ingest)

    p_stats = sub.add_parser("stats", help="usage analytics: models, effort, time, lengths")
    p_stats.add_argument("--tz", help="time zone, e.g. America/New_York (default: this Mac's)")
    p_stats.add_argument("--json", action="store_true", help="print the raw JSON")
    p_stats.set_defaults(func=_cmd_stats)

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
    elif hasattr(args, "func"):
        args.func(args)
    else:
        print(f"`pompy {args.command}` is not implemented yet.")


if __name__ == "__main__":
    main()
