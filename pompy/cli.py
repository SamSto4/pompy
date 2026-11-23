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
    print(f"  {report.excerpts} exchange excerpts (prompt + reply + what happened next)")
    print(f"  {report.episodes} episode cards (one per chat)")
    if s["events"]:
        print("  friction events: " + ", ".join(f"{n} {k}" for k, n in s["events"].items()))
    if s["first_prompt"]:
        print(f"  from {s['first_prompt'][:10]} to {s['last_prompt'][:10]} (UTC)")


def _working_set(args: argparse.Namespace, period: str | None = None):
    """The analysis window for this command: all-time unless --period/--project narrow it."""
    from zoneinfo import ZoneInfo

    from pompy.working_set import WorkingSet

    tz = ZoneInfo(args.tz) if getattr(args, "tz", None) else None
    try:
        return WorkingSet.for_period(
            period or getattr(args, "period", None), tz, getattr(args, "project", None)
        )
    except ValueError as e:
        raise SystemExit(str(e)) from None


def _save_result(name: str, result: dict, ws, as_json: bool, text: str) -> None:
    """Print a report. Only all-time results are saved: they are Pompy's reference copy."""
    import json

    print(json.dumps(result, indent=2) if as_json else text)
    if not ws.window.all_time:
        return
    out = config.out_dir()
    out.mkdir(parents=True, exist_ok=True)
    (out / name).write_text(json.dumps(result, indent=2))
    if not as_json:
        print(f"\n(saved to {out / name})")


def _cmd_stats(args: argparse.Namespace) -> None:
    from pompy import stats

    ws = _working_set(args)
    conn = _open_store()
    result = stats.compute(conn, ws)
    conn.close()
    if not result["totals"]["prompts"]:
        print(f"No prompts in {ws.window.label}. Run `pompy ingest` first, or widen --period.")
        return
    _save_result("stats.json", result, ws, args.json, stats.render(result))


def _open_store():
    from pompy import store

    conn = store.connect(config.data_dir() / "pompy.db")
    if store.needs_rebuild(conn):
        conn.close()
        raise SystemExit(
            "Pompy's store is from an older version. Run `pompy ingest` to upgrade it."
        )
    return conn


def _cmd_keywords(args: argparse.Namespace) -> None:
    from pompy import keywords

    ws = _working_set(args)
    conn = _open_store()
    result = keywords.compute(conn, ws)
    conn.close()
    if not result["typed_prompts"]:
        print(f"No prompts in {ws.window.label}. Run `pompy ingest` first, or widen --period.")
        return
    _save_result("keywords.json", result, ws, args.json, keywords.render(result))


def _cmd_search(args: argparse.Namespace) -> None:
    from pompy.search import search

    ws = _working_set(args)
    conn = _open_store()
    mode = "keyword" if args.exchanges and args.mode == "hybrid" else args.mode
    if args.raw:
        mode = "prompts"  # FTS5 syntax applies to the bare-prompt index
    if mode != "prompts":
        from pompy import embed, excerpts, hybrid

        if not excerpts.count(conn):
            mode = "prompts"  # an old store: search bare prompts until `pompy ingest` runs
    if mode != "prompts":
        query = " ".join(args.query)
        embedder = hybrid.default_embedder(conn) if mode in ("hybrid", "meaning") else None
        if mode == "meaning" and embedder is None:
            conn.close()
            raise SystemExit("No meaning index yet. Run `pompy embed` first.")
        with ws.lens(conn):
            if mode == "meaning":
                found = embed.search(conn, query, embedder, limit=args.limit, tz=ws.tz)
            elif mode == "keyword":
                found = excerpts.search(conn, query, limit=args.limit, tz=ws.tz)
            else:
                found = hybrid.search(
                    conn, query, embedder, limit=args.limit, tz=ws.tz, blend=args.blend
                )
        conn.close()
        if mode == "hybrid" and embedder is None:
            print("(keyword only: run `pompy embed` to add meaning search)\n")
        if not found:
            print("No matches.")
        for h in found:
            why = []
            if h.get("keyword_rank"):
                why.append(f"keyword #{h['keyword_rank']}")
            if h.get("meaning_rank"):
                why.append(f"meaning #{h['meaning_rank']}")
            print(f"{h['local_time']}  {h['header']}" + (f"   [{', '.join(why)}]" if why else ""))
            print(f"  You: {h['prompt'][:200]}")
            if h["reply"]:
                print(f"  Claude: {h['reply'][:200]}")
            print(f"  Then: {h['outcome']}")
            print(f"  ref: {h['session_id']} / {h['uuid']}")
        return
    with ws.lens(conn):
        hits = search(conn, " ".join(args.query), limit=args.limit, raw=args.raw, tz=ws.tz)
    conn.close()
    if not hits:
        print("No matches.")
    for h in hits:
        print(f"{h['local_time']}  {h['project_name'] or h['project_key']}  ({h['kind']})")
        print(f"  {h['snippet']}")
        print(f"  ref: {h['session_id']} / {h['uuid']}")


