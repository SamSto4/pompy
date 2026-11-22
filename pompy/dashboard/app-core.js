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
  const fmt = (v, suffix = "") => (v == null ? "–" : `${v}${suffix}`);
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

  P.scorePill = (row) => {
    if (row.s == null) return h("span", { class: "pill none", title: "Not scored: only task prompts get a score", text: "–" });
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
    P.S = window.PompyScorer.make(P.D.rules);
    P.FEAT = Object.fromEntries(P.D.rules.features.map((f) => [f.name, f]));
    P.byId = new Map(P.D.prompts.map((r) => [r.u, r]));
    renderReadouts();
    window.addEventListener("hashchange", route);
    route();
  };

  const built = {};
  function route() {
    const names = ["overview", "habits", "prompts", "ask", "memory", "compare"];
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

  function renderReadouts() {
    const m = P.D.meta;
    const box = document.getElementById("readouts");
    box.textContent = "";
    const item = (label, value) => h("span", {}, label + " ", h("b", { class: "mono", text: value }));
    box.append(
      item("memory", `${m.prompts} prompts`),
      item("from", `${m.first_day} → ${m.last_day}`),
      item("snapshot", m.generated_at.slice(0, 16).replace("T", " ") + " UTC"),
      item("time zone", m.timezone),
      item("rubric", "v" + m.rubric_version));
  }

  // ── Overview ─────────────────────────────────────────────────────────────
  P.views.overview = function (root) {
    const D = P.D, st = D.stats, sc = D.score;
    const share = st.models.share;
    const topModel = Object.keys(share).sort((a, b) => share[b] - share[a])[0];
    const typedCount = st.totals.prompts_by_kind.typed || 0;
    root.append(
      h("p", { class: "lede", text: "Everything Pompy remembers about how you prompt Claude, all-time. Specificity is scored 0–7 on task prompts only; friction means something went wrong right after a prompt." }),
      h("div", { class: "tiles", id: "tiles" },
        tile("Typed prompts", typedCount, `${st.totals.sessions} chats · ${st.totals.projects} projects`),
        tile("Avg specificity", `${sc.score.mean}/7`, `median ${sc.score.median} · ${sc.scored_prompts} task prompts`),
        tile("Friction after tasks", `${sc.friction.overall.rate}%`, `${sc.friction.overall.friction} of ${sc.friction.overall.n}`),
        tile("Top model", topModel ? topModel.replace("claude-", "") : "–", topModel ? `${share[topModel]}% of replies` : ""),
        tile("Peak time", `${st.time.peak_weekday} ${String(st.time.peak_hour).padStart(2, "0")}:00`, `${st.time.active_days} active days`)),
      h("div", { class: "grid2" }, heatmap(), monthly()),
      h("div", { class: "grid2" },
        P.panel("Specificity of your task prompts", "how many of the 7 rubric parts each prompt had",
          P.bars(sc.score.distribution.map((n, i) => ({ name: `${i} of 7`, value: n })))),
        P.panel("What your task prompts include", "share of task prompts with each part",
          P.bars(Object.values(sc.features).map((f) => ({ name: f.label, value: f.present_pct })).sort((a, b) => b.value - a.value), { max: 100, suffix: "%" }))),
      h("div", { class: "grid2" }, keywordsPanel(), stylePanel()),
      h("div", { class: "grid2" },
        P.panel("Models that answered", "share of Claude's replies",
          P.bars(Object.entries(share).map(([k, v]) => ({ name: k.replace("claude-", ""), value: v })), { max: 100, suffix: "%" })),
        P.panel("Projects", "prompts per project",
          P.bars(st.projects.slice(0, 10).map((p) => ({ name: p.name || p.key, value: p.prompts }))))));
  };

  function tile(label, value, sub) {
    return h("div", { class: "tile" }, h("div", { class: "label", text: label }), h("div", { class: "value", text: value }), h("div", { class: "sub", text: sub }));
  }

  function heatmap() {
    const grid = P.D.stats.time.weekday_hour_grid;
    const max = Math.max(1, ...grid.flat());
    const readout = h("div", { class: "readout", text: "Point at a square to see the count." });
    const g = h("div", { class: "heat", role: "img", "aria-label": "Prompts by weekday and hour" });
    grid.forEach((row, wd) => {
      g.append(h("span", { class: "rl", text: P.WD[wd] }));
      row.forEach((n, hr) => {
        const label = `${P.WD[wd]} ${String(hr).padStart(2, "0")}:00 · ${n} prompt${n === 1 ? "" : "s"}`;
        const cell = h("span", { class: "cell", tabindex: n ? 0 : null, title: label,
          style: n ? `background: color-mix(in srgb, var(--accent) ${Math.round(18 + (82 * n) / max)}%, var(--accent-soft))` : "" });
        cell.addEventListener("mouseenter", () => (readout.textContent = label));
        cell.addEventListener("focus", () => (readout.textContent = label));
        g.append(cell);
      });
    });
    g.append(h("span", {}));
    for (let hr = 0; hr < 24; hr++) g.append(h("span", { class: "cl", text: hr % 6 === 0 ? String(hr) : "" }));
    return P.panel("When you use Claude", `local time (${P.D.meta.timezone})`, g, readout);
  }

  function monthly() {
    const counts = P.D.stats.time.by_month;
    const scores = P.D.score.score.by_month;
    const months = Object.keys(counts).sort();
    const W = 460, H = 190, padL = 30, padB = 22, padT = 12;
    const svg = s("svg", { class: "chart", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Prompts and specificity by month" });
    const maxC = Math.max(1, ...Object.values(counts));
    const bw = (W - padL) / Math.max(1, months.length);
    const y = (v) => H - padB - ((H - padB - padT) * v) / maxC;
    const ys = (v) => H - padB - ((H - padB - padT) * v) / 7;
    for (const t of [0, 0.5, 1]) {
      svg.append(s("line", { x1: padL, x2: W, y1: y(maxC * t), y2: y(maxC * t), stroke: "var(--line)" }));
      svg.append(s("text", { x: padL - 4, y: y(maxC * t) + 3, "text-anchor": "end" }, Math.round(maxC * t)));
    }
    const readout = h("div", { class: "readout", text: "Bars: prompts per month. Line: average specificity (0–7)." });
    const pts = [];
    months.forEach((mo, i) => {
      const x = padL + i * bw;
      const bar = s("rect", { x: x + bw * 0.18, y: y(counts[mo]), width: bw * 0.64, height: H - padB - y(counts[mo]), rx: 3, fill: "var(--accent-soft)", stroke: "var(--accent)", "stroke-opacity": 0.35, tabindex: 0 });
      const say = () => (readout.textContent = `${mo} · ${counts[mo]} prompts · specificity ${scores[mo] ?? "–"}/7`);
      bar.addEventListener("mouseenter", say);
      bar.addEventListener("focus", say);
      svg.append(bar, s("text", { x: x + bw / 2, y: H - 6, "text-anchor": "middle" }, mo.slice(2)));
      if (scores[mo] != null) pts.push([x + bw / 2, ys(scores[mo]), scores[mo]]);
    });
    if (pts.length) {
      svg.append(s("polyline", { points: pts.map((p) => p.slice(0, 2).join(",")).join(" "), fill: "none", stroke: "var(--note)", "stroke-width": 2 }));
      pts.forEach(([x, yy, v], i) => {
        svg.append(s("circle", { cx: x, cy: yy, r: i === pts.length - 1 ? 4.5 : 3, fill: "var(--note)" }));
        svg.append(s("text", { x, y: yy - 8, "text-anchor": "middle" }, v));
      });
    }
    const gap = missingMonths(months);
    return P.panel("Your prompts over time", null, svg, readout,
      gap.length ? h("p", { class: "muted small", text: `No data for ${gap.join(", ")}: those transcripts were deleted by Claude Code's 30-day cleanup before Pompy existed.` }) : null);
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

  function keywordsPanel() {
    const kw = P.D.keywords;
    const terms = (kw.top_terms || []).slice(0, 24);
    const maxP = Math.max(1, ...terms.map((t) => t.prompts));
    return P.panel("Your words", "topics ranked by how many prompts use them",
      h("div", { class: "chips" }, terms.map((t) =>
        h("span", { class: "chip", title: `${t.term}: in ${t.prompts} prompts, ${t.sessions} chats`, style: `font-size:${0.78 + (0.3 * t.prompts) / maxP}rem` }, t.term, h("span", { class: "muted mono", text: String(t.prompts) })))),
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
