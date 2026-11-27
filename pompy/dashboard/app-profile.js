/* Pompy Dashboard, part 5 (the Volt redesign): Home's top, My profile (the Prompt Score),
 * Plan before you send, Projects and Analytics. Every number here is counted from data.json
 * (decided in Python); nothing is estimated by a model. Text from data goes through textContent. */
(function () {
  "use strict";
  const P = window.Pompy;
  const { h, s } = P;

  // ── Shared: your scored prompts ────────────────────────────────────────────
  const newest = (a, b) => (a.d + a.hh).localeCompare(b.d + b.hh);
  const scored = () => P.D.prompts.filter((r) => r.k === "typed" && r.r === "task" && r.s != null).sort(newest);
  const avg = (rows) => (rows.length ? rows.reduce((t, r) => t + r.s, 0) / rows.length : null);
  const tenOf = (mean7) => (mean7 == null ? null : Math.round((mean7 * 100) / 7) / 10);
  const bandOf10 = (x) => (x == null ? "none" : x >= 5.7 ? "good" : x >= 2.9 ? "ok" : "low");
  const wordOf10 = (x) => ({ good: "Clear", ok: "Okay", low: "Vague", none: "No score yet" })[bandOf10(x)];
  const project = (p) => String(p || "").replace(/[-_]+/g, " ");
  const daysAgo = (n) => { const d = new Date(P.D.meta.today + "T12:00:00Z"); d.setUTCDate(d.getUTCDate() - n); return d.toISOString().slice(0, 10); };
  // Share of task prompts with a check, as the deck's words: Strong, Good, Weak, Missing.
  const factorWord = (pct) => (pct >= 60 ? "Strong" : pct >= 35 ? "Good" : pct >= 15 ? "Weak" : "Missing");
  const head = (eyebrow, title, lede) =>
    h("div", { class: "page-head" }, h("span", { class: "eyebrow", text: eyebrow }), h("h1", { text: title }), lede ? h("p", { class: "lede", text: lede }) : null);
  const countTile = (label, value, sub, cls) => {
    const v = h("div", { class: "value" });
    P.countUp(v, value);
    return h("div", { class: "tile" + (cls ? " " + cls : "") }, h("div", { class: "label", text: label }), v, h("div", { class: "sub", text: sub }));
  };

  // ── Home: the top of the page ──────────────────────────────────────────────
  P.views.home = function (root) {
    const rows = scored();
    const all = tenOf(avg(rows));
    const last = rows[rows.length - 1];
    const st = P.D.stats;
    const hb = (P.D.habits || [])[0];
    root.append(
      h("div", { class: "home-hero" },
        h("div", { class: "intro" },
          h("span", { class: "eyebrow", text: "Your AI, understood" }),
          h("h1", { text: "How you prompt, and the one thing to change." }),
          h("p", { class: "lede", text: "Pompy reads your Claude Code chats on this Mac, scores every task prompt out of 10, and learns which habits make Claude miss." }),
          h("div", { class: "controls" },
            h("a", { class: "btn primary", href: "#write", text: "Plan your next prompt" }),
            h("a", { class: "btn", href: "#profile", text: "See my profile" }))),
        h("a", { class: "panel lift", href: "#profile", style: "text-decoration:none;color:inherit", "aria-label": "Your Prompt Score. Open your profile." },
          h("div", { class: "panel-head" }, h("span", { class: "eyebrow", text: "Your Prompt Score" }), h("span", { class: "score-word " + bandOf10(all), text: wordOf10(all) })),
          h("div", { class: "score-card" },
            P.ring(all, { band: bandOf10(all), label: "average" }),
            h("div", { style: "display:flex;flex-direction:column;gap:8px" },
              h("p", { class: "small muted", text: `Across ${rows.length.toLocaleString()} task prompts.` }),
              last ? h("p", { class: "small" }, "Latest: ", h("b", { text: `${P.tenText(last.s)}/10` }), h("span", { class: "muted", text: ` · ${P.when(last)}` })) : null,
              h("span", { class: "small", style: "color:var(--primary);font-weight:500", text: "Open my profile →" }))))),
      h("div", { class: "tiles" },
        countTile("Prompts", st.totals.prompts_by_kind.typed || P.D.meta.prompts, "typed by you"),
        countTile("Chats", st.sessions.with_prompts || st.totals.sessions, `median ${st.sessions.prompts_per_session.median} prompts each`),
        countTile("Active days", st.time.active_days, `${P.niceDate(P.D.meta.first_day, true)} to now`),
        countTile("Projects", st.totals.projects, "worked in")),
      hb ? topInsight(hb) : null);
  };

  // One thing to change today: the habit that matters most, with its evidence and how sure Pompy is.
  function topInsight(hb) {
    const thin = hb.with.low_n || hb.without.low_n;
    const evidence = hb.with.rate != null && hb.without.rate != null
      ? `Something went wrong after ${hb.without.rate}% of prompts without it, vs ${hb.with.rate}% with it.`
      : "Not enough prompts yet to tie it to how replies went.";
    return h("div", { class: "panel insight lift" },
      h("div", { class: "panel-head" }, h("span", { class: "chip lime", text: "One thing to change today" }),
        h("span", { class: "small muted", text: thin ? "Thin evidence" : "Counted from your chats" })),
      h("h2", { text: `Add ${hb.label.toLowerCase()} to your prompts.` }),
      h("p", { class: "why", text: hb.tip }),
      h("div", { class: "meta-row" },
        h("span", { text: `In ${hb.present_pct}% of your task prompts today` }),
        h("span", { text: evidence })),
      h("div", { class: "controls" }, h("a", { class: "btn", href: "#insights", text: "See examples and fixes" })));
  }

  // ── My profile: the Prompt Score ──────────────────────────────────────────
  P.views.profile = function (root) {
    const rows = scored();
    const recent = rows.filter((r) => r.d > daysAgo(30));
    const all10 = tenOf(avg(rows)), recent10 = tenOf(avg(recent));
    const sc = P.D.score || {};
    // Like a social profile: who you are as a prompter, then your feed of prompts.
    root.append(profileHeader(rows, all10, recent10, recent.length));
    root.append(feedPanel(rows));

    // Last 24 prompts as bars, then the monthly trend.
    const lastN = rows.slice(-24);
    const bars = h("div", { class: "recent-scores", role: "img", "aria-label": `Your last ${lastN.length} Prompt Scores` },
      lastN.map((r, i) => h("i", { class: P.band(r.s), title: `${P.when(r)}: ${P.tenText(r.s)}/10`, style: `height:${Math.max(6, (100 * r.s) / 7)}%;animation-delay:${i * 25}ms` })));
    root.append(h("div", { class: "grid2" },
      h("div", { class: "panel" },
        h("div", { class: "panel-head quip-head" }, h("h2", { text: "Every prompt" }), quip(rows)),
        bars,
        h("div", { class: "chips small" },
          h("span", { class: "muted", text: `Your last ${lastN.length}, oldest on the left:` }),
          h("span", { class: "legend" }, h("i", { class: "dot sq", style: "background:var(--accent)" }), "clear (5.7+)"),
          h("span", { class: "legend" }, h("i", { class: "dot sq", style: "background:var(--primary)" }), "okay"),
          h("span", { class: "legend" }, h("i", { class: "dot sq", style: "background:var(--danger)" }), "vague (under 2.9)"))),
      tokensMini()));

    // The factors, like the deck: how often each check shows up in your prompts.
    const feats = P.D.rules.features.map((f) => ({ f, pct: ((sc.features || {})[f.name] || {}).present_pct ?? 0 }));
    root.append(P.panel("What your score is made of", factorLine(feats),
      h("div", { class: "factors" }, feats.map(({ f, pct }) =>
        h("div", { class: "factor", title: f.tip },
          h("span", { text: f.label }),
          h("div", { class: "bar-track" }, h("div", { class: "bar-fill" + (pct >= 60 ? " lime" : ""), style: `width:${Math.max(2, pct)}%` })),
          h("span", { class: "w " + factorWord(pct), text: `${factorWord(pct)} · ${Math.round(pct)}%` })))),
      h("p", { class: "small muted", text: "Each check is one point of 7, scaled to 10. The rubric itself hasn't changed." })));

    root.append(h("div", { class: "grid2" }, toneBubbles(), improvePanel()));
    root.append(projectsMini());
  };

  // ── Your prompt sign: a zodiac-style read of how you prompt ─────────────
  // Counted by rules, not guessed: your sun sign is how you most often open a prompt, your rising
  // sign the next most common, and your moon the check you include most. For fun, from real data.
  const SIGNS = {
    commander: { name: "The Commander", glyph: "▲", line: "You lead with the verb. Claude knows who's in charge.", gift: "Fast, decisive asks", watch: "Commands skip the why" },
    sprinter: { name: "The Sprinter", glyph: "⚡", line: "Short, quick, gone. You prompt at the speed of thought.", gift: "Momentum", watch: "Claude fills in the blanks" },
    explorer: { name: "The Explorer", glyph: "✦", line: "You think out loud and follow the question.", gift: "Curiosity", watch: "Questions rarely say what 'done' is" },
    diplomat: { name: "The Diplomat", glyph: "❋", line: "Polite, collaborative, a pleasure to work with.", gift: "Clear asks", watch: "Kind words can hide a vague goal" },
    storyteller: { name: "The Storyteller", glyph: "☾", line: "You set the scene and let Claude find the plot.", gift: "Rich context", watch: "The actual ask can get lost" },
    architect: { name: "The Architect", glyph: "◆", line: "Context, limits, a plan. You hand Claude a blueprint.", gift: "Structure", watch: "Can over-specify small things" },
  };
  const STYLE_SIGN = { command: "commander", request: "diplomat", question: "explorer", statement: "storyteller" };
  function promptSign() {
    const os = (P.D.keywords || {}).opening_style || {};
    const order = ["command", "request", "question", "statement"].filter((k) => os[k]).sort((a, b) => os[b] - os[a]);
    const words = ((P.D.stats.prompt_length || {}).words || {}).median ?? 20;
    const fp = (n) => (((P.D.score || {}).features || {})[n] || {}).present_pct ?? 0;
    const pick = (style) => (style === "command" && words <= 8 ? "sprinter" : STYLE_SIGN[style]);
    let sun = order.length ? pick(order[0]) : "explorer";
    if ((fp("context") + fp("constraints")) / 2 >= 40) sun = "architect";
    const rising = order[1] ? pick(order[1]) : null;
    const feats = P.D.rules.features.map((f) => [f, fp(f.name)]).sort((a, b) => b[1] - a[1]);
    return { sun: SIGNS[sun], rising: rising && rising !== sun ? SIGNS[rising] : null, moon: feats[0] && feats[0][0], weak: feats[feats.length - 1] && feats[feats.length - 1][0] };
  }
  // Days in a row, ending on your last active day, with at least one clear prompt (5.7+).
  // Also your best run ever, shown when the current one is 0.
  function clearStreak(rows) {
    const days = new Set(rows.filter((r) => r.s >= 4).map((r) => r.d));
    let now = 0;
    const d = new Date(P.D.meta.last_day + "T12:00:00Z");
    while (days.has(d.toISOString().slice(0, 10))) { now++; d.setUTCDate(d.getUTCDate() - 1); }
    let best = 0, run = 0, prev = null;
    for (const day of [...days].sort()) {
      const t = Date.parse(day + "T12:00:00Z");
      run = prev != null && t - prev === 86400000 ? run + 1 : 1;
      best = Math.max(best, run); prev = t;
    }
    return { now, best };
  }
  function profileHeader(rows, all10, recent10, recentN) {
    const sg = promptSign();
    const st = P.D.stats;
    const stat = (n, label) => { const v = h("b", {}); P.countUp(v, n); return h("div", { class: "pstat" }, v, h("span", { text: label })); };
    return h("section", { class: "phead panel" },
      h("div", { class: "phead-main" },
        h("div", { class: "avatar", "aria-hidden": "true" }, h("span", { text: sg.sun.glyph })),
        h("div", { class: "phead-id" },
          h("span", { class: "eyebrow", text: "My profile · your prompt sign" }),
          h("h1", { text: sg.sun.name }),
          h("p", { class: "lede", text: sg.sun.line }),
          h("div", { class: "chips" },
            h("span", { class: "chip lime", text: `Sun ${sg.sun.glyph} ${sg.sun.name.replace("The ", "")}` }),
            sg.rising ? h("span", { class: "chip", text: `Rising ${sg.rising.glyph} ${sg.rising.name.replace("The ", "")}` }) : null,
            sg.moon ? h("span", { class: "chip", text: `Moon ☽ ${sg.moon.label}` }) : null)),
        h("div", { class: "phead-score" }, P.ring(all10, { band: bandOf10(all10), label: "Prompt Score", size: 132 }),
          h("span", { class: "small muted", text: recent10 != null ? `${recent10}/10 over your last ${recentN} (30 days)` : "your average" }))),
      h("div", { class: "pstats" },
        stat(rows.length, "scored prompts"),
        stat(st.sessions.with_prompts || st.totals.sessions, "chats"),
        stat(st.totals.projects, "projects"),
        (() => { const k = clearStreak(rows); return k.now ? stat(k.now, "day clear streak") : stat(k.best, "best clear streak (days)"); })()),
      h("div", { class: "traits" },
        h("div", {}, h("span", { class: "eyebrow", text: "Your gift" }), h("b", { text: sg.sun.gift })),
        h("div", {}, h("span", { class: "eyebrow", text: "Watch out for" }), h("b", { text: sg.sun.watch })),
        sg.weak ? h("div", {}, h("span", { class: "eyebrow", text: "Your next move" }), h("b", { text: `Add ${sg.weak.label.toLowerCase()}` })) : null),
      h("p", { class: "small muted", text: "Your sign comes from how your prompts open (sun, then rising) and the check you include most (moon). Counted by rules from your chats, just for fun." }));
  }

  // Your feed: your latest prompts, newest first, each with its score and what it was missing.
  function feedPanel(rows) {
    let limit = 4;
    const list = h("div", { class: "feed" });
    const more = h("button", { class: "btn", type: "button", text: "Show more" });
    const draw = () => {
      list.textContent = "";
      rows.slice(-limit).reverse().forEach((r, i) => {
        const has = new Set(r.f);
        const miss = P.D.rules.features.filter((f) => !has.has(f.name)).slice(0, 3);
        list.append(h("article", { class: "post lift", style: `animation-delay:${i * 50}ms` },
          h("div", { class: "post-score " + P.band(r.s) }, h("b", { text: P.tenText(r.s) }), h("span", { text: "/10" })),
          h("div", { class: "post-body" },
            h("div", { class: "post-meta" }, h("b", { text: project(r.p) }), h("span", { class: "muted", text: ` · ${P.when(r)}` }), r.tk ? h("span", { class: "muted", text: ` · ${P.fmtTok(r.tk)} tokens` }) : null),
            h("p", { class: "post-text", text: r.t }),
            miss.length ? h("div", { class: "chips small" }, miss.map((f) => h("span", { class: "chip", text: `+ ${f.label.toLowerCase()}` }))) : h("span", { class: "chip ok", text: "Every check" }),
            h("div", { class: "controls" }, h("button", { class: "linkish", type: "button", text: "Write a better version", onclick: () => { P.prefill = r.t; location.hash = "write"; } })))));
      });
      more.classList.toggle("hidden", rows.length <= limit);
    };
    more.addEventListener("click", () => { limit += 6; draw(); });
    draw();
    return h("div", { class: "panel" },
      h("div", { class: "panel-head" }, h("h2", { text: "Your feed" }), h("span", { class: "small muted", text: "your latest prompts, newest first" })),
      list, h("div", {}, more));
  }

  // Tone as bubbles: the biggest is how you most often open a prompt. Hover one to read it.
  function toneBubbles() {
    const os = (P.D.keywords || {}).opening_style || {};
    const kinds = [["command", "Commands", "var(--primary)", "You tell Claude what to do."], ["request", "Requests", "var(--accent)", "You ask nicely: can you, please."], ["question", "Questions", "var(--success)", "You ask and think out loud."], ["statement", "Statements", "var(--ink-muted)", "You describe and let Claude decide."]]
      .filter(([k]) => os[k]).sort((a, b) => os[b[0]] - os[a[0]]);
    const total = kinds.reduce((t, [k]) => t + os[k], 0);
    if (!total) return P.panel("Your tone", null, h("p", { class: "muted", text: "Not enough prompts yet." }));
    // Area ∝ count. The biggest sits centre-left; the rest orbit it.
    const maxR = 92;
    const r = (n) => Math.max(26, maxR * Math.sqrt(n / os[kinds[0][0]]));
    const spots = [[38, 50], [76, 28], [78, 74], [52, 88]];
    const field = h("div", { class: "bubbles", role: "list", "aria-label": "How your prompts open" },
      kinds.map(([k, label, color, what], i) => {
        const rad = r(os[k]), pct = Math.round((100 * os[k]) / total);
        const textInk = k === "request" ? "var(--on-accent)" : k === "statement" ? "var(--surface)" : "var(--on-primary)";
        return h("div", { class: "bubble", role: "listitem", tabindex: 0, title: `${label}: ${os[k]} prompts. ${what}`,
          style: `width:${rad * 2}px;height:${rad * 2}px;left:calc(${spots[i][0]}% - ${rad}px);top:calc(${spots[i][1]}% - ${rad}px);background:${color};color:${textInk};animation-delay:${i * -1.3}s` },
          h("b", { text: `${pct}%` }), h("span", { text: label }), h("em", { text: what }));
      }));
    const top = kinds[0];
    return P.panel("Your tone", "how your prompts open",
      h("p", { class: "big-line" }, "Mostly ", h("b", { class: "tone-word", text: top[1].toLowerCase() }), "."),
      field,
      h("p", { class: "small muted", text: "Bubble size is how often you open a prompt that way. Hover or tab to one to see what it means." }));
  }

  // A little line that changes with how clear your last few prompts were. Picked from your last 5
  // scores (and whether they beat the 5 before), so it moves as you send prompts. Tap to get another.
  const QUIPS = {
    great: ["Crystal clear. Claude barely has to guess.", "You're writing prompts Claude could frame and hang.", "Goal, context, done. Chef's kiss.", "Five clear prompts in a row? Claude owes you a coffee."],
    rising: ["Look at that climb. Your last few beat the ones before.", "Sharper every send. Keep stacking.", "Trending up: Claude can feel the difference.", "Getting clearer. The bars don't lie."],
    good: ["Solid. One more detail and it's a 10.", "Clear enough to work, close enough to shine.", "Good bones. Add how you'll know it worked.", "Halfway to telepathy. Say what 'done' looks like."],
    slipping: ["Quick ones lately? Claude's been filling in the blanks.", "A bit foggy recently. Name the file, name the goal.", "Your prompts got shorter than your patience. Happens.", "Dipped a little. One sentence of context fixes most of it."],
    vague: ["Claude is good, but it isn't a mind reader (yet).", "Short and mysterious. Great for poems, rough for code.", "'Fix it' is a vibe, not a prompt.", "Give Claude a map: what, where, and what 'done' means."],
    none: ["Send a task prompt and I'll have something to say.", "Nothing scored yet. Your first prompt sets the tone."],
  };
  function quip(rows) {
    const last = rows.slice(-5), before = rows.slice(-10, -5);
    const a = last.length ? tenOf(avg(last)) : null, b = before.length ? tenOf(avg(before)) : null;
    const mood = a == null ? "none" : a >= 5.7 ? "great" : b != null && a - b >= 1 ? "rising" : b != null && b - a >= 1 ? "slipping" : a >= 2.9 ? "good" : "vague";
    const pool = QUIPS[mood];
    let i = rows.length % pool.length;
    const text = h("span", { class: "quip-text", text: pool[i] });
    const btn = h("button", { class: "quip", type: "button", "aria-label": "Show another line", title: "Tap for another" },
      h("span", { class: "quip-mark", "aria-hidden": "true", text: "“" }), text,
      h("span", { class: "quip-meta", text: a == null ? "" : `last 5: ${a}/10${b != null ? ` · before: ${b}/10` : ""}` }));
    btn.addEventListener("click", () => {
      i = (i + 1) % pool.length;
      text.classList.remove("swap"); void text.offsetWidth; text.classList.add("swap");
      text.textContent = pool[i];
    });
    return btn;
  }

  // ── Tokens: exactly what your prompts cost, from the API's own numbers ───
  // Memory concept: the context window. Each reply re-sends the whole chat so far, so a prompt
  // deep in a long chat costs more than the same prompt in a fresh one.
  P.fmtTok = (n) => (n == null ? "–" : n >= 1e9 ? (n / 1e9).toFixed(2).replace(/\.?0+$/, "") + "B" : n >= 1e6 ? (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M" : n >= 1e3 ? Math.round(n / 1e3) + "K" : String(n));
  const T = () => P.D.tokens || null;
  function weekSums() {
    const t = T(); if (!t) return [0, 0];
    let a = 0, b = 0;
    for (const [d, v] of Object.entries(t.by_day)) { if (d > daysAgo(7)) a += v.total; else if (d > daysAgo(14)) b += v.total; }
    return [a, b];
  }
  function longChatLine() {
    const t = T(); if (!t) return null;
    const first = t.by_position[0], last = [...t.by_position].reverse().find((x) => x.mean && x.prompts >= 5);
    if (!first || !first.mean || !last || last === first) return null;
    const x = Math.round((10 * last.mean) / first.mean) / 10;
    return x >= 1.3 ? `A prompt after #${last.label.replace(/[–+].*/, "")} in a chat costs ${x}× one in the first five: Claude re-reads the whole chat every time. Start fresh with a short summary.` : null;
  }
  function tokensMini() {
    const t = T();
    if (!t) return P.panel("Your tokens", null, h("p", { class: "muted", text: "Run pompy publish to count your tokens." }));
    const [wk, prev] = weekSums();
    const tasks = P.D.prompts.filter((r) => r.tk && r.k === "typed");
    const per = tasks.length ? median(tasks.map((r) => r.tk)) : null;
    const big = h("div", { class: "value" });
    const tt = t.total.total;
    if (tt >= 1e9) P.countUp(big, tt / 1e9, { decimals: 2, suffix: "B" });
    else P.countUp(big, tt / 1e6, { decimals: 1, suffix: "M" });
    const line = longChatLine();
    return h("div", { class: "panel" },
      h("div", { class: "panel-head" }, h("h2", { text: "Your tokens" }),
        h("button", { class: "btn primary", type: "button", text: "Open token roadmap", onclick: (e) => openRoadmap(e.currentTarget) })),
      h("div", { class: "tok-row", role: "button", tabindex: 0, "aria-label": "Open token roadmap", style: "cursor:pointer",
        onclick: (e) => openRoadmap(e.currentTarget), onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openRoadmap(e.currentTarget); } } },
        h("div", { class: "tile" }, h("div", { class: "label", text: "All time" }), big, h("div", { class: "sub", text: `${t.reread_pct}% was Claude re-reading the chat` })),
        h("div", { class: "tile" }, h("div", { class: "label", text: "This week" }), h("div", { class: "value", text: P.fmtTok(wk) }),
          h("div", { class: "sub" }, prev ? h("span", { class: wk <= prev ? "better" : "worse", text: wk <= prev ? `▼ ${Math.round(100 - (100 * wk) / prev)}% vs last week` : `▲ ${Math.round((100 * wk) / prev - 100)}% vs last week` }) : "no data last week")),
        h("div", { class: "tile" }, h("div", { class: "label", text: "Per prompt" }), h("div", { class: "value", text: P.fmtTok(per) }), h("div", { class: "sub", text: "typical prompt, all replies included" }))),
      line ? h("p", { class: "note", text: line }) : null);
  }
  // ── Token roadmap: a pop-up that follows your tokens from the total to where they ended up ──
  // All tokens → which project → what Claude did with them (re-reading the chat, saving new
  // context, writing, new input). Ribbon widths are the real token counts.
  const KINDS = [["cache_read", "Re-reading the chat", "var(--primary)"], ["cache_write", "Saving new context", "var(--accent)"], ["output", "Claude writing", "var(--success)"], ["input", "New input", "var(--ink-muted)"]];
  function roadmapFlow(t) {
    const tot = t.total.total || 1;
    const projs = Object.entries(t.by_project);
    const top = projs.slice(0, 5);
    const rest = projs.slice(5);
    if (rest.length) {
      const o = { input: 0, output: 0, cache_read: 0, cache_write: 0, total: 0 };
      rest.forEach(([, v]) => Object.keys(o).forEach((k) => (o[k] += v[k] || 0)));
      top.push([`${rest.length} other projects`, o]);
    }
    const W = 900, H = 380, NW = 14, GAP = 10, X0 = 20, X1 = 420, X2 = W - 20 - NW;
    const usable = H - GAP * (top.length - 1);
    const sc = (v) => (usable * v) / tot;
    const svg = s("svg", { class: "flow", viewBox: `0 0 ${W} ${H + 24}`, role: "img",
      "aria-label": `Your ${P.fmtTok(tot)} tokens by project and by what Claude did with them` });
    // Left: all tokens.
    svg.append(s("rect", { x: X0, y: 0, width: NW, height: H, rx: 4, fill: "var(--ink)" }));
    svg.append(s("text", { x: X0, y: H + 18, class: "strong" }, `All tokens · ${P.fmtTok(tot)}`));
    // Middle: projects; right: kinds.
    let yL = 0, yM = 0;
    const kindY = {}; let yR = 0;
    const kindH = KINDS.map(([k]) => sc(t.total[k]));
    const rGap = (H - kindH.reduce((a, b) => a + b, 0)) / Math.max(1, KINDS.length - 1);
    KINDS.forEach(([k], i) => { kindY[k] = yR; yR += kindH[i] + rGap; });
    const kindFill = { ...kindY };
    const ribbon = (x0, y0, x1, y1, w, color, title, i) => {
      const mx = (x0 + x1) / 2;
      const d = `M${x0},${y0} C${mx},${y0} ${mx},${y1} ${x1},${y1} L${x1},${y1 + w} C${mx},${y1 + w} ${mx},${y0 + w} ${x0},${y0 + w} Z`;
      return s("path", { class: "rib", d, fill: color, "fill-opacity": 0.28, style: `animation-delay:${i * 40}ms` }, s("title", {}, title));
    };
    let i = 0;
    top.forEach(([name, v]) => {
      const hgt = Math.max(1.5, sc(v.total));
      svg.append(ribbon(X0 + NW, yL, X1, yM, hgt, "var(--primary)", `${project(name)}: ${P.fmtTok(v.total)} (${((100 * v.total) / tot).toFixed(1)}%)`, i++));
      yL += hgt;
      // This project's tokens split by kind, flowing right.
      let yy = yM;
      KINDS.forEach(([k, label, color]) => {
        const w = sc(v[k] || 0);
        if (w < 0.4) return;
        svg.append(ribbon(X1 + NW, yy, X2, kindFill[k], w, color, `${project(name)} → ${label}: ${P.fmtTok(v[k])}`, i++));
        yy += w; kindFill[k] += w;
      });
      svg.append(s("rect", { x: X1, y: yM, width: NW, height: hgt, rx: 3, fill: "var(--primary)" }));
      if (hgt >= 14) svg.append(s("text", { x: X1 - 8, y: yM + hgt / 2 + 4, "text-anchor": "end", class: "strong" }, `${project(name).slice(0, 26)} · ${P.fmtTok(v.total)}`));
      yM += hgt + GAP;
    });
    KINDS.forEach(([k, label, color], j) => {
      if (kindH[j] < 0.5) return;
      svg.append(s("rect", { x: X2, y: kindY[k], width: NW, height: Math.max(2, kindH[j]), rx: 3, fill: color }));
      svg.append(s("text", { x: X2 - 8, y: kindY[k] + Math.max(2, kindH[j]) / 2 + 4, "text-anchor": "end", class: "strong" }, `${label} · ${((100 * t.total[k]) / tot).toFixed(1)}%`));
    });
    return svg;
  }
  function roadmapStops(t) {
    const tot = t.total.total || 1;
    const [topName, topV] = Object.entries(t.by_project)[0] || [];
    const days = Object.entries(t.by_day).sort((a, b) => b[1].total - a[1].total);
    const chat = t.top_chats[0];
    const first = t.by_position[0], deep = [...t.by_position].reverse().find((x) => x.mean && x.prompts >= 5);
    const stops = [
      ["Start", `${P.fmtTok(tot)} tokens`, `Every reply Claude sent from ${P.niceDate(P.D.meta.first_day, true)} to ${P.niceDate(P.D.meta.last_day, true)}, ${t.replies.toLocaleString()} replies in all.`],
      topName ? ["Most went to", project(topName), `${P.fmtTok(topV.total)}, or ${Math.round((100 * topV.total) / tot)}% of everything.`] : null,
      ["Mostly spent on", "Re-reading the chat", `${t.reread_pct}%. Claude keeps no memory between replies, so each one re-sends the chat so far: that's the context window.`],
      days[0] ? ["Biggest day", P.niceDate(days[0][0], true), `${P.fmtTok(days[0][1].total)} in one day.`] : null,
      chat ? ["Costliest chat", chat.si ? `Chat ${chat.si} · ${project(chat.project)}` : `An agent run · ${project(chat.project)}`, `${P.fmtTok(chat.total)} over ${chat.prompts} prompts and ${chat.replies} replies.`] : null,
      first && deep && deep !== first ? ["Where it adds up", `Prompts ${deep.label} in a chat`, `${P.fmtTok(deep.mean)} each, vs ${P.fmtTok(first.mean)} for the first five.`] : null,
      ["Next stop", "Start fresh sooner", "After about 15 prompts, open a new chat with a 3-line summary. The next prompt re-reads a summary instead of the whole chat."],
    ].filter(Boolean);
    return h("ol", { class: "road" }, stops.map(([k, v, why], j) =>
      h("li", { style: `animation-delay:${120 + j * 70}ms`, class: j === stops.length - 1 ? "goal" : "" },
        h("span", { class: "eyebrow", text: k }), h("b", { text: v }), h("span", { class: "small muted", text: why }))));
  }
  function openRoadmap(from) {
    const t = T(); if (!t) return;
    const close = h("button", { class: "icon-btn", type: "button", "aria-label": "Close", text: "✕" });
    const dlg = h("dialog", { class: "roadmap", "aria-labelledby": "roadmap-title" },
      h("div", { class: "panel-head" },
        h("div", {}, h("span", { class: "eyebrow", text: "Token roadmap" }), h("h2", { id: "roadmap-title", text: "Where your tokens went" })), close),
      h("div", { class: "chart-scroll" }, roadmapFlow(t)),
      h("div", { class: "chips small" }, KINDS.map(([k, l, c]) => h("span", { class: "legend" }, h("i", { class: "dot sq", style: `background:${c}` }), l))),
      roadmapStops(t),
      h("div", { class: "controls" }, h("a", { class: "btn", href: "#analytics", text: "All token details", onclick: () => dlg.close() })));
    const done = () => { dlg.remove(); if (from && from.focus) from.focus(); };
    close.addEventListener("click", () => dlg.close());
    dlg.addEventListener("close", done);
    dlg.addEventListener("click", (e) => { // a click on the dimmed backdrop closes it
      const r = dlg.getBoundingClientRect();
      if (e.target === dlg && (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom)) dlg.close();
    });
    document.body.append(dlg);
    dlg.showModal();
  }

  function median(xs) { const o = [...xs].sort((a, b) => a - b); const m = o.length >> 1; return o.length % 2 ? o[m] : (o[m - 1] + o[m]) / 2; }

  function tokensSection() {
    const t = T(); if (!t) return [];
    const days = Object.entries(t.by_day).filter(([d]) => d > daysAgo(30));
    const max = Math.max(1, ...days.map(([, v]) => v.total));
    const W = 900, H = 160, B = 22, bw = (W / Math.max(1, days.length)) * 0.7;
    const svg = s("svg", { class: "chart", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Tokens per day, last 30 days" },
      days.map(([d, v], i) => {
        const x = (i + 0.15) * (W / days.length), hgt = ((H - B - 8) * v.total) / max;
        return s("g", { class: "tbar" },
          s("rect", { x, y: H - B - hgt, width: bw, height: Math.max(1, hgt), rx: 3, fill: "var(--primary)", style: `animation-delay:${i * 18}ms` }, s("title", {}, `${P.niceDate(d)}: ${P.fmtTok(v.total)} tokens (${P.fmtTok(v.output)} written by Claude)`)),
          i % Math.ceil(days.length / 6) === 0 ? s("text", { x: x + bw / 2, y: H - 6, "text-anchor": "middle" }, P.niceDate(d)) : null);
      }));
    const projects = Object.entries(t.by_project).slice(0, 8).map(([name, v]) => ({ name: project(name), value: v.total }));
    const pos = t.by_position.filter((b) => b.mean);
    const kinds = [["cache_read", "Re-reading the chat", "var(--primary)"], ["cache_write", "Saving new context", "var(--accent)"], ["output", "Claude writing", "var(--success)"], ["input", "New input", "var(--ink-muted)"]];
    const tot = t.total.total || 1;
    const chats = h("table", { class: "cmp" },
      h("thead", {}, h("tr", {}, ["Chat", "Project", "Prompts", "Replies", "Tokens"].map((x) => h("th", { text: x })))),
      h("tbody", {}, t.top_chats.map((c) => h("tr", {},
        h("td", { text: c.si ? `Chat ${c.si}` : "Agent run", title: c.si ? "" : "A chat with no typed prompt: a subagent or tool run" }), h("td", { text: project(c.project) }), h("td", { class: "num", text: c.prompts }),
        h("td", { class: "num", text: c.replies }), h("td", { class: "num", text: P.fmtTok(c.total) })))));
    return [
      h("h2", { class: "section-title", text: "Tokens" }),
      h("p", { class: "lede", text: `Exact counts from Claude Code's own records: ${P.fmtTok(tot)} tokens over ${t.replies.toLocaleString()} replies. Chats in the Claude app aren't included: they don't record tokens.` }),
      h("div", { class: "grid2" },
        P.panel("Where they go", "every reply re-sends the chat so far",
          h("div", { class: "tone", role: "img", "aria-label": kinds.map(([k, l]) => `${l}: ${Math.round((100 * t.total[k]) / tot)}%`).join(", ") },
            kinds.filter(([k]) => t.total[k]).map(([k, l, c]) => h("div", { title: `${l}: ${P.fmtTok(t.total[k])}`, style: `flex:${t.total[k]};background:${c}` }))),
          h("div", { class: "chips small" }, kinds.map(([k, l, c]) => h("span", { class: "legend" }, h("i", { class: "dot sq", style: `background:${c}` }), `${l} ${((100 * t.total[k]) / tot).toFixed(1)}%`))),
          h("p", { class: "small muted", text: "Re-reading is the context window at work: Claude has no memory between replies, so it reads the whole chat again each time. The prompt cache makes that cheaper, not free." })),
        P.panel("Cost by place in the chat", "average tokens for one prompt",
          P.bars(pos.map((b) => ({ name: `prompts ${b.label}`, value: b.mean })), { fmtNum: P.fmtTok }),
          longChatLine() ? h("p", { class: "note", text: longChatLine() }) : null)),
      P.panel("Tokens per day", "last 30 days", h("div", { class: "chart-scroll" }, svg)),
      h("div", { class: "grid2" },
        P.panel("By project", "all time", P.bars(projects, { fmtNum: P.fmtTok })),
        P.panel("Your costliest chats", `top ${t.top_chats.length}`, h("div", { class: "scroll-x" }, chats))),
    ];
  }

  // The factor panel's corner line: your strongest check and the one you skip most, with a wink.
  function factorLine(feats) {
    if (!feats.length) return "";
    const sorted = [...feats].sort((a, b) => b.pct - a.pct);
    const best = sorted[0], worst = sorted[sorted.length - 1];
    const B = best.f.label.toLowerCase(), W = worst.f.label.toLowerCase();
    const lines = [
      `${best.f.label} is your superpower. ${worst.f.label} is your plot twist.`,
      `You nail ${B}. Claude is still waiting on ${W}.`,
      `Strong on ${B}, shy on ${W}.`,
      `${Math.round(best.pct)}% ${B}, ${Math.round(worst.pct)}% ${W}. One of these needs a hug.`,
    ];
    return lines[scored().length % lines.length];
  }




  function improvePanel() {
    const hs = P.D.habits || [];
    if (!hs.length) return P.panel("How to improve", null, h("p", { class: "muted", text: "Pompy needs more prompts to say." }));
    return P.panel("How to improve", "your three biggest gaps",
      h("ol", { class: "steps", style: "gap:12px" }, hs.map((hb) => h("li", {},
        h("b", { text: `Add ${hb.label.toLowerCase()}. ` }), h("span", { class: "muted", text: hb.tip }),
        h("div", { class: "small muted", text: `In ${hb.present_pct}% of your task prompts${hb.this_month != null && hb.before != null ? `; ${hb.this_month}% this month vs ${hb.before}% before` : ""}.` })))),
      h("div", { class: "controls" }, h("a", { class: "btn", href: "#insights", text: "Practice with your own prompts" })));
  }

  function projectsMini() {
    const rows = projectRows().slice(0, 5);
    if (!rows.length) return null;
    return P.panel("Projects you work in", "your five busiest",
      h("div", {}, rows.map(projectRow)),
      h("div", { class: "controls" }, h("a", { class: "btn", href: "#projects", text: "All projects" })));
  }

  // ── Projects ─────────────────────────────────────────────────────────────
  function projectRows() {
    const list = P.D.stats.projects || [];
    const by = new Map();
    P.D.prompts.forEach((r) => { if (!by.has(r.p)) by.set(r.p, []); by.get(r.p).push(r); });
    return list.map((x) => {
      const rs = by.get(x.name) || [];
      const tasks = rs.filter((r) => r.k === "typed" && r.r === "task" && r.s != null);
      const judged = rs.filter((r) => r.v && r.v !== "no reaction");
      const bad = judged.filter((r) => r.v === "unclear" || r.v === "missed").length;
      return { name: x.name, prompts: x.prompts, last: (x.last_prompt || "").slice(0, 10), score: tenOf(avg(tasks)), n: tasks.length, miss: judged.length >= 5 ? Math.round((100 * bad) / judged.length) : null };
    }).sort((a, b) => b.prompts - a.prompts);
  }
  function projectRow(x) {
    return h("div", { class: "proj" },
      h("div", {}, h("div", { class: "nm", text: project(x.name) }),
        h("div", { class: "sub", text: `${x.prompts.toLocaleString()} prompts${x.last ? ` · last ${P.niceDate(x.last, true)}` : ""}${x.miss != null ? ` · ${x.miss}% of replies missed or asked` : ""}` })),
      h("div", { class: "stats" }, x.score != null ? h("span", { class: "pill " + (bandOf10(x.score) === "good" ? "" : bandOf10(x.score) === "ok" ? "mid" : "low"), title: `Average Prompt Score over ${x.n} task prompts`, text: `${x.score}/10` }) : h("span", { class: "pill none", text: "·" })));
  }

  P.views.projects = function (root) {
    const rows = projectRows();
    root.append(head("Projects", "What you're working on",
      "Every folder you've used Claude Code in, with how your prompts score there and how often Claude missed."));
    root.append(P.panel("All projects", `${rows.length} in all, busiest first`, rows.length ? h("div", {}, rows.map(projectRow)) : h("p", { class: "muted", text: "No projects yet." })));
    const goals = P.goalsPanel && P.goalsPanel();
    if (goals) root.append(goals);
    const by = P.projectsPanel && P.projectsPanel();
    if (by) root.append(P.fold(by));
  };

  // ── Analytics ────────────────────────────────────────────────────────────
  P.views.analytics = function (root) {
    const st = P.D.stats, kw = P.D.keywords || {};
    root.append(head("Analytics", "Your numbers",
      `Usage times, models, tokens and keywords, from ${P.niceDate(P.D.meta.first_day, true)} to ${P.niceDate(P.D.meta.last_day, true)}. Times are in ${P.D.meta.timezone}.`));
    const am = st.sessions.active_minutes || {};
    root.append(h("div", { class: "tiles" },
      countTile("Prompts", st.totals.prompts, `${(st.totals.prompts_by_kind.typed || 0).toLocaleString()} typed, the rest commands`),
      countTile("Chats", st.totals.sessions, `${st.sessions.with_subagents || 0} used subagents`),
      countTile("Active days", st.time.active_days, `busiest: ${st.time.peak_weekday}s`),
      countTile("Peak hour", st.time.peak_hour, "o'clock, your busiest", "hl"),
      am.median != null ? countTile("Chat length", am.median, "minutes, at the median") : null));

    root.append(h("div", { class: "grid2" },
      P.panel("When you work", "prompts by weekday and hour", heat(st.time.weekday_hour_grid),
        P.bars(Object.entries(st.time.by_part_of_day).map(([name, value]) => ({ name, value })))),
      P.panel("Models", "share of Claude's replies",
        P.bars(Object.entries(st.models.share).sort((a, b) => b[1] - a[1]).map(([id, v]) => ({ name: P.modelName(id), value: v })), { suffix: "%", max: 100 }),
        h("h3", { text: "Effort" }),
        P.bars(Object.entries(st.effort.share).sort((a, b) => b[1] - a[1]).map(([name, value]) => ({ name, value })), { suffix: "%", max: 100 }),
        st.surfaces ? h("p", { class: "small muted", text: "Where you chat: " + Object.entries(st.surfaces).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${({ cli: "terminal", "claude-desktop": "desktop app", "claude-vscode": "VS Code" })[k] || k} ${v.toLocaleString()}`).join(", ") + "." }) : null)));

    const terms = kw.top_terms || [];
    const phrases = kw.top_phrases || [];
    const verbs = Object.entries((kw.action_verbs || {}).opening || {}).sort((a, b) => b[1] - a[1]).slice(0, 8);
    root.append(h("div", { class: "grid2" },
      P.panel("Keywords", "the words you use most",
        h("div", { class: "kw" }, terms.slice(0, 30).map((t, i) => h("span", { class: i < 3 ? "top" : "", title: `${t.prompts} prompts in ${t.sessions} chats` }, t.term, h("b", { text: t.count })))),
        phrases.length ? h("h3", { text: "Phrases" }) : null,
        phrases.length ? h("div", { class: "kw" }, phrases.slice(0, 12).map((p) => h("span", {}, p.phrase, h("b", { text: p.prompts })))) : null),
      P.panel("How you start", "the first verb of your prompts",
        P.bars(verbs.map(([name, value]) => ({ name, value }))),
        h("h3", { text: "Prompt length" }),
        P.bars(Object.entries(st.prompt_length.buckets).map(([name, value]) => ({ name: `${name} words`, value }))))));

    root.append(...tokensSection());
    if (P.dayTimeline) root.append(P.dayTimeline());
    const setup = P.setupPanel && P.setupPanel();
    if (setup) root.append(P.fold(setup));
  };

  // Weekday × hour grid: darker is busier.
  function heat(grid) {
    if (!grid || !grid.length) return null;
    const max = Math.max(1, ...grid.flat());
    const box = h("div", { class: "heat", role: "img", "aria-label": "Prompts by weekday and hour" });
    grid.forEach((row, d) => {
      box.append(h("span", { class: "rl", text: P.WD[d] }));
      row.forEach((n, hr) => box.append(h("span", { class: "cell", title: `${P.WD[d]} ${hr}:00 · ${n} prompts`,
        style: `opacity:${n ? (0.15 + 0.85 * (n / max)).toFixed(2) : 0.06};animation-delay:${(hr * 12 + d * 20)}ms` })));
    });
    box.append(h("span", {}));
    for (let hr = 0; hr < 24; hr++) box.append(h("span", { class: "cl", text: hr % 6 === 0 ? String(hr) : "" }));
    return box;
  }

  // ── Plan before you send ─────────────────────────────────────────────────
  // A space to think the task through first. Each field is one check of the rubric; Pompy turns
  // what you fill in into a prompt, scores it live, and hands it to step 2.
  const PLAN = [
    ["goal", "What do you want done?", "The action and the thing it acts on.", "Fix the password reset on the web login", (t) => t],
    ["context", "What's going on right now?", "Why, or where: which app or page, what happens now.", "it fails after a reset and sends people to a blank page", (t) => `Right now, ${t}`],
    ["constraints", "What must stay the same?", "Limits: what to keep, avoid, or match.", "don't change the API", (t) => `Make sure ${t}`],
    ["done", "How will you know it worked?", "Tests pass, the page shows X.", "the reset-then-login test passes", (t) => `Done when ${t}`],
    ["reference", "Point at something exact", "A file, error text, URL or code.", "src/auth/reset.ts", (t) => `See ${t}`],
    ["example", "Anything to copy?", "A screenshot or \"like the pricing page\".", "like the sign-up flow", (t) => `For example, ${t}`],
    ["format", "What should the answer look like?", "A list, a table, short, code only.", "a short summary and the diff", (t) => `Format: ${t}`],
  ];
  const sentence = (t) => { t = t.trim().replace(/\s+/g, " "); if (!t) return ""; t = t[0].toUpperCase() + t.slice(1); return /[.!?]$/.test(t) ? t : t + "."; };

  P.views.plan = function (root) {
    root.append(head("Plan & write", "Plan before you send",
      "Good planning matters as much as the task. Think it through here, one check at a time, then send the draft to step 2 to polish it."));
    const fields = {};
    const ringBox = h("div", {});
    const draftEl = h("div", { class: "quote after", style: "min-height:3em" });
    const status = h("span", { class: "muted small" });
    const send = h("button", { class: "btn primary", type: "button", text: "Use this draft", disabled: true });
    const think = h("button", { class: "btn", type: "button", text: "Ask Claude what's unclear", disabled: true });
    const questions = h("div", { class: "quote hidden" });

    const compose = () => PLAN.map(([k, , , , wrap]) => { const v = fields[k].value.trim(); return v ? sentence(wrap(v)) : ""; }).filter(Boolean).join(" ");
    let lastScore = null;
    function draw() {
      PLAN.forEach(([k]) => fields[k].box.classList.toggle("done", !!fields[k].value.trim()));
      const text = compose();
      draftEl.textContent = text || "Your prompt builds here as you plan.";
      draftEl.classList.toggle("muted", !text);
      send.disabled = !text; think.disabled = !fields.goal.value.trim();
      const r = text ? P.S.scoreText(text, false, false) : null;
      const score = r && r.score != null ? r.score : null;
      if (score !== lastScore) {
        lastScore = score;
        ringBox.textContent = "";
        ringBox.append(h("div", { class: "score-card" }, P.ring(score == null ? null : P.ten(score), { size: 112, band: P.band(score), label: "out of 10" }),
          h("div", { style: "display:flex;flex-direction:column;gap:6px" },
            h("span", { class: "score-word " + P.band(score), text: score == null ? "Start with the goal" : P.bandWord(score) }),
            h("span", { class: "small muted", text: score == null ? "Fill in what you know." : `${score} of 7 checks` }))));
      }
    }
    const list = h("div", { class: "plan-fields" }, PLAN.map(([k, label, hint, ph], i) => {
      const ta = h("textarea", { id: "plan-" + k, rows: 2, placeholder: `e.g. ${ph}` });
      const box = h("div", { class: "plan-field" },
        h("label", { for: "plan-" + k }, h("span", { class: "tick", "aria-hidden": "true", text: "✓" }), `${i + 1}. ${label}`),
        h("span", { class: "hint", text: hint }), ta);
      fields[k] = { value: "", box, ta };
      Object.defineProperty(fields[k], "value", { get: () => ta.value });
      ta.addEventListener("input", draw);
      return box;
    }));

    send.addEventListener("click", () => {
      const text = compose();
      if (P.useDraft) P.useDraft(text); else { P.prefill = text; location.hash = "ask"; }
      status.textContent = "Sent to step 2 below.";
      setTimeout(() => (status.textContent = ""), 2400);
    });
    // Optional: Claude reads the plan and asks what it would otherwise have to guess.
    think.addEventListener("click", async () => {
      const sample = await P.cap("sample");
      if (!sample) { status.textContent = "Asking Claude isn't available in this view."; return; }
      think.disabled = true;
      questions.classList.remove("hidden");
      questions.textContent = "";
      status.textContent = "Claude is reading your plan…";
      const ask = ["You are Pompy, a prompting coach. A developer is planning a prompt for Claude Code.",
        "List the 2 or 3 questions Claude would most need answered to do this well, as short numbered lines.",
        "Only ask about what the plan leaves open. No preamble.", "", "Plan:", compose()].join("\n");
      try {
        const res = await sample(ask, { onText: ({ text }) => (questions.textContent = text) });
        questions.textContent = res.text.trim();
        status.textContent = "";
      } catch (e) {
        status.textContent = "Claude couldn't answer (" + ((e && (e.code || e.message)) || "error") + ").";
      }
      think.disabled = false;
    });

    root.append(h("div", { class: "plan" },
      h("div", { class: "panel" }, h("span", { class: "eyebrow", text: "Step 1" }), h("h2", { text: "Think it through" }), list),
      h("div", { class: "plan-side" },
        h("div", { class: "panel" }, h("span", { class: "eyebrow", text: "Live Prompt Score" }), ringBox,
          h("h3", { text: "Your draft" }), draftEl,
          h("div", { class: "controls" }, send, think, status), questions))));
    draw();
  };
})();
