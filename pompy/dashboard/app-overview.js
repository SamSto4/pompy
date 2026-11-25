/* Pompy Dashboard, Overview: did Claude's replies give you what you wanted, and when they
 * didn't, why. Every verdict and cause is decided in Python (pompy/outcomes.py); this page only
 * counts them and lets you read the evidence. */
(function () {
  "use strict";
  const P = window.Pompy;
  const { h } = P;
  const RECENT = 20;
  const MIN_PROJECT = 5; // task prompts a project needs before its rates are shown
  const V = {
    useful: { label: "Useful", cls: "ok", tip: "You said it worked, thanked Claude, or asked to commit or ship it." },
    "moved on": { label: "Moved on", cls: "", tip: "You went on to something else without pushing back. Probably fine, but Pompy can't be sure." },
    unclear: { label: "Claude had to ask", cls: "fr", tip: "Claude stopped to ask what you meant." },
    missed: { label: "Missed", cls: "fr", tip: "You corrected Claude, asked again, stopped it, or said something was still wrong." },
    "no reaction": { label: "No reaction", cls: "", tip: "The chat ended there, so there is nothing to judge." },
  };
  const ORDER = ["useful", "moved on", "unclear", "missed", "no reaction"];
  const COLOR = { useful: "var(--good)", "moved on": "var(--accent)", unclear: "var(--purple)", missed: "var(--note)", "no reaction": "var(--line)" };
  const bad = (r) => r.v === "unclear" || r.v === "missed";
  const judged = (rows) => rows.filter((r) => r.v && r.v !== "no reaction");
  const pctBad = (rows) => { const j = judged(rows); return j.length ? Math.round((100 * j.filter(bad).length) / j.length) : null; };
  // The part a bad reply's prompt lacked, from its cause ("The prompt never said X.").
  const lacked = (r) => (r.c && /never said (.*)\.$/.exec(r.c[1]) || [])[1] || null;
  const top = (xs) => { const n = new Map(); xs.filter(Boolean).forEach((x) => n.set(x, (n.get(x) || 0) + 1)); return [...n].sort((a, b) => b[1] - a[1])[0] || null; };

  P.views.overview = function (root) {
    const tasks = P.D.prompts.filter((r) => r.v);
    root.append(
      h("p", { class: "lede", text: "Did Claude give you what you wanted? Pompy reads how you reacted to each reply, and when a reply missed, works out what the prompt was missing." }),
      outcomesPanel(tasks),
      recentPanel(tasks),
      projectsPanel(tasks),
      setupPanel(tasks),
      P.dayTimeline());
  };

  // ── How replies went ──────────────────────────────────────────────────────
  function outcomesPanel(all) {
    let period = "30d";
    const box = h("div", {});
    const sel = P.periodSelect("oc-period", period, (v) => { period = v; draw(); });
    function draw() {
      const rows = all.filter(P.period(period).test);
      box.textContent = "";
      if (!rows.length) { box.append(h("p", { class: "muted", text: "No task prompts in this window." })); return; }
      const n = Object.fromEntries(ORDER.map((v) => [v, rows.filter((r) => r.v === v).length]));
      const bar = h("div", { class: "stack", role: "img", "aria-label": ORDER.map((v) => `${V[v].label}: ${n[v]}`).join(", ") },
        ORDER.filter((v) => n[v]).map((v) => h("div", { title: `${V[v].label}: ${n[v]}. ${V[v].tip}`, style: `flex:${n[v]};background:${COLOR[v]}` })));
      const legend = h("div", { class: "chips small" }, ORDER.map((v) =>
        h("span", { class: "legend", title: V[v].tip }, h("i", { class: "dot sq", style: `background:${COLOR[v]}` }), `${V[v].label} ${n[v]}`)));
      const why = top(rows.filter(bad).map(lacked));
      box.append(
        h("p", { class: "big-line" }, h("b", { text: `${pctBad(rows) ?? 0}%` }), " of the replies you reacted to missed or needed a question."),
        bar, legend,
        why ? h("p", { class: "note", text: `Most common reason: the prompt never said ${why[0]} (${why[1]} times).` }) : null,
        h("p", { class: "small muted", text: "Moved on means you carried on without pushing back. Pompy counts it as fine but can't be sure it was what you wanted." }));
    }
    draw();
    return P.panel("How Claude's replies went", "task prompts only", h("div", { class: "controls" }, sel), box);
  }

  // ── Your last prompts ─────────────────────────────────────────────────────
  function recentPanel(all) {
    let onlyBad = false;
    const list = h("div", { class: "plist" });
    const tabs = h("div", { class: "seg-tabs", role: "tablist" });
    const tab = (label, val) => h("button", { class: "seg-tab", role: "tab", "aria-selected": String(onlyBad === val), text: label,
      onclick: () => { onlyBad = val; [...tabs.children].forEach((b, i) => b.setAttribute("aria-selected", String(i === (val ? 1 : 0)))); draw(); } });
    tabs.append(tab("All", false), tab("Missed or unclear", true));
    function draw() {
      list.textContent = "";
      const rows = all.filter((r) => !onlyBad || bad(r)).slice(-RECENT).reverse();
      if (!rows.length) list.append(h("p", { class: "muted", text: "Nothing here." }));
      rows.forEach((r) => list.append(promptRow(r)));
    }
    draw();
    return P.panel(`Your last ${RECENT} prompts`, "click one to see what it was missing", h("div", { class: "controls" }, tabs), list);
  }

  function promptRow(r) {
    const v = V[r.v];
    const row = h("div", { class: "prow" },
      h("div", {}, h("div", { class: "txt", text: r.t }), h("div", { class: "meta", text: `${P.when(r)} · ${r.p}` })),
      h("div", { class: "right" }, h("span", { class: "chip " + v.cls, title: v.tip, text: v.label }), P.scorePill(r)));
    let open = null;
    row.addEventListener("click", (ev) => {
      if (ev.target.closest(".detail")) return;
      if (open) { open.remove(); open = null; return; }
      open = detail(r);
      row.append(open);
    });
    return row;
  }

  function detail(r) {
    const has = new Set(r.f);
    const parts = h("div", { class: "checks" }, P.D.rules.features.map((f) => h("span", { class: has.has(f.name) ? "yes" : "no", text: f.label })));
    const out = [h("div", { class: "full", text: r.t }), parts];
    if (r.c) {
      out.push(h("h3", { text: "Why it went wrong" }), h("ol", { class: "steps" }, r.c.map((x) => h("li", { text: x }))));
      if (r.rp) out.push(h("div", { class: "small muted", text: "Claude replied" }), h("div", { class: "quote", text: r.rp }));
      if (r.nx) out.push(h("div", { class: "small muted", text: "Then you said" }), h("div", { class: "quote", text: r.nx }));
      out.push(explainBlock(r));
    }
    out.push(h("div", { class: "controls" }, h("button", { class: "btn", text: "Write a better version",
      onclick: () => { P.prefill = r.t; location.hash = "ask"; } })));
    return h("div", { class: "detail" }, out);
  }

  // Claude reasons over the same evidence, step by step, when you want more than the rule's answer.
  function explainBlock(r) {
    const outEl = h("div", { class: "quote after hidden" });
    const status = h("span", { class: "muted small" });
    const btn = h("button", { class: "btn primary", text: "Explain step by step" });
    btn.addEventListener("click", async () => {
      const sample = await P.cap("sample");
      if (!sample) { status.textContent = "Asking Claude isn't available in this view."; return; }
      btn.disabled = true;
      outEl.classList.remove("hidden");
      status.textContent = "Claude is reasoning…";
      const ask = [
        "You are Pompy, a prompting coach. A developer sent the prompt below to Claude Code and was not happy",
        "with the reply. Using deductive reasoning and ONLY the evidence given, explain in 3 to 5 short numbered",
        "steps what the prompt left Claude to guess, what Claude guessed, and why that was not what the developer",
        "wanted. Then give one line starting 'Next time:' with the exact words to add. No preamble.",
        "", "Prompt:", r.t, "", "Claude's reply (start):", r.rp || "(not recorded)", "",
        "What the developer said next:", r.nx || "(nothing)", "", "Pompy's own guess:", r.c.join(" "),
      ].join("\n");
      try {
        const res = await sample(ask, { onText: ({ text }) => (outEl.textContent = text) });
        outEl.textContent = res.text.trim();
        status.textContent = "";
      } catch (e) {
        status.textContent = "Claude couldn't answer (" + ((e && (e.code || e.message)) || "error") + ").";
      }
      btn.disabled = false;
    });
    return h("div", {}, h("div", { class: "controls" }, btn, status), outEl);
  }

  // ── By project ────────────────────────────────────────────────────────────
  function projectsPanel(all) {
    const by = new Map();
    all.forEach((r) => { if (!by.has(r.p)) by.set(r.p, []); by.get(r.p).push(r); });
    const rows = [...by].filter(([, rs]) => judged(rs).length >= MIN_PROJECT)
      .map(([p, rs]) => ({ p, rs, bad: pctBad(rs), why: top(rs.filter(bad).map(lacked)) }))
      .sort((a, b) => b.bad - a.bad);
    if (!rows.length) return null;
    const t = h("table", { class: "cmp" },
      h("thead", {}, h("tr", {}, ["Project", "Prompts", "Missed or unclear", "Usually missing"].map((x) => h("th", { text: x })))),
      h("tbody", {}, rows.map((x) => h("tr", {},
        h("td", { text: x.p.replace(/[-_]+/g, " ") }),
        h("td", { class: "num", text: judged(x.rs).length }),
        h("td", { class: "num " + (x.bad >= 25 ? "worse" : ""), text: `${x.bad}%` }),
        h("td", { class: "small", text: x.why ? x.why[0] : "nothing stands out" })))));
    return P.panel("By project", `projects with ${MIN_PROJECT} or more judged prompts`, h("div", { class: "scroll-x" }, t));
  }

  // ── Your setup ────────────────────────────────────────────────────────────
  function setupPanel(all) {
    const table = (head, groups) => h("table", { class: "cmp" },
      h("thead", {}, h("tr", {}, [head, "Prompts", "Missed or unclear"].map((x) => h("th", { text: x })))),
      h("tbody", {}, groups.filter(([, rs]) => judged(rs).length >= 3).map(([name, rs]) => h("tr", {},
        h("td", { text: name }), h("td", { class: "num", text: judged(rs).length }), h("td", { class: "num", text: `${pctBad(rs)}%` })))));
    const group = (key) => { const m = new Map(); all.forEach((r) => { const k = key(r); if (k == null) return; if (!m.has(k)) m.set(k, []); m.get(k).push(r); }); return [...m].sort((a, b) => b[1].length - a[1].length); };

    // Position of each task prompt within its chat: do long chats drift?
    const pos = new Map(), seen = new Map();
    P.D.prompts.forEach((r) => { if (r.k !== "typed") return; const n = (seen.get(r.si) || 0) + 1; seen.set(r.si, n); pos.set(r.u, n); });
    const BUCKETS = [[1, 5, "prompts 1 to 5"], [6, 15, "prompts 6 to 15"], [16, 40, "prompts 16 to 40"], [41, 1e9, "prompt 41 on"]];
    const byPos = BUCKETS.map(([a, b, label]) => [label, all.filter((r) => pos.get(r.u) >= a && pos.get(r.u) <= b)]);
    const early = pctBad(byPos[0][1]), mid = pctBad(byPos[1][1]), late = pctBad([...byPos[2][1], ...byPos[3][1]]);
    const advice = [];
    if (early != null && mid != null && early - mid >= 10)
      advice.push(`The first prompts of a chat miss most (${early}%, vs ${mid}% a few prompts in): Claude starts with no context. Open a chat with where you are and what you want, not just the task.`);
    if (late != null && mid != null && late - mid >= 5)
      advice.push(`Replies miss more often deep into long chats (${late}% after prompt 15). Start a fresh chat with a short summary after about 15 prompts.`);
    if (!advice.length) advice.push("Chat length doesn't change how often your replies miss.");
    const s = P.D.stats.sessions.prompts_per_session;
    return P.panel("Your setup", "which model, effort and chat length work for you",
      h("div", { class: "grid2" },
        table("Model", group((r) => (r.m ? P.modelName(r.m) : null))),
        table("Effort", group((r) => r.e || null))),
      h("h3", { text: "Chat length" }),
      h("p", { class: "small muted", text: `Your chats run ${s.median} prompts at the median, ${s.p90} at the long end.` }),
      table("Where in the chat", byPos),
      advice.map((a) => h("p", { class: "note", text: a })),
      h("p", { class: "small muted", text: "Thin groups (under 3 prompts) are left out. A model or effort with few prompts is weak evidence." }));
  }
})();
