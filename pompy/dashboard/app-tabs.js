/* Pompy Dashboard, part 2: Habits, Prompts (explorer + corrections), Ask Pompy. */
(function () {
  "use strict";
  const P = window.Pompy;
  const { h } = P;

  // ── Claude rewrite (the `sample` capability), shared by Habits and Ask Pompy ──
  function rewritePrompt(text, missing, about) {
    const tips = missing.map((n) => `- ${P.FEAT[n].label}: ${P.FEAT[n].tip}`).join("\n");
    return [
      "You are Pompy, a prompting coach. Rewrite the prompt below, which its author will send to",
      "Claude Code, so it is more specific. Keep the author's intent and voice; at most about three",
      "times longer. Add what it is missing:",
      tips || "- (it already has every part; tighten wording only)",
      "Never invent facts, file names or numbers: where a detail only the author knows is needed,",
      "write a short [placeholder in brackets]. Return only the rewritten prompt, nothing else.",
      ...(about ? ["", "What Pompy knows about this author (use it where it helps; never invent beyond it):", about] : []),
      "",
      "Prompt:",
      "<<<",
      text,
      ">>>",
    ].join("\n");
  }
  async function streamRewrite(text, missing, outEl, statusEl, about) {
    const sample = await P.cap("sample");
    if (!sample) {
      statusEl.textContent = "Asking Claude isn't available in this view.";
      return null;
    }
    statusEl.textContent = "Claude is rewriting…";
    try {
      const res = await sample(rewritePrompt(text, missing, about), {
        onText: ({ text: t }) => (outEl.textContent = t),
      });
      outEl.textContent = res.text.trim();
      statusEl.textContent = res.truncated ? "The answer was cut short." : "";
      return res.text.trim();
    } catch (e) {
      statusEl.textContent =
        e && e.code === "rate_limited" ? "Claude is busy: try again in a minute." : "Claude couldn't answer (" + ((e && (e.code || e.message)) || "error") + ").";
      return null;
    }
  }
  function scoreBadge(text) {
    const r = P.S.scoreText(text, false, false);
    return r.score == null ? h("span", { class: "pill none", text: r.role }) : h("span", { class: "pill", text: `${r.score}/7` });
  }
  async function copyText(text, btn) {
    try {
      await navigator.clipboard.writeText(text);
      btn.textContent = "Copied";
    } catch {
      btn.textContent = "Select and copy by hand";
    }
    setTimeout(() => (btn.textContent = "Copy"), 1600);
  }

  // ── Habits ───────────────────────────────────────────────────────────────
  P.views.habits = function (root) {
    const hs = P.D.habits || [];
    root.append(h("p", { class: "lede", text: "The three parts your task prompts leave out most often. For each, one of your real prompts: what is missing from it, and a version that would work better." }));
    hs.forEach((hb, i) => root.append(habitCard(hb, i + 1, hs.length)));
  };

  // What is wrong with one prompt, in words: the parts it lacks (this habit's first), what it has,
  // and what happened after you sent it.
  function analysis(row, focus) {
    const has = new Set(row.f);
    const share = (n) => (P.D.score.features[n] || {}).present_pct ?? 0;
    // The two fixes that matter most: this card's habit, then the parts you skip most often.
    const missing = P.D.rules.features.filter((f) => !has.has(f.name))
      .sort((x, y) => (x.name === focus ? -1 : y.name === focus ? 1 : x.name === "goal" ? -1 : y.name === "goal" ? 1 : share(x.name) - share(y.name)));
    const top = missing.slice(0, 2), rest = missing.slice(2);
    const items = top.map((f, i) =>
      h("li", { class: "miss" + (i === 0 ? " focus" : "") }, h("b", { text: `Add ${f.label.toLowerCase()}. ` }), h("span", { class: "muted", text: f.tip })));
    if (row.w <= 8) items.push(h("li", { class: "miss" }, h("b", { text: `Only ${row.w} words. ` }), h("span", { class: "muted", text: "Claude has to guess the rest." })));
    return h("div", {},
      h("ul", { class: "analysis" }, items),
      rest.length ? h("p", { class: "small muted", text: `Also missing: ${rest.map((f) => f.label.toLowerCase()).join(", ")}.` }) : null);
  }

  function habitCard(hb, rank, total) {
    const thin = (x) => (x.low_n ? h("span", { class: "thin", text: ` (only ${x.n} prompts, thin evidence)` }) : null);
    const trend = hb.this_month != null && hb.before != null
      ? h("p", { class: "small" }, "This month ", h("b", { class: "mono", text: `${hb.this_month}%` }), " vs before ", h("b", { class: "mono", text: `${hb.before}%` }),
        h("span", { class: hb.this_month >= hb.before ? "better" : "worse", text: hb.this_month >= hb.before ? "  ▲ improving" : "  ▼ slipping" }))
      : null;
    let idx = 0;
    const exBox = h("div", { class: "habit-body" });
    const card = h("div", { class: "panel", id: "habit-" + hb.feature },
      h("div", { class: "panel-head" }, h("span", { class: "habit-rank", text: `${rank} of ${total}` }), h("span", { class: "muted small", text: `in ${hb.present_pct}% of your task prompts` })),
      h("h2", {}, h("span", { class: "muted", text: "What to work on: " }), hb.label),
      h("p", { class: "note", text: hb.tip }),
      trend,
      h("p", { class: "small muted" }, `Something went wrong after ${hb.with.rate ?? "n/a"}% of prompts with it`, thin(hb.with), `, and ${hb.without.rate ?? "n/a"}% without it`, thin(hb.without), "."),
      exBox);
    const show = () => {
      exBox.textContent = "";
      const u = hb.examples[idx];
      const row = u && P.byId.get(u);
      if (!row) {
        exBox.append(h("p", { class: "muted", text: "No example prompt found." }));
        return;
      }
      const after = h("div", { class: "quote after" });
      const afterHead = h("div", { class: "controls" }, h("h3", { text: "A better version" }));
      const status = h("span", { class: "muted small" });
      const btn = h("button", { class: "btn", text: "Write it again" });
      const missing = row.f.length ? P.D.rules.features.map((f) => f.name).filter((n) => !row.f.includes(n)) : [hb.feature];
      const showAfter = (text) => {
        after.textContent = text;
        afterHead.textContent = "";
        const copy = h("button", { class: "btn", text: "Copy" });
        copy.addEventListener("click", () => copyText(text, copy));
        afterHead.append(h("h3", { text: "A better version" }), scoreBadge(text), copy);
      };
      const rewrite = async () => {
        btn.disabled = true;
        const text = await streamRewrite(row.t, missing, after, status);
        btn.disabled = false;
        if (text) {
          showAfter(text);
          const ref = await P.userDoc("rw~" + row.u).catch(() => null);
          if (ref) ref.set({ text, at: new Date().toISOString(), rubric_version: P.D.meta.rubric_version }).catch(() => {});
        }
      };
      btn.addEventListener("click", rewrite);
      const next = hb.examples.length > 1 ? h("button", { class: "btn", text: "Another example", onclick: () => { idx = (idx + 1) % hb.examples.length; show(); } }) : null;
      exBox.append(
        h("div", { class: "controls" }, h("h3", { text: "Your prompt" }), P.scorePill(row), h("span", { class: "muted small", text: `${P.when(row)} · ${row.p}` })),
        h("div", { class: "quote", text: row.t }),
        h("h3", { text: "How to improve it" }),
        analysis(row, hb.feature),
        afterHead, after,
        h("div", { class: "controls" }, btn, next, status));
      // A saved version shows at once; otherwise Claude writes one now.
      P.userDoc("rw~" + row.u).then((ref) => ref && ref.get()).then((snap) => {
        if (hb.examples[idx] !== row.u) return;
        if (snap && snap.exists) showAfter(snap.data().text);
        else rewrite();
      }).catch(() => { if (hb.examples[idx] === row.u) rewrite(); });
    };
    show();
    return card;
  }

  // A "make it better" block: Claude's version of a prompt, with Copy (and an optional extra button).
  function betterBlock(getText, getMissing, extra, getAbout) {
    const out = h("div", { class: "quote after hidden" });
    const head = h("div", { class: "controls hidden" });
    const status = h("span", { class: "muted small" });
    const btn = h("button", { class: "btn primary", text: "Make it better" });
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      out.classList.remove("hidden");
      head.classList.add("hidden");
      const text = await streamRewrite(getText(), getMissing(), out, status, getAbout ? getAbout() : null);
      btn.disabled = false;
      if (!text) return;
      head.textContent = "";
      const copy = h("button", { class: "btn", text: "Copy" });
      copy.addEventListener("click", () => copyText(text, copy));
      head.append(h("h3", { text: "Better" }), scoreBadge(text), extra ? extra(text) : null, copy);
      head.classList.remove("hidden");
    });
    return { el: h("div", { class: "better-block" }, h("div", { class: "controls" }, btn, status), head, out), btn };
  }

  // ── Prompts: learn from the ones that went wrong ─────────────────────────
  P.views.prompts = function (root) {
    const tasks = P.D.prompts.filter((r) => r.k === "typed" && r.r === "task");
    const newest = (a, b) => (b.d + b.hh).localeCompare(a.d + a.hh);
    const views = [
      ["wrong", "Went wrong", () => tasks.filter((r) => r.fr.length).sort(newest)],
      ["vague", "Most vague", () => tasks.filter((r) => r.s <= 1).sort(newest)],
      ["all", "All", () => [...tasks].sort(newest)],
    ];
    let cur = "wrong", q = "", limit = 40;
    const tabs = h("div", { class: "seg-tabs", role: "tablist" });
    const search = h("input", { type: "search", class: "text", placeholder: "Search", "aria-label": "Search prompts", style: "flex:0 1 220px" });
    const count = h("p", { class: "small muted" });
    const list = h("div", { class: "plist" });
    const more = h("button", { class: "btn hidden", text: "Show more", onclick: () => { limit += 40; draw(); } });
    search.addEventListener("input", () => { q = search.value.trim().toLowerCase(); limit = 40; draw(); });
    views.forEach(([id, label]) => {
      const b = h("button", { class: "seg-tab", role: "tab", text: label, onclick: () => { cur = id; limit = 40; draw(); } });
      b.dataset.id = id;
      tabs.append(b);
    });
    root.append(
      h("p", { class: "lede", text: "Open a prompt to see what it was missing and get a better version." }),
      h("div", { class: "panel" }, h("div", { class: "controls spread" }, tabs, search), count, list, more));

    function draw() {
      tabs.querySelectorAll("button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.id === cur)));
      const all = views.find((v) => v[0] === cur)[2]();
      const out = q ? all.filter((r) => r.t.toLowerCase().includes(q)) : all;
      count.textContent = { wrong: `${out.length} prompts where something went wrong right after.`, vague: `${out.length} prompts with one part or none.`, all: `${out.length} task prompts.` }[cur];
      list.textContent = "";
      out.slice(0, limit).forEach((r) => list.append(promptRow(r)));
      more.classList.toggle("hidden", out.length <= limit);
      if (!out.length) list.append(h("p", { class: "muted", style: "padding:14px 4px", text: "Nothing here." }));
    }
    draw();
  };

  function promptRow(r) {
    const row = h("div", { class: "prow", tabindex: 0, role: "button", "aria-expanded": "false" },
      h("div", {},
        h("div", { class: "txt", text: r.t || "(an image or paste only)" }),
        h("div", { class: "meta", text: `${P.when(r)} · ${r.p}` })),
      h("div", { class: "right" }, P.scorePill(r), r.fr.length ? h("span", { class: "chip fr", text: P.FRICTION_LABEL[r.fr[0]] }) : null));
    let open = null;
    const toggle = (e) => {
      if (open && open.contains(e.target)) return;
      const clamp = row.querySelector(".txt");
      if (open) { open.remove(); open = null; clamp.classList.remove("hidden"); row.setAttribute("aria-expanded", "false"); return; }
      open = detail(r);
      row.append(open);
      clamp.classList.add("hidden");
      row.setAttribute("aria-expanded", "true");
    };
    row.addEventListener("click", toggle);
    row.addEventListener("keydown", (e) => { if ((e.key === "Enter" || e.key === " ") && e.target === row) { e.preventDefault(); toggle(e); } });
    return row;
  }

  function detail(r) {
    const box = h("div", { class: "detail" }, h("div", { class: "full", text: r.t }));
    if (r.q) box.append(h("p", { class: "small" }, h("b", { text: "Claude had to ask: " }), r.q));
    const like = (r.nb || []).map((u) => P.byId.get(u)).filter(Boolean);
    if (like.length) {
      const wrong = like.filter((x) => x.fr.length).length;
      box.append(
        h("h3", { text: "Prompts like this" }),
        h("p", { class: "small muted", text: wrong ? `${wrong} of ${like.length} went wrong after.` : `All ${like.length} went fine.` }),
        h("ul", { class: "like-list" }, like.map((x) =>
          h("li", {}, h("span", { class: x.fr.length ? "worse" : "better", text: x.fr.length ? "✕ " : "✓ " }),
            h("span", { text: x.t.length > 120 ? x.t.slice(0, 119) + "…" : x.t }),
            h("span", { class: "muted small", text: `  ${P.when(x)}${x.fr.length ? " · " + P.FRICTION_LABEL[x.fr[0]] : ""}` })))));
    }
    if (r.r === "task") {
      box.append(h("h3", { text: "How to improve it" }), analysis(r, P.D.rules.features.find((f) => !r.f.includes(f.name))?.name));
      const missing = P.D.rules.features.map((f) => f.name).filter((n) => !r.f.includes(n));
      box.append(betterBlock(() => r.t, () => missing).el);
    }
    box.append(feedbackForm(r));
    return box;
  }

  // Corrections stay private (data/users/<you>/…) and hold verdicts, never prompt text.
  function feedbackForm(r) {
    const status = h("span", { class: "muted small" });
    const opts = r.r === "task" ? [["too_high", "Score too high"], ["too_low", "Score too low"], ["not_task", "Not a task"]] : [["is_task", "This was a task"]];
    const choices = h("span", { class: "controls hidden" }, opts.map(([v, t]) =>
      h("button", { class: "btn", text: t, onclick: () => save(v, t) })));
    const open = h("button", { class: "linkish", text: "Pompy got this wrong?", onclick: () => { choices.classList.toggle("hidden"); } });
    async function save(v, t) {
      const ref = await P.userDoc("fb~" + r.u).catch(() => null);
      if (!ref) { status.textContent = "Can't save here."; return; }
      const fix = { too_high: { score: "too_high" }, too_low: { score: "too_low" }, not_task: { role: "wrong" }, is_task: { role: "wrong" } }[v];
      try {
        await ref.set({ uuid: r.u, ...fix, stored_role: r.r, stored_score: r.s, stored_friction: r.fr, rubric_version: P.D.meta.rubric_version, at: new Date().toISOString() });
        choices.classList.add("hidden");
        status.textContent = `Noted: ${t.toLowerCase()}. Thanks.`;
      } catch (e) {
        status.textContent = "Couldn't save.";
      }
    }
    return h("div", { class: "controls" }, open, choices, status);
  }

  // ── Write a better prompt: Pompy rewrites it with what it knows about you ──
  // What Pompy knows, for one project (or none): its goal, the parts you usually leave out, and
  // why your recent misses there happened, plus one past prompt of yours that worked.
  function knowledge(project) {
    const rows = P.D.prompts.filter((r) => r.v && (!project || r.p === project));
    const goal = ((P.D.learned || {}).patterns || []).find((x) => x.kind === "goal" && x.scope === "project: " + project);
    const miss = rows.filter((r) => r.c).slice(-30).map((r) => r.c[1]);
    const count = new Map(); miss.forEach((m) => count.set(m, (count.get(m) || 0) + 1));
    const causes = [...count].sort((a, b) => b[1] - a[1]).slice(0, 3).map(([m, n]) => `${m.replace(/^The prompt never said/, "Their prompts often left out").replace(/\.$/, "")} (${n} recent ${n === 1 ? "miss" : "misses"}).`);
    const good = rows.filter((r) => r.v === "useful" && r.s >= 3).slice(-1)[0];
    const lines = [];
    if (goal) lines.push(`Project goal: ${goal.s}`);
    causes.forEach((c) => lines.push(c));
    if (good) lines.push(`A prompt of theirs that worked well: "${good.t.slice(0, 240)}"`);
    return lines;
  }

  P.views.ask = function (root) {
    const ta = h("textarea", { id: "ask-draft", "aria-label": "Your prompt", placeholder: "Type a rough prompt. Pompy fills in what you usually leave out." });
    const projects = [...new Set(P.D.prompts.filter((r) => r.v).slice(-200).map((r) => r.p))];
    const proj = h("select", { class: "sel", "aria-label": "Project" },
      h("option", { value: "", text: "Any project" }), projects.map((p) => h("option", { value: p, text: p.replace(/[-_]+/g, " ") })));
    proj.value = projects[projects.length - 1] || "";
    const read = h("div", { class: "coach-read" });
    const knows = h("ul", { class: "small muted knows" });
    const better = betterBlock(() => ta.value, () => P.S.scoreText(ta.value, false, false).missing,
      (text) => h("button", { class: "btn", text: "Use it", onclick: () => { ta.value = text; draw(); } }),
      () => knowledge(proj.value).join("\n"));
    root.append(h("div", { class: "panel coach" },
      h("h2", { text: "Write a better prompt" }),
      h("div", { class: "controls" }, h("label", { class: "small muted", text: "For" }), proj),
      ta, read, better.el,
      h("details", {}, h("summary", { class: "small muted", text: "What Pompy will tell Claude about you" }), knows)));
    let timer;
    ta.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(draw, 120); });
    const showKnows = () => { knows.textContent = ""; const k = knowledge(proj.value); (k.length ? k : ["Nothing yet for this project."]).forEach((x) => knows.append(h("li", { text: x }))); };
    proj.addEventListener("change", () => { showKnows(); draw(); });
    showKnows();
    if (P.prefill) { ta.value = P.prefill; P.prefill = null; }
    window.addEventListener("hashchange", () => { if (location.hash === "#ask" && P.prefill) { ta.value = P.prefill; P.prefill = null; draw(); } });

    function draw() {
      const text = ta.value.trim();
      read.textContent = "";
      better.btn.disabled = !text;
      if (!text) { read.append(h("p", { class: "muted", text: "Pompy scores it as you type and tells you the one thing to add next." })); return; }
      const r = P.S.scoreText(text, false, false);
      if (r.role !== "task") { read.append(h("p", { class: "muted", text: `Reads as a ${r.role}. Nothing to add, send it.` })); return; }
      const s = r.score;
      const cls = s >= 4 ? "good" : s >= 2 ? "ok" : "low";
      // Order the missing parts by how often leaving them out made YOUR replies miss, then by rarity.
      const blamed = new Map();
      P.D.prompts.forEach((x) => x.lk && (!proj.value || x.p === proj.value) && blamed.set(x.lk, (blamed.get(x.lk) || 0) + 1));
      const order = P.D.rules.features.map((f) => f.name).sort((x, y) => (x === "goal" ? -1 : y === "goal" ? 1
        : (blamed.get(y) || 0) - (blamed.get(x) || 0) || P.D.score.features[x].present_pct - P.D.score.features[y].present_pct));
      const next = order.filter((n) => r.missing.includes(n)).slice(0, 2);
      read.append(
        h("div", { class: "controls" },
          h("div", { class: "dots " + cls, "aria-label": `${s} of 7 parts` }, Array.from({ length: 7 }, (_, i) => h("i", { class: i < s ? "on" : "" }))),
          h("b", { class: "coach-score " + cls, text: s >= 4 ? "Clear" : s >= 2 ? "Okay" : "Vague" })),
        next.length
          ? h("ul", { class: "next-list" }, next.map((n) => h("li", {}, h("b", { text: `Add ${P.FEAT[n].label.toLowerCase()}. ` }), h("span", { class: "muted", text: P.FEAT[n].tip }))))
          : h("p", { class: "better", text: "Every part is there." }));
    }
    draw();
  };
})();