def _cmd_embed(args: argparse.Namespace) -> None:
    from pompy import embed, excerpts

    conn = _open_store()
    if not excerpts.count(conn):
        conn.close()
        raise SystemExit("No exchange excerpts yet. Run `pompy ingest` first.")
    result = embed.update(conn, embed.FastEmbedder())
    total = embed.count(conn)
    conn.close()
    if args.quiet:
        return
    print(
        f"Embedded {result['embedded']} exchanges in {result['seconds']}s "
        f"({result['unchanged']} unchanged, {result['removed']} removed)."
    )
    print(f"Meaning index: {total} exchanges, model {embed.DEFAULT_MODEL} (local).")


def _cmd_eval(args: argparse.Namespace) -> None:
    import json

    from pompy import evaluate, excerpts, hybrid

    path = Path(args.file) if args.file else config.data_dir() / "eval.json"
    if not path.exists():
        raise SystemExit(
            f"No eval set at {path}. Write one as a JSON list of "
            '{"query": ..., "relevant": [prompt uuids]} (keep it in data/: it holds your prompts).'
        )
    conn = _open_store()
    if not excerpts.count(conn):
        conn.close()
        raise SystemExit("No exchange excerpts yet. Run `pompy ingest` first.")
    result = evaluate.run(conn, evaluate.load(path), hybrid.default_embedder(conn), k=args.k)
    conn.close()
    print(json.dumps(result, indent=2) if args.json else evaluate.render(result))


def _cmd_episodes(args: argparse.Namespace) -> None:
    from pompy import episodes, hybrid

    ws = _working_set(args)
    conn = _open_store()
    query = " ".join(args.query)
    with ws.lens(conn):
        if query:
            found = episodes.find(conn, query, hybrid.default_embedder(conn), args.limit, ws.tz)
        else:
            listed = episodes.cards(conn, project=args.project, limit=args.limit)
    conn.close()
    if not query:
        if not listed:
            print("No episode cards. Run `pompy ingest` first, or widen --period.")
        print("\n\n".join(episodes.render_card(c) for c in listed))
        return
    if not found:
        print("No chats match.")
    for f in found:
        print(episodes.render_card(f["card"]))
        print("  evidence:")
        for e in f["evidence"]:
            print(f"    · You: {e['prompt'][:140]}")
            print(f"      Then: {e['outcome']}   (ref {e['uuid']})")
        print()


def _cmd_score(args: argparse.Namespace) -> None:
    from pompy import score

    ws = _working_set(args)
    conn = _open_store()
    result = score.compute(conn, ws)
    conn.close()
    if not result["typed_prompts"]:
        print(f"No prompts in {ws.window.label}. Run `pompy ingest` first, or widen --period.")
        return
    _save_result("specificity.json", result, ws, args.json, score.render(result))


def _cmd_compare(args: argparse.Namespace) -> None:
    import json

    from pompy import compare
    from pompy.working_set import before

    a = _working_set(args, args.period_a)
    if args.period_b == "before":
        try:
            b = type(a)(window=before(a.window), tz=a.tz)
        except ValueError as e:
            raise SystemExit(str(e)) from None
    else:
        b = _working_set(args, args.period_b)
    conn = _open_store()
    result = compare.compute(conn, a, b)
    conn.close()
    print(json.dumps(result, indent=2) if args.json else compare.render(result))


