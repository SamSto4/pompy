/* Pompy Dashboard, part 2: Habits, Prompts (explorer + corrections), Ask Pompy. */
(function () {
  "use strict";
  const P = window.Pompy;
  const { h } = P;

  // ── Claude rewrite (the `sample` capability), shared by Habits and Ask Pompy ──
  function rewritePrompt(text, missing) {
    const tips = missing.map((n) => `- ${P.FEAT[n].label}: ${P.FEAT[n].tip}`).join("\n");
    return [
      "You are Pompy, a prompting coach. Rewrite the prompt below, which its author will send to",
      "Claude Code, so it is more specific. Keep the author's intent and voice; at most about three",
      "times longer. Add what it is missing:",
      tips || "- (it already has every part; tighten wording only)",
      "Never invent facts, file names or numbers: where a detail only the author knows is needed,",
      "write a short [placeholder in brackets]. Return only the rewritten prompt, nothing else.",
      "",
      "Prompt:",
      "<<<",
      text,
      ">>>",
    ].join("\n");
  }
  async function streamRewrite(text, missing, outEl, statusEl) {
    const sample = await P.cap("sample");
    if (!sample) {
      statusEl.textContent = "Asking Claude isn't available in this view.";
      return null;
    }
    statusEl.textContent = "Claude is rewriting…";
    try {
      const res = await sample(rewritePrompt(text, missing), {
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
    root.append(h("p", { class: "lede", text: "The three parts your task prompts leave out most often, with a real prompt of yours as the example. Ranked by how rarely you include each part, which is solid evidence; the friction numbers are shown too, but rest on fewer prompts." }));
    hs.forEach((hb, i) => root.append(habitCard(hb, i + 1)));
  };

  function habitCard(hb, rank) {
    const thin = (x) => (x.low_n ? h("span", { class: "thin", text: ` (only ${x.n} prompts: thin evidence)` }) : null);
    const trend = hb.this_month != null && hb.before != null
      ? h("p", { class: "small" }, "This month ", h("b", { class: "mono", text: `${hb.this_month}%` }), " vs before ", h("b", { class: "mono", text: `${hb.before}%` }),
        h("span", { class: hb.this_month >= hb.before ? "better" : "worse", text: hb.this_month >= hb.before ? "  ▲ improving" : "  ▼ slipping" }))
      : null;
    let idx = 0;
    const exBox = h("div", { class: "detail" });
    const card = h("div", { class: "panel", id: "habit-" + hb.feature },
      h("div", { class: "panel-head" }, h("span", { class: "habit-rank", text: `#${rank} to work on` }), h("span", { class: "muted small", text: `in ${hb.present_pct}% of your task prompts` })),
      h("h2", { text: hb.label }),
      h("p", { class: "note", text: hb.tip }),
      trend,
      h("p", { class: "small muted" }, `Friction after prompts with it: ${hb.with.rate ?? "–"}% `, thin(hb.with), ` · without it: ${hb.without.rate ?? "–"}% `, thin(hb.without)),
      exBox);
    const show = () => {
      exBox.textContent = "";
      const u = hb.examples[idx];
      const row = u && P.byId.get(u);
      if (!row) {
        exBox.append(h("p", { class: "muted", text: "No example prompt found." }));
        return;
      }
      const after = h("div", { class: "quote after hidden" });
      const afterHead = h("div", { class: "controls hidden" });
      const status = h("span", { class: "muted small" });
      const btn = h("button", { class: "btn primary", text: "Rewrite with Claude" });
      const missing = row.f.length ? P.D.rules.features.map((f) => f.name).filter((n) => !row.f.includes(n)) : [hb.feature];
      const showAfter = (text) => {
        after.classList.remove("hidden");
        after.textContent = text;
        afterHead.textContent = "";
        const copy = h("button", { class: "btn", text: "Copy" });
        copy.addEventListener("click", () => copyText(text, copy));
        afterHead.append(h("b", { text: "After" }), scoreBadge(text), copy);
        afterHead.classList.remove("hidden");
      };
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        after.classList.remove("hidden");
        const text = await streamRewrite(row.t, missing, after, status);
        btn.disabled = false;
        if (text) {
          showAfter(text);
          const ref = await P.userDoc("rw~" + row.u).catch(() => null);
          if (ref) ref.set({ text, at: new Date().toISOString(), rubric_version: P.D.meta.rubric_version }).catch(() => {});
        }
      });
      const next = hb.examples.length > 1 ? h("button", { class: "btn", text: "Another example", onclick: () => { idx = (idx + 1) % hb.examples.length; show(); } }) : null;
      exBox.append(
        h("div", { class: "controls" }, h("b", { text: "Before" }), P.scorePill(row), h("span", { class: "muted small mono", text: `${row.d} · ${row.p}` }),
          row.fr.map((k) => h("span", { class: "chip fr", text: P.FRICTION_LABEL[k] }))),
        h("div", { class: "quote", text: row.t }),
        h("div", { class: "controls" }, btn, next, status),
        afterHead, after);
      P.userDoc("rw~" + row.u).then((ref) => ref && ref.get()).then((snap) => {
        if (snap && snap.exists && hb.examples[idx] === row.u) showAfter(snap.data().text);
      }).catch(() => {});
    };
    show();
    return card;
  }

  // ── Prompts explorer + corrections ───────────────────────────────────────
  P.views.prompts = function (root) {
    const rows = P.D.prompts;
    const projects = [...new Set(rows.map((r) => r.p))].sort();
    const st = { q: "", role: "typed", project: "", period: "all", fr: "", sort: "new", limit: 150 };
    const search = h("input", { type: "search", id: "p-search", placeholder: "Search your prompts…", "aria-label": "Search prompts" });
    const sel = (id, label, opts, key) => {
      const e = h("select", { id, "aria-label": label });
      opts.forEach(([v, t]) => e.append(h("option", { value: v, text: t, selected: st[key] === v })));
      e.addEventListener("change", () => { st[key] = e.value; st.limit = 150; draw(); });
      return e;
    };
    const summary = h("p", { class: "small muted mono" });
    const list = h("div", { class: "plist" });
    const more = h("button", { class: "btn hidden", text: "Show more", onclick: () => { st.limit += 150; draw(); } });
    const fbLine = h("p", { class: "small muted" });
    search.addEventListener("input", () => { st.q = search.value.trim().toLowerCase(); st.limit = 150; draw(); });

    root.append(
      h("p", { class: "lede", text: "Every prompt Pompy has, with the verdicts it reached. Open one to see why, and tell Pompy when it got something wrong: your corrections are saved privately and used to recalibrate the rules." }),
      h("div", { class: "panel" },
        h("div", { class: "controls" }, search,
          sel("p-role", "Role", [["typed", "Typed prompts"], ["task", "Tasks (scored)"], ["reply", "Replies"], ["correction", "Corrections"], ["routine", "Routine"], ["other", "Other"], ["all", "Everything"]], "role"),
          sel("p-project", "Project", [["", "All projects"], ...projects.map((p) => [p, p])], "project"),
          sel("p-period", "Period", P.periods().map((p) => [p.id, p.label]), "period"),
          sel("p-fr", "Friction", [["", "Any friction"], ["none", "No friction"], ["some", "Some friction"], ...Object.entries(P.FRICTION_LABEL).map(([k, v]) => [k, v])], "fr"),
          sel("p-sort", "Sort", [["new", "Newest first"], ["old", "Oldest first"], ["low", "Lowest score"], ["high", "Highest score"]], "sort")),
        summary, fbLine, list, more));

    function draw() {
      const per = P.period(st.period);
      let out = rows.filter((r) => {
        if (st.role === "typed" && r.k !== "typed") return false;
        if (!["typed", "all"].includes(st.role) && r.r !== st.role) return false;
        if (st.project && r.p !== st.project) return false;
        if (!per.test(r)) return false;
        if (st.fr === "none" && r.fr.length) return false;
        if (st.fr === "some" && !r.fr.length) return false;
        if (st.fr && !["none", "some"].includes(st.fr) && !r.fr.includes(st.fr)) return false;
        if (st.q && !r.t.toLowerCase().includes(st.q)) return false;
        return true;
      });
      const key = { new: (a, b) => (b.d + b.hh).localeCompare(a.d + a.hh), old: (a, b) => (a.d + a.hh).localeCompare(b.d + b.hh),
        low: (a, b) => (a.s ?? 99) - (b.s ?? 99), high: (a, b) => (b.s ?? -1) - (a.s ?? -1) }[st.sort];
      out.sort(key);
      const scored = out.filter((r) => r.s != null);
      summary.textContent = `${out.length} prompts · ${scored.length} scored · mean ${P.fmt(P.round(P.mean(scored.map((r) => r.s)), 2))}/7 · friction after ${P.fmt(P.pct(scored.filter((r) => r.fr.length).length, scored.length), "%")} of tasks`;
      list.textContent = "";
      out.slice(0, st.limit).forEach((r) => list.append(promptRow(r)));
      more.classList.toggle("hidden", out.length <= st.limit);
      if (!out.length) list.append(h("p", { class: "muted", style: "padding:14px 4px", text: "No prompts match. Widen the filters." }));
    }
    draw();
    feedbackSummary(fbLine);
  };

  function promptRow(r) {
    const row = h("div", { class: "prow", tabindex: 0, role: "button", "aria-expanded": "false" },
      h("div", {},
        h("div", { class: "meta", text: `${r.d} ${r.hh} · ${r.p} · chat ${r.si}${r.o ? " · opened the chat" : ""}${r.k !== "typed" ? " · " + r.k : ""}` }),
        h("div", { class: "txt", text: r.t || "(no words: an image or paste only)" })),
      h("div", { class: "right" }, P.scorePill(r), r.r && r.r !== "task" ? h("span", { class: "chip", text: r.r }) : null,
        r.fr.map((k) => h("span", { class: "chip fr", text: P.FRICTION_LABEL[k] }))));
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
    const box = h("div", { class: "detail" });
    box.append(h("div", { class: "full", text: r.t }));
    if (r.k === "typed") {
      const live = P.S.scoreText(r.t, r.pa, r.im);
      const agrees = live.role === r.r && (r.r !== "task" || live.score === r.s);
      box.append(
        h("div", { class: "checks" }, P.D.rules.features.map((f) => h("span", { class: r.f.includes(f.name) ? "yes" : "no", text: f.label }))),
        h("p", { class: "small muted" }, `Role: ${r.r}. `, agrees
          ? h("span", { class: "better", text: "The in-page scorer agrees with Python." })
          : h("span", { class: "worse", text: "The in-page scorer disagrees (the text shown here may be shortened or redacted)." })));
    }
    if (r.fr.length) box.append(h("p", { class: "small" }, "Friction: ", r.fr.map((k) => P.FRICTION_LABEL[k]).join(", ")));
    if (r.q) box.append(h("p", { class: "small" }, h("b", { text: "Claude asked: " }), h("span", { text: r.q })));
    if (r.k === "typed") box.append(feedbackForm(r));
    return box;
  }

  // Corrections: private to you (data/users/<you>/…). They hold verdicts, never prompt text.
  function feedbackForm(r) {
    const wrap = h("div", { class: "controls" });
    const status = h("span", { class: "muted small" });
    const state = {};
    const group = (label, field, options) => {
      const g = h("span", { class: "controls" }, h("span", { class: "small muted", text: label }));
      options.forEach(([v, t]) => {
        const b = h("button", { class: "btn", text: t, "aria-pressed": "false" });
        b.addEventListener("click", async () => {
          state[field] = state[field] === v ? null : v;
          g.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", "false"));
          if (state[field]) b.setAttribute("aria-pressed", "true");
          paint(g, field);
          await save();
        });
        b.dataset.v = v;
        g.append(b);
      });
      return g;
    };
    const paint = (g, field) => g.querySelectorAll("button").forEach((x) => x.classList.toggle("primary", x.dataset.v === state[field]));
    const scoreG = r.r === "task" ? group("Score:", "score", [["right", "Right"], ["too_high", "Too high"], ["too_low", "Too low"]]) : null;
    const roleG = group("Role:", "role", [["right", "Right"], ["wrong", "Wrong"]]);
    const frG = group("Friction:", "friction", [["right", "Right"], ["missed", "Missed some"], ["false_alarm", "False alarm"]]);
    wrap.append(h("b", { class: "small", text: "Is Pompy right?" }), scoreG, roleG, frG, status);
    async function save() {
      const ref = await P.userDoc("fb~" + r.u).catch(() => null);
      if (!ref) { status.textContent = "Saving needs the page's database, which isn't available here."; return; }
      try {
        await ref.set({ uuid: r.u, ...state, stored_role: r.r, stored_score: r.s, stored_friction: r.fr, rubric_version: P.D.meta.rubric_version, at: new Date().toISOString() });
        status.textContent = "Saved privately.";
        P._fbDirty = true;
      } catch (e) {
        status.textContent = "Couldn't save (" + (e.code || "error") + ").";
      }
    }
    P.userDoc("fb~" + r.u).then((ref) => ref && ref.get()).then((snap) => {
      if (!snap || !snap.exists) return;
      Object.assign(state, snap.data());
      [[scoreG, "score"], [roleG, "role"], [frG, "friction"]].forEach(([g, f]) => g && paint(g, f));
      status.textContent = "You corrected this before.";
    }).catch(() => {});
    return wrap;
  }

  async function feedbackSummary(line) {
    const [db, user] = await Promise.all([P.cap("db"), P.cap("user")]);
    const id = user && (await user.id());
    if (!db || !id) { line.textContent = "Corrections can't be saved in this view."; return; }
    const snap = await db.collection("data/users/" + id).get().catch(() => null);
    const fb = snap ? snap.docs.filter((d) => d.id.startsWith("fb~")).map((d) => d.data()) : [];
    line.textContent = "";
    const copy = h("button", { class: "btn", text: "Copy corrections as JSON" });
    copy.addEventListener("click", () => copyText(JSON.stringify(fb, null, 1), copy));
    line.append(`You've corrected ${fb.length} prompt${fb.length === 1 ? "" : "s"}. `);
    if (fb.length) line.append(copy);
  }

  // ── Ask Pompy: Pre-flight in the browser ─────────────────────────────────
  P.views.ask = function (root) {
    const ta = h("textarea", { id: "ask-draft", "aria-label": "Draft prompt", placeholder: "Write the prompt you're about to send…" });
    ta.value = "fix the nav bar on mobile";
    const paste = h("input", { type: "checkbox", id: "ask-paste" });
    const image = h("input", { type: "checkbox", id: "ask-image" });
    const result = h("div", { class: "panel", id: "ask-result" });
    const similar = h("div", { class: "panel", id: "ask-similar" });
    const status = h("span", { class: "muted small" });
    const after = h("div", { class: "quote after hidden" });
    const afterHead = h("div", { class: "controls hidden" });
    const improve = h("button", { class: "btn primary", text: "Improve with Claude" });
    root.append(
      h("p", { class: "lede", text: "Score a draft before you send it. This runs the same rules as `pompy check`, in the page, as you type. The example below is a placeholder: replace it with a real draft." }),
      h("div", { class: "grid2" },
        h("div", { class: "panel" }, h("h2", { text: "Your draft" }), ta,
          h("div", { class: "controls small" }, h("label", {}, paste, " includes pasted text"), h("label", {}, image, " includes a screenshot")),
          h("div", { class: "controls" }, improve, status), afterHead, after),
        result),
      similar);
    let timer;
    const update = () => { clearTimeout(timer); timer = setTimeout(() => draw(), 120); };
    [ta, paste, image].forEach((e) => e.addEventListener("input", update));
    improve.addEventListener("click", async () => {
      const r = P.S.scoreText(ta.value, paste.checked, image.checked);
      improve.disabled = true;
      after.classList.remove("hidden");
      const text = await streamRewrite(ta.value, r.missing, after, status);
      improve.disabled = false;
      if (!text) return;
      afterHead.textContent = "";
      const use = h("button", { class: "btn", text: "Use this draft", onclick: () => { ta.value = text; draw(); } });
      const copy = h("button", { class: "btn", text: "Copy" });
      copy.addEventListener("click", () => copyText(text, copy));
      afterHead.append(h("b", { text: "Claude's version" }), scoreBadge(text), use, copy);
      afterHead.classList.remove("hidden");
    });

    function draw() {
      const text = ta.value;
      const r = P.S.scoreText(text, paste.checked, image.checked);
      result.textContent = "";
      result.append(h("h2", { text: "Pompy's read" }));
      if (!text.trim()) { result.append(h("p", { class: "muted", text: "Start typing." })); similar.textContent = ""; return; }
      if (r.role !== "task") {
        result.append(h("p", {}, "This reads as a ", h("b", { text: r.role }), " prompt, so it isn't scored. Replies, corrections and routine commands are complete for what they do."));
      } else {
        result.append(
          h("div", { class: "controls" }, h("span", { class: "big", text: `${r.score}/7` }),
            h("div", { class: "seg", "aria-hidden": "true" }, Array.from({ length: 7 }, (_, i) => h("i", { class: i < r.score ? "on" : "" })))),
          h("div", { class: "checks" }, P.D.rules.features.map((f) => h("span", { class: r.features[f.name] ? "yes" : "no", text: f.label }))));
        const habitNames = (P.D.habits || []).map((x) => x.feature);
        const tips = r.missing.filter((n) => n !== "goal" || !r.features.goal).slice(0, 3);
        if (tips.length) {
          result.append(h("h3", { text: "Consider adding" }), h("div", { class: "bars" }, tips.map((n) => {
            const usual = habitNames.includes(n) ? h("span", { class: "thin", text: ` You usually leave this out (in ${P.D.score.features[n].present_pct}% of your tasks).` }) : null;
            return h("p", { class: "small" }, h("b", { text: P.FEAT[n].label + ": " }), P.FEAT[n].tip, usual);
          })));
        }
      }
      drawSimilar(text);
    }

    function drawSimilar(text) {
      similar.textContent = "";
      const terms = new Set(P.S.contentTerms(text));
      similar.append(h("div", { class: "panel-head" }, h("h2", { text: "Prompts like this, from your history" }), h("span", { class: "muted small", text: "shared topic words" })));
      if (!terms.size) { similar.append(h("p", { class: "muted small", text: "No topic words yet to compare." })); return; }
      const hits = [];
      for (const r of P.D.prompts) {
        if (r.k !== "typed" || r.r !== "task") continue;
        const t = new Set(P.S.contentTerms(r.t));
        let both = 0;
        terms.forEach((x) => t.has(x) && both++);
        if (!both) continue;
        hits.push([both / (terms.size + t.size - both), r]);
      }
      hits.sort((a, b) => b[0] - a[0]);
      const top = hits.slice(0, 6).map((x) => x[1]);
      if (!top.length) { similar.append(h("p", { class: "muted small", text: "Nothing similar yet." })); return; }
      const withFr = top.filter((r) => r.fr.length).length;
      similar.append(h("p", { class: "small" }, `${withFr} of these ${top.length} led to friction. Their average score was `, h("b", { class: "mono", text: `${P.round(P.mean(top.map((r) => r.s)), 1)}/7` }), "."));
      const list = h("div", { class: "plist" });
      top.forEach((r) => list.append(promptRow(r)));
      similar.append(list);
    }
    draw();
  };
})();
