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
    const last = rows[rows.length - 1];
    const recent = rows.filter((r) => r.d > daysAgo(30));
    const all10 = tenOf(avg(rows)), recent10 = tenOf(avg(recent));
    const sc = P.D.score || {};
    root.append(head("My profile", "Your Prompt Score",
      "Every task prompt gets a score out of 10 from 7 checks. Your average combines them all. Replies like \"ok go\" and routine commands aren't scored."));

    // Latest and average, side by side.
    const latest = h("div", { class: "panel" },
      h("div", { class: "panel-head" }, h("span", { class: "eyebrow", text: "Latest prompt" }), last ? h("span", { class: "small muted", text: P.when(last) }) : null),
      last
        ? h("div", { class: "score-card" }, P.ring(P.ten(last.s), { band: P.band(last.s), label: "out of 10" }),
            h("div", { style: "display:flex;flex-direction:column;gap:8px" },
              h("span", { class: "score-word " + P.band(last.s), text: P.bandWord(last.s) }),
              h("p", { class: "small", style: "display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;overflow-wrap:anywhere", text: last.t }),
              missingLine(last)))
        : h("p", { class: "muted", text: "No scored prompts yet." }));
    const average = h("div", { class: "panel" },
      h("div", { class: "panel-head" }, h("span", { class: "eyebrow", text: "Your average" }), h("span", { class: "small muted", text: `${rows.length.toLocaleString()} prompts` })),
      h("div", { class: "score-card" }, P.ring(all10, { band: bandOf10(all10), label: "all-time" }),
        h("div", { style: "display:flex;flex-direction:column;gap:8px" },
          h("span", { class: "score-word " + bandOf10(all10), text: wordOf10(all10) }),
          recent10 != null
            ? h("p", { class: "small" }, "Last 30 days: ", h("b", { text: `${recent10}/10` }), deltaSpan(recent10, all10), h("span", { class: "muted", text: ` · ${recent.length} prompts` }))
            : h("p", { class: "small muted", text: "No prompts in the last 30 days." }),
          h("p", { class: "small muted", text: `Snapshot from ${P.niceDate(P.D.meta.today, true)}. The notch and side panel update after every prompt.` }))));
    root.append(h("div", { class: "profile-top" }, latest, average));

    // Last 24 prompts as bars, then the monthly trend.
    const lastN = rows.slice(-24);
    const bars = h("div", { class: "recent-scores", role: "img", "aria-label": `Your last ${lastN.length} Prompt Scores` },
      lastN.map((r, i) => h("i", { class: P.band(r.s), title: `${P.when(r)}: ${P.tenText(r.s)}/10`, style: `height:${Math.max(6, (100 * r.s) / 7)}%;animation-delay:${i * 25}ms` })));
    root.append(h("div", { class: "grid2" },
      P.panel("Every prompt", `your last ${lastN.length}, oldest on the left`, quip(rows), bars,
        h("div", { class: "chips small" },
          h("span", { class: "legend" }, h("i", { class: "dot sq", style: "background:var(--accent)" }), "clear (5.7+)"),
          h("span", { class: "legend" }, h("i", { class: "dot sq", style: "background:var(--primary)" }), "okay"),
          h("span", { class: "legend" }, h("i", { class: "dot sq", style: "background:var(--danger)" }), "vague (under 2.9)"))),
      P.panel("Your average by month", "out of 10", trend((sc.score || {}).by_month || {}))));

    // The factors, like the deck: how often each check shows up in your prompts.
    const feats = P.D.rules.features.map((f) => ({ f, pct: ((sc.features || {})[f.name] || {}).present_pct ?? 0 }));
    root.append(P.panel("What your score is made of", factorLine(feats),
      h("div", { class: "factors" }, feats.map(({ f, pct }) =>
        h("div", { class: "factor", title: f.tip },
          h("span", { text: f.label }),
          h("div", { class: "bar-track" }, h("div", { class: "bar-fill" + (pct >= 60 ? " lime" : ""), style: `width:${Math.max(2, pct)}%` })),
          h("span", { class: "w " + factorWord(pct), text: `${factorWord(pct)} · ${Math.round(pct)}%` })))),
      h("p", { class: "small muted", text: "Each check is one point of 7, scaled to 10. The rubric itself hasn't changed." })));

    root.append(h("div", { class: "grid2" }, tonePanel(), improvePanel()));
    root.append(projectsMini());
  };

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

  function missingLine(r) {
    const has = new Set(r.f);
    const miss = P.D.rules.features.filter((f) => !has.has(f.name)).slice(0, 2);
    if (!miss.length) return h("p", { class: "small better", text: "Every check is there." });
    return h("p", { class: "small" }, h("span", { class: "muted", text: "Next time add " }), h("b", { text: miss.map((f) => f.label.toLowerCase()).join(" and ") }), ".");
  }
  function deltaSpan(a, b) {
    const d = Math.round((a - b) * 10) / 10;
    if (!d) return h("span", { class: "muted", text: " · same as usual" });
    return h("span", { class: d > 0 ? "better" : "worse", text: d > 0 ? ` ▲ ${d}` : ` ▼ ${-d}` });
  }

  // A small area chart of the monthly average, on the 10-point scale.
  function trend(byMonth) {
    const months = Object.keys(byMonth).sort();
    if (months.length < 2) return h("p", { class: "muted small", text: "A trend needs two months of prompts." });
    const pts = months.map((m) => tenOf(byMonth[m]));
    const W = 520, H = 120, L = 28, R = 12, T = 12, B = 22;
    const x = (i) => L + ((W - L - R) * i) / (months.length - 1);
    const y = (v) => T + (H - T - B) * (1 - v / 10);
    const line = pts.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
    const area = `${line}L${x(pts.length - 1)},${y(0)}L${x(0)},${y(0)}Z`;
    const path = s("path", { class: "ln", d: line });
    const svg = s("svg", { class: "spark", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": months.map((m, i) => `${m}: ${pts[i]}`).join(", ") },
      [0, 5, 10].map((v) => s("text", { x: L - 6, y: y(v) + 4, "text-anchor": "end" }, v)),
      s("path", { class: "area", d: area }), path,
      pts.map((v, i) => s("circle", { cx: x(i), cy: y(v), r: 4 }, s("title", {}, `${months[i]}: ${v}/10`))),
      months.map((m, i) => s("text", { x: x(i), y: H - 4, "text-anchor": i === 0 ? "start" : i === months.length - 1 ? "end" : "middle" }, P.MON[+m.slice(5) - 1])));
    // Draw the line in once, left to right.
    if (!P.calm()) requestAnimationFrame(() => {
      const len = path.getTotalLength ? path.getTotalLength() : 0;
      if (!len) return;
      path.style.strokeDasharray = len; path.style.strokeDashoffset = len;
      path.getBoundingClientRect();
      path.style.transition = "stroke-dashoffset 1200ms cubic-bezier(0.23, 1, 0.32, 1)";
      path.style.strokeDashoffset = 0;
    });
    const first = pts[0], lastV = pts[pts.length - 1];
    const say = lastV > first ? `Up from ${first} in ${P.MON[+months[0].slice(5) - 1]}.` : lastV < first ? `Down from ${first} in ${P.MON[+months[0].slice(5) - 1]}.` : "Steady.";
    return h("div", {}, svg, h("p", { class: "small muted", text: say }));
  }

  // Tone: how your prompts open, counted by the same rules as the scorer (no AI guessing).
  function tonePanel() {
    const kw = P.D.keywords || {};
    const os = kw.opening_style || {};
    const order = [["command", "Commands", "var(--primary)"], ["request", "Requests", "var(--accent)"], ["question", "Questions", "var(--success)"], ["statement", "Statements", "var(--ink-muted)"]];
    const total = order.reduce((t, [k]) => t + (os[k] || 0), 0);
    if (!total) return P.panel("Your tone", null, h("p", { class: "muted", text: "Not enough prompts yet." }));
    const top = [...order].sort((a, b) => (os[b[0]] || 0) - (os[a[0]] || 0))[0];
    const words = (P.D.stats.prompt_length || {}).words || {};
    const sm = kw.style_markers || {};
    const typed = P.D.stats.totals.prompts_by_kind.typed || 1;
    const polite = Math.round((100 * ((sm.please || 0) + (sm.thanks || 0))) / typed);
    const firm = Math.round((100 * ((sm["don't / never (constraint)"] || 0) + (sm["must / should"] || 0))) / typed);
    const desc = {
      command: "You mostly give direct instructions.",
      request: "You mostly ask politely for what you want.",
      question: "You mostly ask questions and think out loud.",
      statement: "You mostly describe the situation and let Claude decide.",
    }[top[0]];
    return P.panel("Your tone", "how your prompts open",
      h("p", { class: "big-line", text: desc }),
      h("div", { class: "tone", role: "img", "aria-label": order.map(([k, l]) => `${l}: ${os[k] || 0}`).join(", ") },
        order.filter(([k]) => os[k]).map(([k, l, c]) => h("div", { title: `${l}: ${os[k]}`, style: `flex:${os[k]};background:${c}` }))),
      h("div", { class: "chips small" }, order.map(([k, l, c]) =>
        h("span", { class: "legend" }, h("i", { class: "dot sq", style: `background:${c}` }), `${l} ${Math.round((100 * (os[k] || 0)) / total)}%`))),
      h("ul", { class: "small", style: "margin:0;padding-left:1.2em;display:flex;flex-direction:column;gap:4px" },
        words.median != null ? h("li", { text: `Your typical prompt is ${words.median} words; the longest tenth run past ${words.p90}.` }) : null,
        h("li", { text: `"Please" or "thanks" shows up in ${polite}% of your prompts.` }),
        h("li", { text: `Firm limits ("don't", "must", "should") show up in ${firm}%.` })),
      h("p", { class: "small muted", text: "Counted by rules from how each prompt starts. It describes your wording, not you." }));
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
      `Usage times, models and keywords, from ${P.niceDate(P.D.meta.first_day, true)} to ${P.niceDate(P.D.meta.last_day, true)}. Times are in ${P.D.meta.timezone}.`));
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
