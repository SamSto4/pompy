/* Pompy Dashboard, part 3: Memory Lab (working sets) and Compare (two periods). */
(function () {
  "use strict";
  const P = window.Pompy;
  const { h } = P;
  const CHARS_PER_TOKEN = 4;
  const SNIPPET_CHARS = 400;

  // ── Memory Lab: what Pompy remembers, and one box to query it ───────────
  P.views.memory = function (root) {
    const D = P.D;
    const beliefs = ((D.learned || {}).patterns || []);
    const held = beliefs.filter((p) => p.status === "supported");
    root.append(
      h("div", { class: "page-head" }, h("span", { class: "eyebrow", text: "Memory lab" }), h("h1", { text: "How Pompy remembers you" }),
        h("p", { class: "lede", text: "Three layers of agent memory, built from your own chats. Query the short-term layer below and watch a long-term belief fade." })),
      h("div", { class: "layers" },
        layer("Short-term", "~600 tokens", "What it can hold for one question. Try it below."),
        layer("Episodic", `${D.meta.prompts} prompts`, "Everything you sent, and what happened after."),
        layer("Long-term", `${held.length} beliefs`, beliefs.length ? `Plus ${beliefs.length - held.length} ideas still being tested. See below.` : "Nothing learned yet.")),
      askMemory(),
      watchBelief(beliefs));
  };

  // Long-term memory over time: pick a belief, watch it form and fade.
  function watchBelief(beliefs) {
    if (!beliefs.length || !P.beliefChart) return null;
    const order = [...beliefs].sort((a, b) => b.c - a.c);
    const sel = h("select", { class: "sel", "aria-label": "Belief" }, order.map((p, i) =>
      h("option", { value: i, text: (p.kind === "goal" ? "Project: " : "") + (p.s.length > 90 ? p.s.slice(0, 89) + "…" : p.s) })));
    const out = h("div", {});
    const draw = () => { out.textContent = ""; out.append(P.beliefChart(order[+sel.value])); };
    sel.addEventListener("change", draw);
    draw();
    return P.panel("Watch a belief fade", `forgetting, on a ${(P.D.learned || {}).half_life_days || 90} day half life`, h("div", { class: "controls" }, sel), out);
  }

  function layer(name, fact, text) {
    return h("div", { class: "layer" }, h("span", { class: "tag", text: name }), h("div", { class: "layer-fact", text: fact }), h("p", { class: "small muted", text }));
  }

  // Ranks your past prompts by shared topic words (rarer words count more), then keeps what fits
  // the room, at most 2 per chat: the same shape as `pompy context`, simplified.
  function askMemory() {
    const q = h("input", { class: "text", type: "search", placeholder: "Ask your memory, e.g. nav bar on mobile", "aria-label": "Question", style: "flex:1 1 260px" });
    const budget = h("input", { type: "range", min: 100, max: 3000, step: 50, value: 600, "aria-label": "Room in tokens" });
    const room = h("span", { class: "small muted" });
    const out = h("div", {});
    [q, budget].forEach((e) => e.addEventListener("input", run));

    const df = new Map();
    const docs = P.D.prompts.filter((r) => r.k === "typed").map((r) => {
      const terms = new Set(P.S.contentTerms(r.t));
      terms.forEach((t) => df.set(t, (df.get(t) || 0) + 1));
      return { r, terms };
    });
    const N = docs.length;

    function run() {
      room.textContent = `Room: ~${budget.value} tokens`;
      out.textContent = "";
      const want = new Set(P.S.contentTerms(q.value));
      if (!want.size) { out.append(h("p", { class: "muted small", text: "Type a topic to see which past prompts Pompy would pull into its short-term memory." })); return; }
      const cands = [];
      for (const d of docs) {
        let sc = 0;
        want.forEach((t) => { if (d.terms.has(t)) sc += Math.log(1 + N / df.get(t)); });
        if (sc > 0) cands.push([sc, d.r]);
      }
      cands.sort((a, b) => b[0] - a[0]);
      let used = 0;
      const per = {};
      const kept = [];
      for (const [, r] of cands.slice(0, 25)) {
        const text = r.t.length > SNIPPET_CHARS ? r.t.slice(0, SNIPPET_CHARS - 1) + "…" : r.t;
        const tok = Math.max(1, Math.ceil(text.length / CHARS_PER_TOKEN));
        if ((per[r.si] || 0) >= 2 || used + tok > +budget.value) continue;
        used += tok;
        per[r.si] = (per[r.si] || 0) + 1;
        kept.push([r, text]);
      }
      out.append(
        h("div", { class: "meter", role: "img", "aria-label": `${used} of ${budget.value} tokens used` }, h("div", { style: `width:${Math.min(100, (100 * used) / +budget.value)}%` })),
        h("p", { class: "small muted", text: cands.length ? `${kept.length} of ${cands.length} matching prompts fit. The rest were left out.` : "Nothing in memory matches." }),
        h("div", { class: "plist" }, kept.map(([r, text]) =>
          h("div", { class: "prow static" }, h("div", {}, h("div", { class: "txt", text }), h("div", { class: "meta", text: `${P.when(r)} · ${r.p}` })), h("div", { class: "right" }, P.scorePill(r))))));
    }
    run();
    return P.panel("Ask your memory", null, h("div", { class: "controls" }, q), h("div", { class: "controls" }, budget, room), out);
  }

  // ── Compare ──────────────────────────────────────────────────────────────
  const ROWS = () => [
    ["typed", "Typed prompts", null, false],
    ["median_words", "Median words you wrote", null, false],
    ["score_mean", "Prompt Score, mean (out of 10)", true, false],
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
      // Scores read out of 10, like everywhere else (the same 7 checks, scaled).
      for (const m of [ma, mb]) for (const k of ["score_mean", "openers_mean", "follow_ups_mean"]) if (m[k] != null) m[k] = P.round((m[k] * 10) / 7, 1);
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
