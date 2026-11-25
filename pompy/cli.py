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
    if ws.window.all_time:
        from pompy import preflight

        preflight.write_profile(preflight.build_profile(result), config.out_dir())


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
    from pompy import preflight

    preflight.write_profile(preflight.build_profile(bundle["score"]), config.out_dir())
    print(f"Built the Pompy Dashboard in {out}")
    print(f"  {bundle['meta']['prompts']} prompts, data bundle {size:.0f} KB")
    print("  Nothing has left your Mac. Ask Claude to publish it to your private artifact.")


def _cmd_preflight(args: argparse.Namespace) -> None:
    from pompy import preflight

    flag = config.data_dir() / "preflight_off"
    if args.off or args.on:
        if args.off:
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.touch()
        else:
            flag.unlink(missing_ok=True)
        print(f"Pompy Pre-flight is {'off' if args.off else 'on'} in every session.")
        return
    if args.bench:
        import json
        import subprocess
        import sys

        event = json.dumps({"prompt": "fix the nav bar on mobile", "session_id": "bench"})
        times = []
        for _ in range(args.bench):
            start = time.perf_counter()
            subprocess.run(
                [sys.executable, "-m", "pompy.cli", "preflight"],
                input=event,
                capture_output=True,
                text=True,
                env={**__import__("os").environ, "POMPY_DATA": str(config.data_dir() / "bench")},
            )
            times.append((time.perf_counter() - start) * 1000)
        times.sort()
        p95 = times[min(len(times) - 1, int(len(times) * 0.95))]
        print(f"pompy preflight, {len(times)} runs as a fresh process (what the hook pays):")
        print(f"  median {times[len(times) // 2]:.0f} ms · p95 {p95:.0f} ms · budget 100 ms")
        return
    preflight.main_hook()


def _cmd_similar(args: argparse.Namespace) -> None:
    import json
    import sys

    from pompy import excerpts, hybrid, similar

    text = " ".join(args.text) if args.text else sys.stdin.read()
    if not text.strip():
        raise SystemExit('Give a prompt: pompy similar "fix the nav bar" (or pipe it in).')
    conn = _open_store()
    if not excerpts.count(conn):
        conn.close()
        raise SystemExit("No exchange excerpts yet. Run `pompy ingest` first.")
    import os

    from pompy import advice, facts

    embedder = None if args.keyword else hybrid.default_embedder(conn)
    # The folder the prompt was sent from: --cwd, else Claude Code's project folder, else here.
    cwd = args.cwd or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    result = similar.similar(conn, text, embedder, limit=args.limit, cwd=cwd)
    project_key = facts.project_key_for(conn, cwd)
    conn.close()
    fingerprint = advice.digest(text)
    result = similar.advise(result, fingerprint, advice.load_profile(config.out_dir()))
    if not args.no_log and result.get("role") == "task":
        row = similar.log_row(result, fingerprint, args.session, project_key, time.time())
        advice.log(config.data_dir(), row)
    print(json.dumps(result) if args.json else similar.render(result))


def _cmd_impact(args: argparse.Namespace) -> None:
    import json

    from pompy import advice

    rows = advice.read_log(config.data_dir())
    conn = _open_store()
    result = advice.impact(conn, rows)
    conn.close()
    path = advice.write_profile(advice.profile(result), config.out_dir())
    if args.json:
        print(json.dumps(result))
        return
    print(advice.render(result))
    print(f"\nProcedural memory written to {path.name} (the notch reads it to pick tips).")


def _cmd_refresh(args: argparse.Namespace) -> None:
    """The warm path in one command: new lines in, meaning index and profile up to date."""
    import contextlib
    import importlib.util
    import io

    from pompy import embed, preflight, score
    from pompy.ingest import ingest

    start = time.perf_counter()
    report = ingest(config.source_dir(), config.data_dir())
    conn = _open_store()
    embedded = 0
    if importlib.util.find_spec("fastembed") is not None:
        with contextlib.redirect_stderr(io.StringIO()):  # the model's progress bars
            embedded = embed.update(conn, embed.FastEmbedder())["embedded"]
    preflight.write_profile(preflight.build_profile(score.compute(conn)), config.out_dir())
    from pompy import advice  # procedural memory: re-learn which tips you act on

    advice.write_profile(
        advice.profile(advice.impact(conn, advice.read_log(config.data_dir()))), config.out_dir()
    )
    conn.close()
    if not args.quiet:
        print(
            f"Pompy refreshed in {time.perf_counter() - start:.1f}s: "
            f"{report.new['prompt']} new prompts, {report.excerpts} exchanges, "
            f"{embedded} newly embedded."
        )


