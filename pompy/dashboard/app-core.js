/* Pompy Dashboard, part 1: helpers, data loading, routing, and the Overview tab.
 * All text from data goes through textContent (never innerHTML): prompts contain code and HTML. */
(function () {
  "use strict";
  const P = (window.Pompy = { views: {} });

  // ── tiny DOM helpers ─────────────────────────────────────────────────────
  function h(tag, props, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue;
      if (k === "class") e.className = v;
      else if (k === "text") e.textContent = v;
      else if (k === "style") e.style.cssText = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) {
      if (kid == null || kid === false) continue;
      e.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return e;
  }
  const NS = "http://www.w3.org/2000/svg";
  function s(tag, attrs, ...kids) {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) if (v != null) e.setAttribute(k, v);
    for (const kid of kids.flat()) if (kid != null) e.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    return e;
  }
  const round = (x, d) => (x == null || isNaN(x) ? null : Math.round(x * 10 ** d) / 10 ** d);
  const pct = (a, b) => (b ? round((100 * a) / b, 1) : null);
  const mean = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
  function median(xs) {
    if (!xs.length) return null;
    const o = [...xs].sort((a, b) => a - b);
    const m = o.length >> 1;
    return o.length % 2 ? o[m] : (o[m - 1] + o[m]) / 2;
  }
  const fmt = (v, suffix = "") => (v == null ? "none" : `${v}${suffix}`);
  const WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  Object.assign(P, { h, s, round, pct, mean, median, fmt, WD });

  // ── shared pieces ────────────────────────────────────────────────────────
  P.panel = (title, sub, ...body) =>
    h("div", { class: "panel" }, h("div", { class: "panel-head" }, h("h2", { text: title }), sub ? h("span", { class: "muted small", text: sub }) : null), ...body);

  P.bars = (items, { max, suffix = "", fmtNum } = {}) => {
    const top = max ?? Math.max(1, ...items.map((i) => i.value));
    return h("div", { class: "bars" }, items.map((i) =>
      h("div", { class: "bar-row", title: `${i.name}: ${i.value}${suffix}` },
        h("span", { class: "name", text: i.name }),
        h("div", { class: "bar-track" }, h("div", { class: "bar-fill", style: `width:${Math.max(1.5, (100 * i.value) / top)}%` })),
        h("span", { class: "num", text: fmtNum ? fmtNum(i.value) : `${i.value}${suffix}` }))));
  };

  // "claude-opus-5-5" -> "Opus 5.5"; a trailing date stamp is dropped.
  P.modelName = (id) => {
    const m = /^claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?$/.exec(id);
    if (!m) return id.replace("claude-", "");
    return `${m[1][0].toUpperCase()}${m[1].slice(1)} ${m[2]}${m[3] ? "." + m[3] : ""}`;
  };

  P.scorePill = (row) => {
    if (row.s == null) return h("span", { class: "pill none", title: "Not scored: only task prompts get a score", text: "·" });
    return h("span", { class: "pill" + (row.s <= 1 ? " low" : ""), title: "Specificity score", text: `${row.s}/7` });
  };
  P.FRICTION_LABEL = {
    correction: "you corrected", problem: "problem after", interrupt: "interrupted",
    rejected: "tool rejected", rephrase: "re-asked", clarify: "Claude had to ask",
  };

  // Periods use the snapshot's own date (meta.today, in the Mac's time zone), so "this month"
  // here means what it means in `pompy compare`.
  P.periods = () => {
    const today = P.D.meta.today;
    const [y, m] = today.split("-").map(Number);
    const ym = (yy, mm) => `${yy}-${String(mm).padStart(2, "0")}`;
    const thisMonth = ym(y, m);
    const lastMonth = m === 1 ? ym(y - 1, 12) : ym(y, m - 1);
    const daysAgo = (n) => {
      const d = new Date(today + "T12:00:00Z");
      d.setUTCDate(d.getUTCDate() - n);
      return d.toISOString().slice(0, 10);
    };
    const months = [...new Set(P.D.prompts.map((r) => r.d.slice(0, 7)))].sort().reverse();
    const list = [
      { id: "all", label: "All-time", test: () => true },
      { id: "this-month", label: "This month", test: (r) => r.d.startsWith(thisMonth) },
      { id: "before", label: "Before this month", test: (r) => r.d < thisMonth + "-01" },
      { id: "last-month", label: "Last month", test: (r) => r.d.startsWith(lastMonth) },
      { id: "30d", label: "Last 30 days", test: (r) => r.d > daysAgo(30) },
      { id: "7d", label: "Last 7 days", test: (r) => r.d > daysAgo(7) },
      ...months.map((mo) => ({ id: mo, label: mo, test: (r) => r.d.startsWith(mo) })),
    ];
    return list;
  };
  P.periodSelect = (id, value, onchange) => {
    const sel = h("select", { id, class: "sel", onchange: () => onchange(sel.value) });
    for (const p of P.periods()) sel.append(h("option", { value: p.id, text: p.label, selected: p.id === value }));
    return sel;
  };
  P.period = (id) => P.periods().find((p) => p.id === id) || P.periods()[0];

  // The same numbers as `pompy compare`, from the per-prompt rows Python decided.
  P.metrics = (rows) => {
    const typed = rows.filter((r) => r.k === "typed");
    const tasks = typed.filter((r) => r.r === "task");
    const op = tasks.filter((r) => r.o);
    const fu = tasks.filter((r) => !r.o);
    const m = {
      typed: typed.length,
      tasks: tasks.length,
      median_words: round(median(typed.map((r) => r.w)), 1),
      score_mean: round(mean(tasks.map((r) => r.s)), 2),
      openers_mean: round(mean(op.map((r) => r.s)), 2),
      follow_ups_mean: round(mean(fu.map((r) => r.s)), 2),
      friction_rate: pct(tasks.filter((r) => r.fr.length).length, tasks.length),
      clarify_rate: pct(tasks.filter((r) => r.fr.includes("clarify")).length, tasks.length),
    };
    for (const f of P.D.rules.features) m["has_" + f.name] = pct(tasks.filter((r) => r.f.includes(f.name)).length, tasks.length);
    const byHour = new Array(24).fill(0);
    rows.forEach((r) => byHour[r.h]++);
    m.peak_hour = rows.length ? byHour.indexOf(Math.max(...byHour)) : null;
    return m;
  };

  // Capabilities: each may be missing (null). The page works without them.
  P.cap = (name) => (P._caps[name] ??= window.claude ? window.claude.use(name).catch(() => null) : Promise.resolve(null));
  P._caps = {};
  P.userDoc = async (key) => {
    const [db, user] = await Promise.all([P.cap("db"), P.cap("user")]);
    if (!db || !user) return null;
    const id = await user.id();
    return id ? db.collection("data/users/" + id).doc(key) : null;
  };

  // ── boot ─────────────────────────────────────────────────────────────────
  P.start = async function () {
    try {
      const res = await fetch("data.json");
      if (!res.ok) throw new Error(`data.json answered ${res.status}`);
      P.D = await res.json();
    } catch (e) {
      const err = document.getElementById("load-error");
      err.textContent = "Pompy couldn't load its data (" + e.message + "). Run `pompy publish` and publish out/dashboard again.";
      err.classList.remove("hidden");
      document.getElementById("readouts").textContent = "";
      return;
    }
    // Labels read as words: "Done definition", not "Done-definition".
    const plain = (x) => x.replace(/-/g, " ");
    P.D.rules.features.forEach((f) => (f.label = plain(f.label)));
    (P.D.habits || []).forEach((hb) => (hb.label = plain(hb.label)));
    Object.values((P.D.score && P.D.score.features) || {}).forEach((f) => f.label && (f.label = plain(f.label)));
    P.S = window.PompyScorer.make(P.D.rules);
    P.FEAT = Object.fromEntries(P.D.rules.features.map((f) => [f.name, f]));
    P.byId = new Map(P.D.prompts.map((r) => [r.u, r]));
    renderReadouts();
    window.addEventListener("hashchange", route);
    route();
  };

  const built = {};
  function route() {
    const names = ["overview", "habits", "learned", "prompts", "ask", "memory", "compare"];
    const name = names.includes(location.hash.slice(1)) ? location.hash.slice(1) : "overview";
    for (const n of names) {
      document.getElementById("view-" + n).classList.toggle("on", n === name);
      const a = document.querySelector(`#tabs a[href="#${n}"]`);
      if (n === name) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    }
    if (!built[name] && P.views[name]) {
      built[name] = true;
      P.views[name](document.getElementById("view-" + name));
    }
  }

  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const dayParts = (iso) => iso.split("-").map(Number);
  // "2026-04-27" -> "27 Apr" (with the year when asked)
  P.niceDate = (iso, withYear) => {
    const [y, m, d] = dayParts(iso);
    return `${d} ${MON[m - 1]}${withYear ? " " + y : ""}`;
  };
  P.MON = MON;
  // "27 Apr, 14:05" for a prompt row
  P.when = (r) => `${P.niceDate(r.d)}, ${r.hh}`;

  function renderReadouts() {
    const m = P.D.meta;
    const box = document.getElementById("readouts");
    box.textContent = "";
    const sameYear = m.first_day && m.first_day.slice(0, 4) === m.last_day.slice(0, 4);
    const span = m.first_day
      ? `${P.niceDate(m.first_day, !sameYear)} to ${P.niceDate(m.last_day, true)}`
      : "nothing yet";
    box.append(
      h("span", {}, h("b", { text: `${m.prompts} prompts` }), " remembered"),
      h("span", { title: `Rubric version ${m.rubric_version}, times in ${m.timezone}`, text: span }));

  }

  // ── Overview ─────────────────────────────────────────────────────────────
  P.views.overview = function (root) {
    const D = P.D, st = D.stats, sc = D.score;
    const share = st.models.share;
    const topModel = Object.keys(share).sort((a, b) => share[b] - share[a])[0];
    const typedCount = st.totals.prompts_by_kind.typed || 0;
    root.append(
      h("p", { class: "lede", text: "Everything Pompy remembers about how you prompt Claude, all-time. Specificity counts how many of 7 parts a task prompt has (goal, context, limits, done definition, format, example, exact reference); friction means something went wrong right after a prompt." }),
      h("div", { class: "tiles", id: "tiles" },
        tile("Typed prompts", typedCount, `${st.totals.sessions} chats · ${st.totals.projects} projects`),
        specTile(sc),
        tile("Friction after tasks", `${sc.friction.overall.rate}%`, `${sc.friction.overall.friction} of ${sc.friction.overall.n}`),
        tile("Top model", topModel ? P.modelName(topModel) : "none", topModel ? `${share[topModel]}% of replies` : ""),
        tile("Peak time", `${st.time.peak_weekday} ${String(st.time.peak_hour).padStart(2, "0")}:00`, `${st.time.active_days} active days`)),
      dayTimeline(),
      monthly(),
      themesPanel(),
      h("div", { class: "grid2" },
        P.panel("Specificity of your task prompts", "how many of the 7 rubric parts each prompt had",
          P.bars(sc.score.distribution.map((n, i) => ({ name: `${i} of 7 parts`, value: n }))),
          h("h3", { text: "The 7 parts" }),
          h("ol", { class: "parts" }, D.rules.features.map((f) =>
            h("li", {}, h("b", { text: f.label }), h("span", { class: "muted", text: ` ${f.tip}` }))))),
        P.panel("What your task prompts include", "share of task prompts with each part",
          P.bars(Object.values(sc.features).map((f) => ({ name: f.label, value: f.present_pct })).sort((a, b) => b.value - a.value), { max: 100, suffix: "%" }))),
      h("div", { class: "grid2" }, keywordsPanel(), stylePanel()),
      h("div", { class: "grid2" },
        P.panel("Models that answered", "share of Claude's replies",
          P.bars(Object.entries(share).map(([k, v]) => ({ name: P.modelName(k), value: v })), { max: 100, suffix: "%" })),
        P.panel("Projects", "prompts per project",
          P.bars(st.projects.slice(0, 10).map((p) => ({ name: p.name || p.key, value: p.prompts }))))));
  };

  // Specificity: how many of the 7 prompt parts a task prompt has. Says which parts, and which
  // two you leave out most, so the number means something.
  function specTile(sc) {
    const feats = P.D.rules.features;
    const pctOf = (f) => (sc.features[f.name] || {}).present_pct ?? 0;
    const rarest = [...feats].sort((a, b) => pctOf(a) - pctOf(b)).slice(0, 2);
    const t = tile("Specificity", `${sc.score.mean} of 7`, `parts per task prompt, on average (${sc.scored_prompts} prompts)`);
    t.title = "Seven parts: " + feats.map((f) => f.label.toLowerCase()).join(", ");
    t.append(h("div", { class: "sub" }, "Most often missing: ",
      ...rarest.flatMap((f, i) => [i ? " and " : "", h("a", { href: "#habits", text: `${f.label.toLowerCase()} (in ${Math.round(pctOf(f))}%)` })])));
    return t;
  }

  function tile(label, value, sub) {
    return h("div", { class: "tile" }, h("div", { class: "label", text: label }), h("div", { class: "value", text: value }), h("div", { class: "sub", text: sub }));
  }

  // One row per day, one dot per prompt at the time you sent it.
  function dayTimeline() {
    const readout = h("div", { class: "readout", text: "Point at a dot to see the prompt." });
    const chartBox = h("div", {});
    const note = h("p", { class: "muted small" });
    let period = "30d";
    const sel = P.periodSelect("tl-period", period, (v) => { period = v; draw(); });
    const legend = h("div", { class: "chips small" },
      h("span", { class: "legend" }, h("i", { class: "dot", style: "background:var(--accent)" }), "a prompt"),
      h("span", { class: "legend" }, h("i", { class: "dot", style: "background:var(--note)" }), "something went wrong after it"));

    function draw() {
      const rows = P.D.prompts.filter(P.period(period).test);
      const byDay = new Map();
      rows.forEach((r) => { if (!byDay.has(r.d)) byDay.set(r.d, []); byDay.get(r.d).push(r); });
      let days = [...byDay.keys()].sort();
      let skipped = false;
      if (days.length) {
        // Short windows show every calendar day so quiet days are visible; long ones only active days.
        const all = [];
        const d = new Date(days[0] + "T12:00:00Z"), last = new Date(days[days.length - 1] + "T12:00:00Z");
        for (; d <= last; d.setUTCDate(d.getUTCDate() + 1)) all.push(d.toISOString().slice(0, 10));
        if (all.length <= 62) days = all; else skipped = true;
      }
      days.reverse(); // newest on top
      // Fewer days, bigger rows: text and dots grow as the window shrinks.
      const rowH = Math.max(16, Math.min(56, Math.round(480 / Math.max(1, days.length))));
      const fs = Math.max(10, Math.min(20, Math.round(rowH * 0.42)));
      const dotR = Math.max(4, Math.min(10, rowH * 0.2));
      const W = 900, padL = Math.round(fs * 9), padR = Math.round(fs * 3.4), padT = Math.round(fs * 2.4);
      const H = padT + Math.max(1, days.length) * rowH + 8;
      const x = (hhmm) => { const [hr, mi] = hhmm.split(":").map(Number); return padL + ((W - padL - padR) * (hr * 60 + mi)) / 1440; };
      const svg = s("svg", { class: "chart", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Your prompts by day and time of day" });
      [[0, "midnight"], [6, "6 am"], [12, "noon"], [18, "6 pm"], [24, "midnight"]].forEach(([hr, label]) => {
        const xx = padL + ((W - padL - padR) * hr) / 24;
        svg.append(s("line", { x1: xx, x2: xx, y1: padT - 4, y2: H - 6, stroke: "var(--line)" }));
        svg.append(s("text", { x: xx, y: padT - fs * 0.9, "text-anchor": "middle", style: `font-size:${fs}px` }, label));
      });
      days.forEach((day, i) => {
        const y = padT + i * rowH + rowH / 2;
        const list = byDay.get(day) || [];
        const wd = P.WD[(new Date(day + "T12:00:00Z").getUTCDay() + 6) % 7];
        svg.append(s("line", { x1: padL, x2: W - padR, y1: y, y2: y, stroke: "var(--line)", "stroke-dasharray": "1 3" }));
        svg.append(s("text", { x: padL - 8, y: y + fs * 0.35, "text-anchor": "end", class: list.length ? "" : "faint", style: `font-size:${fs}px` }, `${wd} ${P.niceDate(day)}`));
        if (list.length) svg.append(s("text", { x: W - padR + 6, y: y + fs * 0.35, style: `font-size:${fs}px` }, list.length));
        list.forEach((r) => {
          const bad = r.fr && r.fr.length;
          const words = (r.t || "").replace(/\s+/g, " ").trim();
          const say = `${wd} ${P.niceDate(day)}, ${r.hh} · ${r.p}${bad ? " · something went wrong after" : ""} · ${words.length > 90 ? words.slice(0, 89) + "…" : words}`;
          const dot = s("circle", { cx: x(r.hh), cy: y, r: dotR, fill: bad ? "var(--note)" : "var(--accent)", "fill-opacity": 0.75, tabindex: 0 }, s("title", {}, say));
          dot.addEventListener("mouseenter", () => (readout.textContent = say));
          dot.addEventListener("focus", () => (readout.textContent = say));
          svg.append(dot);
        });
      });
      chartBox.textContent = "";
      chartBox.append(days.length ? svg : h("p", { class: "muted", text: "No prompts in this window." }));
      note.textContent = skipped ? "Long window: only days you used Claude are shown." : "";
    }
    draw();
    return P.panel("Your days with Claude", `each dot is a prompt, at the time you sent it (${P.D.meta.timezone})`,
      h("div", { class: "controls" }, sel, legend), chartBox, readout, note);
  }

  // Per month: how many prompts, and how many of your task prompts were specific, okay or vague.
  function monthly() {
    const bands = [
      { key: "specific", label: "specific (4+ parts)", color: "var(--good)", test: (r) => r.r === "task" && r.s >= 4 },
      { key: "okay", label: "okay (2 or 3 parts)", color: "var(--primary)", test: (r) => r.r === "task" && r.s >= 2 && r.s < 4 },
      { key: "vague", label: "vague (0 or 1 part)", color: "var(--purple)", test: (r) => r.r === "task" && r.s < 2 },
      { key: "other", label: "replies, follow-ups, commands", color: "var(--line)", test: (r) => r.r !== "task" },
    ];
    const byMonth = {};
    for (const r of P.D.prompts) {
      const mo = r.d.slice(0, 7);
      byMonth[mo] ??= { specific: 0, okay: 0, vague: 0, other: 0, total: 0 };
      const b = bands.find((bd) => bd.test(r));
      byMonth[mo][b.key]++;
      byMonth[mo].total++;
    }
    const months = Object.keys(byMonth).sort();
    const multiYear = months.length && months[0].slice(0, 4) !== months[months.length - 1].slice(0, 4);
    const moName = (mo) => P.MON[+mo.slice(5) - 1] + (multiYear ? " " + mo.slice(2, 4) : "");
    const W = 900, H = 230, padL = 12, padB = 24, padT = 22;
    const svg = s("svg", { class: "chart", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Prompts per month, split by how specific they were" });
    const maxT = Math.max(1, ...months.map((mo) => byMonth[mo].total));
    const bw = (W - padL) / Math.max(1, months.length);
    const hOf = (n) => ((H - padB - padT) * n) / maxT;
    const readout = h("div", { class: "readout", text: "Point at a month to see the split." });
    months.forEach((mo, i) => {
      const c = byMonth[mo];
      const tasks = c.specific + c.okay + c.vague;
      const x0 = padL + i * bw + bw * 0.2, w = bw * 0.6;
      const g = s("g", { tabindex: 0, class: "month" });
      let y = H - padB;
      for (const b of bands) {
        if (!c[b.key]) continue;
        const hh = hOf(c[b.key]);
        y -= hh;
        g.append(s("rect", { x: x0, y, width: w, height: hh, fill: b.color }));
      }
      const share = tasks ? Math.round((100 * c.specific) / tasks) : null;
      const say = `${moName(mo)}: ${c.total} prompts. Of your ${tasks} task prompts, ${c.specific} specific, ${c.okay} okay, ${c.vague} vague.`;
      g.append(s("title", {}, say));
      g.addEventListener("mouseenter", () => (readout.textContent = say));
      g.addEventListener("focus", () => (readout.textContent = say));
      svg.append(g, s("text", { x: x0 + w / 2, y: H - 7, "text-anchor": "middle" }, moName(mo)));
      if (share != null) svg.append(s("text", { x: x0 + w / 2, y: y - 6, "text-anchor": "middle", class: "strong" }, `${share}% specific`));
    });
    const legend = h("div", { class: "chips small" }, bands.map((b) =>
      h("span", { class: "legend" }, h("i", { class: "dot sq", style: `background:${b.color}` }), b.label)));
    const parts = P.D.rules.features.map((f) => f.label.toLowerCase()).join(", ");
    const gap = missingMonths(months);
    return P.panel("Your prompts over time", "the taller the green, the clearer you were",
      legend, svg, readout,
      h("p", { class: "muted small", text: `A task prompt counts as specific when it has at least 4 of these 7 parts: ${parts}.` }),
      gap.length ? h("p", { class: "muted small", text: `No data for ${gap.map(moName).join(", ")}: those transcripts were deleted by Claude Code's 30-day cleanup before Pompy existed.` }) : null);
  }
  function missingMonths(months) {
    if (months.length < 2) return [];
    const out = [];
    let [y, m] = months[0].split("-").map(Number);
    const last = months[months.length - 1];
    for (;;) {
      m++;
      if (m > 12) { m = 1; y++; }
      const k = `${y}-${String(m).padStart(2, "0")}`;
      if (k >= last) break;
      if (!months.includes(k)) out.push(k);
    }
    return out;
  }

  // What you keep asking about, and how each theme tends to go (from `pompy themes`).
  function themesPanel() {
    const th = P.D.themes;
    if (!th || !th.length) return null;
    const maxSize = Math.max(...th.map((t) => t.size));
    const worst = [...th].filter((t) => t.size >= 10).sort((a, b) => b.went_wrong_pct - a.went_wrong_pct)[0];
    const rows = th.map((t) => {
      const name = t.label.length ? t.label.join(" · ") : "short replies and one-liners";
      const tone = t.went_wrong_pct >= 20 ? "bad" : t.went_wrong_pct >= 12 ? "mid" : "good";
      return h("div", { class: "theme-row", title: t.example ? `e.g. "${t.example}"` : "" },
        h("div", { class: "theme-name" }, h("b", { text: name }), h("span", { class: "muted small", text: ` ${t.size} prompts` })),
        h("div", { class: "bar-track" }, h("div", { class: "bar-fill", style: `width:${Math.max(2, (100 * t.size) / maxSize)}%` })),
        h("span", { class: "chip theme-" + tone, text: `${Math.round(t.went_wrong_pct)}% went wrong` }),
        h("span", { class: "muted small mono", text: `${t.mean_score}/7` }));
    });
    return P.panel("What you ask Claude about", "your task prompts, grouped by meaning",
      worst ? h("p", { class: "note", text: `Most trouble: "${worst.label.join(" · ")}". ${Math.round(worst.went_wrong_pct)}% of those prompts went wrong. Be most specific there.` }) : null,
      h("div", { class: "themes" }, rows),
      h("p", { class: "small muted", text: "Hover a theme for an example. Themes overlap: your prompts form a continuum, so treat these as a map, not hard categories." }));
  }

  function keywordsPanel() {
    const kw = P.D.keywords;
    const terms = (kw.top_terms || []).slice(0, 15);
    return P.panel("Your most used words", "how many of your prompts use each word",
      P.bars(terms.map((t) => ({ name: t.term, value: t.prompts, title: `${t.term}: in ${t.prompts} prompts, ${t.sessions} chats` }))),
      h("h3", { text: "Phrases you repeat" }),
      h("div", { class: "chips" }, (kw.top_phrases || []).slice(0, 14).map((p) => h("span", { class: "chip" }, p.phrase, h("span", { class: "muted mono", text: String(p.prompts) })))));
  }
  function stylePanel() {
    const kw = P.D.keywords;
    const style = kw.opening_style || {};
    const verbs = Object.entries((kw.action_verbs || {}).opening || {}).slice(0, 10);
    return P.panel("How you open a prompt", "first words of each typed prompt",
      P.bars(Object.entries(style).map(([k, v]) => ({ name: k, value: v }))),
      verbs.length ? h("h3", { text: "Verbs you lead with" }) : null,
      verbs.length ? h("div", { class: "chips" }, verbs.map(([v, n]) => h("span", { class: "chip" }, v, h("span", { class: "muted mono", text: String(n) })))) : null);
  }
})();
