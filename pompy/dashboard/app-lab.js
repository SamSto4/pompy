/* Pompy Dashboard, part 3: Memory Lab (working sets) and Compare (two periods). */
(function () {
  "use strict";
  const P = window.Pompy;
  const { h } = P;
  const CHARS_PER_TOKEN = 4;
  const SNIPPET_CHARS = 400;

  // ── Memory Lab ───────────────────────────────────────────────────────────
  P.views.memory = function (root) {
    const D = P.D;
    const chats = new Set(D.prompts.map((r) => r.si)).size;
    root.append(
      h("p", { class: "lede", text: "Pompy is an experiment in agent memory. Here is what it holds in each layer, and a working set (its context window) you can build and inspect." }),
      h("div", { class: "layers" },
        layer("Short-term", "the context window", `A working set: one question, a time window, and the few snippets that fit a token budget. Built fresh for each job, then thrown away.`),
        layer("Episodic", "what happened", `${D.meta.prompts} prompts from ${chats} chats, each with its time, project and what followed (Claude's replies stay on your Mac).`),
        layer("Long-term", "what it learned", `Patterns and habits distilled from the episodes: today the rubric stats and your top ${(D.habits || []).length} habits. Next: hypotheses with supporting and contradicting cases.`)),
      lastWorkingSet(D.working_set),
      builder());
  };

  function layer(name, tag, text) {
    return h("div", { class: "layer" }, h("span", { class: "tag", text: tag }), h("h3", { text: name }), h("p", { class: "small", text }));
  }

  function lastWorkingSet(ws) {
    if (!ws) {
      return P.panel("The last working set Pompy built", null,
        h("p", { class: "muted", text: "None yet. In Terminal, run pompy context \"your question\", then pompy publish, and this panel shows exactly what went in." }));
    }
    const used = ws.tokens_used_estimate, budget = ws.budget_tokens_estimate;
    const c = ws.counts || {};
    const reasons = {};
    (ws.left_out || []).forEach((x) => { const k = x.reason.replace(/\s*\(.*\)/, ""); reasons[k] = (reasons[k] || 0) + 1; });
    return P.panel("The last working set Pompy built", "from `pompy context` (SQLite FTS5 + BM25)",
      h("p", {}, "Question: ", h("b", { text: ws.question || "–" })),
      h("p", { class: "small muted mono", text: `window ${ws.window.label}${ws.window.project ? " · " + ws.window.project : ""} · ${c.prompts_in_window ?? "?"} of ${c.prompts_in_memory ?? "?"} prompts in view · ${c.candidates ?? "?"} candidates` }),
      budgetMeter(used, budget),
      h("h3", { text: `What went in (${ws.snippets.length})` }),
      h("div", {}, ws.snippets.map((sn) => wsItem(sn.rank, sn.text, `${sn.local_time} · ${sn.project} · ~${sn.tokens} tok`, false))),
      Object.keys(reasons).length ? h("h3", { text: `What was left out (${ws.left_out.length})` }) : null,
      Object.keys(reasons).length ? h("div", { class: "chips" }, Object.entries(reasons).map(([k, n]) => h("span", { class: "chip", text: `${n} × ${k}` }))) : null);
  }

  function budgetMeter(used, budget) {
    return h("div", {},
      h("div", { class: "meter", role: "img", "aria-label": `${used} of ${budget} tokens used` }, h("div", { style: `width:${Math.min(100, (100 * used) / Math.max(1, budget))}%` })),
      h("p", { class: "small muted mono", text: `~${used} of ~${budget} tokens used (estimate: 4 characters ≈ 1 token)` }));
  }

  function wsItem(rank, text, meta, out, reason) {
    return h("div", { class: "ws-item" + (out ? " out" : "") },
      h("span", { class: "rank", text: `#${rank}` }),
      h("div", {}, h("div", { class: "small muted mono", text: meta }), h("div", { text: text.length > 220 ? text.slice(0, 219) + "…" : text })),
      out ? h("span", { class: "chip fr", text: reason }) : h("span", { class: "chip ok", text: "in" }));
  }

  // An in-page approximation of `pompy context`: rank by shared topic words, weighted by how
  // rare each word is (no stemming, no BM25 length normalization), then fill the budget.
  function builder() {
    const q = h("input", { class: "text", id: "ml-q", type: "text", value: "nav bar mobile layout", "aria-label": "Question", style: "flex:1 1 240px" });
    let period = "all";
    const per = P.periodSelect("ml-period", period, (v) => { period = v; run(); });
    const budget = h("input", { type: "range", id: "ml-budget", min: 100, max: 3000, step: 50, value: 600, "aria-label": "Token budget" });
    const cap = h("input", { type: "range", id: "ml-cap", min: 1, max: 5, step: 1, value: 2, "aria-label": "Snippets per chat" });
    const budgetLbl = h("span", { class: "mono small" });
    const capLbl = h("span", { class: "mono small" });
    const out = h("div", {});
    [q, budget, cap].forEach((e) => e.addEventListener("input", run));

    const df = new Map();
    const docs = P.D.prompts.filter((r) => r.k === "typed").map((r) => {
      const terms = new Set(P.S.contentTerms(r.t));
      terms.forEach((t) => df.set(t, (df.get(t) || 0) + 1));
      return { r, terms };
    });
    const N = docs.length;

    function run() {
      budgetLbl.textContent = `budget ~${budget.value} tokens`;
      capLbl.textContent = `max ${cap.value} per chat`;
      const want = new Set(P.S.contentTerms(q.value));
      const test = P.period(period).test;
      const inView = docs.filter((d) => test(d.r));
      const cands = [];
      for (const d of inView) {
        let sc = 0;
        want.forEach((t) => { if (d.terms.has(t)) sc += Math.log(1 + N / df.get(t)); });
        if (sc > 0) cands.push([sc, d.r]);
      }
      cands.sort((a, b) => b[0] - a[0]);
      const top = cands.slice(0, 25);
      let used = 0;
      const per = {};
      const items = [];
      top.forEach(([, r], i) => {
        const text = r.t.length > SNIPPET_CHARS ? r.t.slice(0, SNIPPET_CHARS - 1) + "…" : r.t;
        const tok = Math.max(1, Math.ceil(text.length / CHARS_PER_TOKEN));
        const meta = `${r.d} ${r.hh} · ${r.p} · chat ${r.si} · ~${tok} tok`;
        if ((per[r.si] || 0) >= +cap.value) items.push(wsItem(i + 1, text, meta, true, `chat already has ${cap.value}`));
        else if (used + tok > +budget.value) items.push(wsItem(i + 1, text, meta, true, `over budget`));
        else { used += tok; per[r.si] = (per[r.si] || 0) + 1; items.push(wsItem(i + 1, text, meta, false)); }
      });
      out.textContent = "";
      out.append(
        h("p", { class: "small muted mono", text: `${inView.length} of ${N} typed prompts in view · ${cands.length} share a topic word · top ${top.length} considered` }),
        budgetMeter(used, +budget.value),
        want.size ? null : h("p", { class: "muted", text: "Type a question with at least one topic word (not just verbs or stopwords)." }),
        h("div", {}, items),
        cands.length > top.length ? h("p", { class: "small muted", text: `${cands.length - top.length} weaker matches were never considered (only the top 25 are).` }) : null);
    }
    run();
    return P.panel("Build a working set", "in-page approximation",
      h("p", { class: "small muted", text: "Change the question, window, budget or per-chat cap and watch what gets into the context window and what is left out. This ranks by shared topic words weighted by rarity; `pompy context` uses SQLite full-text search with stemming and BM25, so its picks can differ." }),
      h("div", { class: "controls" }, q, per),
      h("div", { class: "controls" }, budget, budgetLbl, cap, capLbl),
      out);
  }

  // ── Compare ──────────────────────────────────────────────────────────────
  const ROWS = () => [
    ["typed", "Typed prompts", null, false],
    ["median_words", "Median words you wrote", null, false],
    ["score_mean", "Specificity, mean (0–7)", true, false],
    ["openers_mean", "session openers", true, true],
    ["follow_ups_mean", "follow-ups", true, true],
    ...P.D.rules.features.map((f) => ["has_" + f.name, `with ${f.label.toLowerCase()} (%)`, true, true]),
    ["friction_rate", "Friction after tasks (%)", false, false],
    ["clarify_rate", "Claude had to ask (%)", false, true],
    ["peak_hour", "Peak hour", null, false],
  ];

  P.views.compare = function (root) {
    let a = "this-month", b = "before";
    const table = h("div", { class: "scroll-x" });
    const warn = h("div", {});
    const selA = P.periodSelect("cmp-a", a, (v) => { a = v; draw(); });
    const selB = P.periodSelect("cmp-b", b, (v) => { b = v; draw(); });
    root.append(
      h("p", { class: "lede", text: "Two windows over the same memory, side by side: are you improving? The numbers match `pompy compare` (same per-prompt verdicts, same definitions)." }),
      h("div", { class: "panel" },
        h("div", { class: "controls" }, h("label", { for: "cmp-a", class: "small muted", text: "Compare" }), selA, h("label", { for: "cmp-b", class: "small muted", text: "with" }), selB),
        table, warn));

    function draw() {
      const pa = P.period(a), pb = P.period(b);
      const ma = P.metrics(P.D.prompts.filter(pa.test));
      const mb = P.metrics(P.D.prompts.filter(pb.test));
      const t = h("table", { class: "cmp" },
        h("thead", {}, h("tr", {}, h("th", { text: "" }), h("th", { text: pa.label }), h("th", { text: pb.label }), h("th", { text: "Change" }))));
      const body = h("tbody", {});
      for (const [key, label, better, indent] of ROWS()) {
        const x = ma[key], y = mb[key];
        const delta = x != null && y != null && key !== "peak_hour" ? P.round(x - y, 2) : null;
        let cls = "", word = "";
        if (delta && better != null) {
          const good = delta > 0 === better;
          cls = good ? "better" : "worse";
          word = good ? " ▲ better" : " ▼ worse";
        }
        body.append(h("tr", {},
          h("td", { class: indent ? "indent" : "", text: label }),
          h("td", { class: "num", text: P.fmt(x) }), h("td", { class: "num", text: P.fmt(y) }),
          h("td", { class: "num " + cls, text: delta ? (delta > 0 ? "+" : "") + delta + word : "" })));
      }
      t.append(body);
      table.textContent = "";
      table.append(t);
      warn.textContent = "";
      for (const [p, m] of [[pa, ma], [pb, mb]]) {
        if (m.tasks < 15) warn.append(h("p", { class: "thin", text: `${p.label}: only ${m.tasks} task prompts (under 15), so its rates are thin evidence.` }));
      }
    }
    draw();
  };
})();