def _cmd_themes(args: argparse.Namespace) -> None:
    import contextlib
    import io
    import json

    from pompy import excerpts, hybrid, themes

    conn = _open_store()
    if not excerpts.count(conn):
        conn.close()
        raise SystemExit("No exchange excerpts yet. Run `pompy ingest` first.")
    embedder = hybrid.default_embedder(conn)
    if embedder is None:
        conn.close()
        raise SystemExit(
            'Themes need the meaning model: pip install -e ".[memory]", then pompy embed.'
        )
    with contextlib.redirect_stderr(io.StringIO()):
        result = themes.compute(conn, embedder, k=args.k)
    conn.close()
    out = config.out_dir()
    out.mkdir(parents=True, exist_ok=True)
    (out / "themes.json").write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1) if args.json else themes.render(result))


def _cmd_mine(args: argparse.Namespace) -> None:
    from zoneinfo import ZoneInfo

    from pompy import excerpts, mine

    conn = _open_store()
    if not excerpts.count(conn):
        conn.close()
        raise SystemExit("No exchange excerpts yet. Run `pompy ingest` first.")
    result = mine.mine(conn, ZoneInfo(args.tz) if args.tz else None)
    conn.close()
    print(mine.render(result, show=len(result["results"]) if args.all else 12))


def _cmd_patterns(args: argparse.Namespace) -> None:
    from pompy import patterns

    conn = _open_store()
    found = patterns.load(conn, status=None if args.all else "supported")
    conn.close()
    if not found:
        print("No supported patterns yet. Run `pompy mine` (or add --all to see every hypothesis).")
    for p in found[: args.limit]:
        s = p["stats"]
        print(f"[{p['status']}] {p['statement']}")
        if p["source"] == "agent":
            print(
                f"  from pompy think: {len(p['supports'])} supporting and "
                f"{len(p['contradicts'])} contradicting exchanges; not yet tested by "
                f"statistics (since {p['first_seen'][:10]})"
            )
            if s.get("rationale"):
                print(f"  why: {s['rationale'][:200]}")
        else:
            print(
                f"  {s['rate_a']:.0%} vs {s['rate_b']:.0%} went wrong "
                f"(n = {s['n_a']} vs {s['n_b']}), q {s['q']:.3f}; "
                f"confidence now {p['confidence_now']:.2f}, "
                f"checked {len(p['history'])} time(s), since {p['first_seen'][:10]}"
            )
        for label, items in (("supports", p["supports"]), ("contradicts", p["contradicts"])):
            for e in items[: args.evidence]:
                sign = "+" if label == "supports" else "-"
                print(f"    {sign} {e['prompt'][:100]}  → {e['outcome']}")
        print()


def _cmd_think(args: argparse.Namespace) -> None:
    import contextlib
    import io
    import os

    from pompy import excerpts, hybrid, think

    conn = _open_store()
    if not excerpts.count(conn):
        conn.close()
        raise SystemExit("No exchange excerpts yet. Run `pompy ingest` first.")
    if args.dry_run:
        print("── Instructions (cached) ──\n" + think.SYSTEM)
        print("\n── Briefing ──\n" + think.briefing(conn))
        print("\n── Tools ──\n" + ", ".join(t["name"] for t in think.TOOLS))
        print(f"\nModel {args.model}, effort {args.effort}. Dry run: nothing was sent.")
        conn.close()
        return
    think.load_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        conn.close()
        raise SystemExit(
            "No ANTHROPIC_API_KEY. Put ANTHROPIC_API_KEY=... in the git-ignored .env file "
            "in this folder, then run `pompy think` again."
        )
    try:
        import anthropic
    except ImportError:
        conn.close()
        raise SystemExit('pompy think needs the SDK: pip install -e ".[agent]"') from None
    embedder = hybrid.default_embedder(conn)
    print(f"Thinking with {args.model} (effort {args.effort}); excerpts go to the Claude API...")
    with contextlib.redirect_stderr(io.StringIO()):  # the embedding model's progress bars
        run = think.think(conn, anthropic.Anthropic(), embedder, args.model, args.effort)
    conn.close()
    print(think.render(run, args.model))


def _api_client():
    """An Anthropic client, or exit with how to set up the key."""
    import os

    from pompy import think

    think.load_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("No ANTHROPIC_API_KEY (put it in the git-ignored .env, or export it).")
    try:
        import anthropic
    except ImportError:
        raise SystemExit('Needs the SDK: pip install -e ".[agent]"') from None
    return anthropic.Anthropic()