def _cmd_context(args: argparse.Namespace) -> None:
    import json

    from pompy import working_set

    ws = _working_set(args)
    ws.question = " ".join(args.question)
    ws.budget_tokens = args.budget
    conn = _open_store()
    ws.retrieve(conn)
    conn.close()
    manifest = ws.manifest()
    out = config.out_dir()
    out.mkdir(parents=True, exist_ok=True)
    (out / "working_set.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2) if args.json else working_set.render(ws))
    if not args.json:
        print(f"\n(saved to {out / 'working_set.json'}: the Memory Lab reads this)")


def _cmd_publish(args: argparse.Namespace) -> None:
    from zoneinfo import ZoneInfo

    from pompy import publish

    conn = _open_store()
    out = config.out_dir() / "dashboard"
    bundle = publish.build(conn, out, ZoneInfo(args.tz) if args.tz else None)
    conn.close()
    size = (out / "data.json").stat().st_size / 1024
    print(f"Built the Pompy Dashboard in {out}")
    print(f"  {bundle['meta']['prompts']} prompts, data bundle {size:.0f} KB")
    print("  Nothing has left your Mac. Ask Claude to publish it to your private artifact.")


def _cmd_check(args: argparse.Namespace) -> None:
    from pompy import score

    text = " ".join(args.text)
    print(score.render_check(text, score.score_text(text)))


def main() -> None:
    from pompy.working_set import PERIOD_HELP

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

    p_kw = sub.add_parser("keywords", help="your top terms, phrases, opening style, themes")
    p_kw.add_argument("--tz", help="time zone for monthly themes (default: this Mac's)")
    p_kw.add_argument("--json", action="store_true", help="print the raw JSON")
    p_kw.set_defaults(func=_cmd_keywords)

    p_search = sub.add_parser("search", help="search your exchanges by words and meaning")
    p_search.add_argument("query", nargs="+", help="words to find (stems match: fix ≈ fixing)")
    p_search.add_argument("--limit", type=int, default=10)
    p_search.add_argument(
        "--project", help="only this project: exact name or key, else names containing it"
    )
    p_search.add_argument("--raw", action="store_true", help="pass FTS5 syntax through as-is")
    p_search.add_argument(
        "--exchanges",
        action="store_true",
        help="same as --mode keyword (kept for scripts)",
    )
    p_search.add_argument(
        "--mode",
        choices=("hybrid", "keyword", "meaning", "prompts"),
        default="hybrid",
        help="hybrid (default): keyword + meaning, fused. keyword: matching words (BM25). "
        "meaning: similar meaning (local embeddings). prompts: your bare prompts only",
    )
    p_search.add_argument(
        "--blend",
        action="store_true",
        help="hybrid: favour recent exchanges and ones with friction",
    )
    p_search.add_argument("--tz", help="time zone for dates (default: this Mac's)")
    p_search.set_defaults(func=_cmd_search)

    p_score = sub.add_parser("score", help="specificity score + friction analysis of your prompts")
    p_score.add_argument("--tz", help="time zone for monthly trend (default: this Mac's)")
    p_score.add_argument("--json", action="store_true", help="print the raw JSON")
    p_score.set_defaults(func=_cmd_score)

    p_compare = sub.add_parser("compare", help="two periods side by side: are you improving?")
    p_compare.add_argument("period_a", help="e.g. this-month, 30d, 2026-10")
    p_compare.add_argument(
        "period_b", nargs="?", default="before", help="default: everything before period_a"
    )
    p_compare.add_argument("--tz", help="time zone for periods (default: this Mac's)")
    p_compare.add_argument("--json", action="store_true", help="print the raw JSON")
    p_compare.set_defaults(func=_cmd_compare)

    p_context = sub.add_parser(
        "context", help="build a working set (short-term memory) for a question and show it"
    )
    p_context.add_argument("question", nargs="+", help="what to gather evidence for")
    p_context.add_argument(
        "--budget", type=int, default=1500, help="token budget (estimate, default 1500)"
    )
    p_context.add_argument("--tz", help="time zone for dates (default: this Mac's)")
    p_context.add_argument("--json", action="store_true", help="print the manifest as JSON")
    p_context.set_defaults(func=_cmd_context)

    p_episodes = sub.add_parser(
        "episodes", help="one card per chat; with words, the chats about them (+ evidence)"
    )
    p_episodes.add_argument("query", nargs="*", help="optional: what the chat was about")
    p_episodes.add_argument("--limit", type=int, default=10)
    p_episodes.add_argument("--tz", help="time zone for dates (default: this Mac's)")
    p_episodes.set_defaults(func=_cmd_episodes)

    for p in (p_stats, p_kw, p_search, p_score, p_compare, p_context, p_episodes):
        if p is not p_compare:
            p.add_argument("--period", help=f"analysis window (default: all). {PERIOD_HELP}")
        if p is not p_search:
            p.add_argument("--project", help="only this project (exact name, else contains)")

    p_publish = sub.add_parser(
        "publish", help="build the Pompy Dashboard page + data in out/dashboard/ (local only)"
    )
    p_publish.add_argument("--tz", help="time zone for dates (default: this Mac's)")
    p_publish.set_defaults(func=_cmd_publish)

    p_embed = sub.add_parser(
        "embed", help="build the meaning index: local embeddings of new exchanges"
    )
    p_embed.add_argument("--quiet", action="store_true", help="print nothing (for hooks)")
    p_embed.set_defaults(func=_cmd_embed)

    p_eval = sub.add_parser("eval", help="measure retrieval: keyword vs meaning vs hybrid")
    p_eval.add_argument("file", nargs="?", help="eval set JSON (default: data/eval.json)")
    p_eval.add_argument("--k", type=int, default=5, help="how many results count (default 5)")
    p_eval.add_argument("--json", action="store_true", help="print the raw JSON")
    p_eval.set_defaults(func=_cmd_eval)

    p_check = sub.add_parser("check", help="score a draft prompt before you send it")
    p_check.add_argument("text", nargs="+", help="the draft prompt (quote it)")
    p_check.set_defaults(func=_cmd_check)

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
    elif hasattr(args, "func"):
        args.func(args)
    else:
        print(f"`pompy {args.command}` is not implemented yet.")


if __name__ == "__main__":
    main()
