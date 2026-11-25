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

  // Five places, each with one job. Older links (#learned, #compare) still land somewhere sensible.
  // A tab can hold more than one part; later parts sit under their own heading, or folded away.
  const TABS = {
    overview: [["overview"]],
    ask: [["ask"]],
    prompts: [["prompts"]],
    habits: [["habits"], ["compare", "Are you improving?", "fold"]],
    memory: [["memory"], ["learned", "Long-term memory: what Pompy believes"]],
  };
  const ALIAS = { learned: "memory", compare: "habits" };
  const built = {};
  function route() {
    const want = location.hash.slice(1);
    const name = TABS[want] ? want : ALIAS[want] || "overview";
    for (const n of Object.keys(TABS)) {
      document.getElementById("view-" + n).classList.toggle("on", n === name);
      const a = document.querySelector(`#tabs a[href="#${n}"]`);
      if (n === name) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    }
    if (built[name]) return;
    built[name] = true;
    const root = document.getElementById("view-" + name);
    for (const [part, title, mode] of TABS[name]) {
      if (!P.views[part]) continue;
      if (!title) { P.views[part](root); continue; }
      const box = h("div", { class: "subview" });
      P.views[part](box);
      if (mode === "fold") root.append(h("details", { class: "panel fold" }, h("summary", { class: "panel-head" }, h("h2", { text: title })), box));
      else root.append(h("h2", { class: "section-title", text: title }), box);
    }
  }

  // Turn a panel into one that opens on click: for detail most visits don't need.
  P.fold = (panel, open) => {
    if (!panel) return null;
    const head = panel.querySelector(".panel-head");
    const rest = [...panel.childNodes].filter((n) => n !== head);
    return h("details", { class: "panel fold", open }, h("summary", { class: "panel-head" }, [...head.childNodes]), rest);
  };

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

  // ── Shared by the Overview (app-overview.js) ─────────────────────────────
  P.tile = function (label, value, sub) {
    return h("div", { class: "tile" }, h("div", { class: "label", text: label }), h("div", { class: "value", text: value }), h("div", { class: "sub", text: sub }));
  };

  // One row per day, one dot per prompt at the time you sent it.
  P.dayTimeline = function () {
    const readout = h("div", { class: "readout", text: "Point at a dot to see the prompt." });
    const chartBox = h("div", { class: "chart-scroll" });
    const note = h("p", { class: "muted small" });
    let period = "30d";
    const sel = P.periodSelect("tl-period", period, (v) => { period = v; draw(); });
    const legend = h("div", { class: "chips small" },
      h("span", { class: "legend" }, h("i", { class: "dot", style: "background:var(--accent)" }), "a prompt"),
      h("span", { class: "legend" }, h("i", { class: "dot", style: "background:var(--note)" }), "reply was unclear or missed"));

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
          const bad = r.v === "unclear" || r.v === "missed";
          const words = (r.t || "").replace(/\s+/g, " ").trim();
          const say = `${wd} ${P.niceDate(day)}, ${r.hh} · ${r.p}${bad ? " · reply unclear or missed" : ""} · ${words.length > 90 ? words.slice(0, 89) + "…" : words}`;
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
  };
})();