def _cmd_facts(args: argparse.Namespace) -> None:
    from pompy import facts

    conn = _open_store()
    found = facts.projects(conn)
    if args.dry_run:
        for p in found:
            print(facts.brief(p) + "\n")
        print(f"{len(found)} projects would be summarised by {args.model}. Dry run: nothing sent.")
        conn.close()
        return
    client = _api_client()
    result = facts.refresh(conn, client, args.model)
    conn.close()
    for p in result["projects"]:
        print(f"{p['project']}: {p['goal']}")
        for pref in p["preferences"]:
            print(f"  · {pref}")
    u = result["usage"]
    print(f"\n{len(result['projects'])} projects, {u['input']} tokens in, {u['output']} out.")


def _cmd_consolidate(args: argparse.Namespace) -> None:
    from zoneinfo import ZoneInfo

    from pompy import consolidate, facts

    conn = _open_store()
    client = _api_client() if args.facts else None
    result = consolidate.consolidate(
        conn,
        ZoneInfo(args.tz) if args.tz else None,
        facts_client=client,
        facts_model=facts.DEFAULT_MODEL,
    )
    conn.close()
    print(consolidate.render(result))


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

    p_pre = sub.add_parser(
        "preflight",
        help="the Pre-flight hook: reads a prompt as JSON on stdin, holds vague ones (fails open)",
    )
    p_pre.add_argument("--off", action="store_true", help="turn Pre-flight off everywhere")
    p_pre.add_argument("--on", action="store_true", help="turn it back on")
    p_pre.add_argument("--bench", type=int, metavar="N", help="time N runs as a fresh process")
    p_pre.set_defaults(func=_cmd_preflight)

    p_similar = sub.add_parser(
        "similar", help="past exchanges like a prompt, and what happened after them"
    )
    p_similar.add_argument("text", nargs="*", help="the prompt (or pipe it in on stdin)")
    p_similar.add_argument("--limit", type=int, default=5)
    p_similar.add_argument("--keyword", action="store_true", help="skip the meaning model")
    p_similar.add_argument("--json", action="store_true", help="print the raw JSON")
    p_similar.add_argument("--cwd", help="folder the prompt was sent from (for project advice)")
    p_similar.add_argument("--session", help="Claude Code session id, kept in the advice log")
    p_similar.add_argument("--no-log", action="store_true", help="don't add to the advice log")
    p_similar.set_defaults(func=_cmd_similar)

    p_impact = sub.add_parser(
        "impact", help="did Pompy's tips help? shown vs held back; which tips you act on"
    )
    p_impact.add_argument("--json", action="store_true", help="print the raw JSON")
    p_impact.set_defaults(func=_cmd_impact)

    p_refresh = sub.add_parser(
        "refresh", help="ingest new chats, embed new exchanges, update the profile (warm path)"
    )
    p_refresh.add_argument("--quiet", action="store_true", help="print nothing (for hooks)")
    p_refresh.set_defaults(func=_cmd_refresh)

    p_themes = sub.add_parser("themes", help="cluster your task prompts into recurring themes")
    p_themes.add_argument("--k", type=int, help="number of themes (default: chosen by silhouette)")
    p_themes.add_argument("--json", action="store_true", help="print the raw JSON")
    p_themes.set_defaults(func=_cmd_themes)

    p_mine = sub.add_parser("mine", help="test hypotheses about your prompts; keep what holds")
    p_mine.add_argument("--tz", help="time zone for time-of-day claims (default: this Mac's)")
    p_mine.add_argument("--all", action="store_true", help="list every claim tested")
    p_mine.set_defaults(func=_cmd_mine)

    p_patterns = sub.add_parser("patterns", help="what Pompy has learned, with evidence")
    p_patterns.add_argument("--all", action="store_true", help="include unproven and retired")
    p_patterns.add_argument("--limit", type=int, default=10)
    p_patterns.add_argument("--evidence", type=int, default=2, help="excerpts to show per side")
    p_patterns.set_defaults(func=_cmd_patterns)

    p_think = sub.add_parser(
        "think", help="a Claude agent looks for patterns, with evidence on both sides (API key)"
    )
    p_think.add_argument("--dry-run", action="store_true", help="show what would be sent")
    p_think.add_argument("--model", default="claude-opus-5-5")
    p_think.add_argument("--effort", default="high", choices=("low", "medium", "high", "xhigh"))
    p_think.set_defaults(func=_cmd_think)

    p_facts = sub.add_parser("facts", help="each project's goal and your preferences (API key)")
    p_facts.add_argument("--dry-run", action="store_true", help="show what would be sent")
    p_facts.add_argument("--model", default="claude-haiku-5-5")
    p_facts.set_defaults(func=_cmd_facts)

    p_cons = sub.add_parser(
        "consolidate", help="re-check patterns, fade and retire stale ones, report changes"
    )
    p_cons.add_argument("--facts", action="store_true", help="also refresh project facts (API)")
    p_cons.add_argument("--tz", help="time zone for time-of-day claims (default: this Mac's)")
    p_cons.set_defaults(func=_cmd_consolidate)

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
