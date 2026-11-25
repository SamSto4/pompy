/* Pompy Dashboard, part 4: long-term memory. "Learned" lists what Pompy believes about how you
 * work, how sure it is, and the evidence on both sides. Memory Lab borrows `P.beliefChart` to show
 * one belief's confidence over time, and how it fades if nothing confirms it again. */
(function () {
  "use strict";
  const P = window.Pompy;
  const { h, s } = P;
  const DAY = 86400000;
  const FUTURE_DAYS = 270;

  const L = () => P.D.learned || { patterns: [], retired: 0, half_life_days: 90, retire_below: 0.15 };
  const sureWord = (c) => (c >= 0.6 ? "Sure" : c >= 0.35 ? "Fairly sure" : c >= 0.15 ? "Unsure" : "Not yet");
  const project = (p) => p.scope.replace(/^project: /, "");

  function meter(c) {
    return h("div", { class: "belief-meter", title: `Confidence today: ${Math.round(100 * c)}%` },
      h("div", { class: "meter" }, h("div", { style: `width:${Math.max(2, 100 * c)}%` })),
      h("span", { class: "small muted", text: `${sureWord(c)} · ${Math.round(100 * c)}%` }));
  }

  function evidenceList(items, more, label, cls) {
    if (!items.length) return h("p", { class: "small muted", text: `${label}: none yet.` });
    return h("div", { class: "ev " + cls },
      h("h3", { text: `${label} (${items.length + more})` }),
      items.map((e) => h("div", { class: "ev-row" },
        h("div", { class: "quote", text: e.t }),
        h("div", { class: "small muted", text: `${P.niceDate(e.d, true)} · then: ${e.o || "nothing noted"}` }))),
      more ? h("p", { class: "small muted", text: `and ${more} more` }) : null);
  }

  function why(p) {
    if (p.tested) {
      const [a, b] = p.rates.map((x) => Math.round(100 * x));
      return `Went wrong after ${a}% of these (${p.n[0]} prompts) vs ${b}% of the rest (${p.n[1]}).`;
    }
    if (p.source === "facts") return `Summarised from ${p.n_for} of your chats in this project.`;
    return `Proposed by Pompy's agent from ${p.n_for} examples, with ${p.n_against} against. Not yet tested by statistics.`;
  }

  function beliefCard(p) {
    const open = h("details", { class: "belief-ev" },
      h("summary", { class: "small", text: "See the evidence" }),
      h("div", { class: "grid2" },
        evidenceList(p.for, p.n_for - p.for.length, "For", "for"),
        p.source === "facts" ? null : evidenceList(p.against, p.n_against - p.against.length, "Against", "against")));
    return h("div", { class: "belief" },
      h("p", { class: "belief-s", text: p.s }),
      meter(p.c),
      h("p", { class: "small muted", text: why(p) }),
      open);
  }

  P.views.learned = function (root) {
    const all = L().patterns;
    const goals = all.filter((p) => p.kind === "goal");
    const prefs = all.filter((p) => p.kind === "preference");
    const habits = all.filter((p) => p.kind !== "goal" && p.kind !== "preference");
    const holds = habits.filter((p) => p.status === "supported");
    const testing = habits.filter((p) => p.status === "hypothesis");
    const wrong = habits.filter((p) => p.status === "contradicted");

    root.append(h("p", { class: "lede", text: `What Pompy believes about how you work, how sure it is, and the evidence on both sides. A belief fades by half every ${L().half_life_days} days unless new chats back it up.` }));

    if (!all.length) {
      root.append(h("div", { class: "panel" }, h("p", { class: "muted", text: "Nothing learned yet. Run pompy consolidate with facts, then publish again." })));
      return;
    }

    // Your projects: one goal each, with any preferences you showed there.
    if (goals.length) {
      root.append(P.panel("Your projects", `${goals.length} goals`,
        h("div", { class: "beliefs" }, goals.map((g) => {
          const mine = prefs.filter((x) => x.scope === g.scope);
          return h("div", { class: "belief" },
            h("span", { class: "tag", text: project(g) }),
            h("p", { class: "belief-s", text: g.s }),
            mine.length ? h("ul", { class: "small" }, mine.map((x) => h("li", { text: x.s }))) : null,
            meter(g.c),
            h("p", { class: "small muted", text: g.last_confirmed ? `Last worked on ${P.niceDate(g.last_confirmed, true)}.` : "" }),
            h("details", { class: "belief-ev" }, h("summary", { class: "small", text: "See the chats" }), evidenceList(g.for, g.n_for - g.for.length, "From", "for")));
        }))));
    }

    // About your prompting: what holds, what turned out false, what is still being tested.
    const body = [];
    body.push(holds.length
      ? h("div", { class: "beliefs" }, holds.map(beliefCard))
      : h("p", { class: "muted", text: "Nothing about your prompting holds up yet: no claim has passed the statistics. That is an honest answer, not a bug; more chats will tell." }));
    if (wrong.length) body.push(h("h3", { text: "Turned out false" }), h("div", { class: "beliefs" }, wrong.map(beliefCard)));
    if (testing.length) {
      body.push(h("details", { class: "testing" },
        h("summary", { text: `Still testing (${testing.length})` }),
        h("p", { class: "small muted", text: "Ideas with too little evidence either way. Each is checked again each time Pompy consolidates its memory." }),
        h("div", { class: "beliefs" }, testing.map(beliefCard))));
    }
    root.append(P.panel("About your prompting", `${holds.length} hold up`, ...body));

    if (L().retired) root.append(h("p", { class: "small muted", text: `Let go of ${L().retired} belief${L().retired === 1 ? "" : "s"} that faded below ${Math.round(100 * L().retire_below)}% or were never confirmed.` }));
  };

  // ── Confidence over time, for Memory Lab ──────────────────────────────────
  // Solid: confidence at each check (last value per day). Dashed: today onwards, if nothing
  // confirms it again (halves every half-life). Grey line: the floor where Pompy lets go.
  P.beliefChart = function (p) {
    const { half_life_days: half, retire_below: floor } = L();
    const byDay = new Map();
    for (const [d, c] of p.history) byDay.set(d, c);
    const pts = [...byDay].map(([d, c]) => [Date.parse(d + "T12:00:00Z"), c]);
    const today = Date.parse(P.D.meta.today + "T12:00:00Z");
    if (p.status !== "retired") pts.push([today, p.c]);
    // Fading so far: from when it was last confirmed (for a project, your last chat) to today.
    const past = [];
    if (p.last_confirmed && p.status === "supported") {
      const t0 = Date.parse(p.last_confirmed + "T12:00:00Z");
      const ago = Math.max(0, (today - t0) / DAY);
      const c0 = p.c / 0.5 ** (ago / half);
      for (let d = 0; d < ago; d += 5) past.push([t0 + d * DAY, c0 * 0.5 ** (d / half)]);
      past.push([today, p.c]);
    }
    const future = [];
    for (let d = 0; d <= FUTURE_DAYS; d += 10) future.push([today + d * DAY, p.c * 0.5 ** (d / half)]);
    const forgetDay = p.c > floor ? Math.ceil(half * Math.log2(p.c / floor)) : 0;

    const W = 640, H = 190, L0 = 34, R0 = 12, T0 = 10, B0 = 26;
    const x0 = Math.min(...pts.map((q) => q[0]), ...past.map((q) => q[0])), x1 = today + FUTURE_DAYS * DAY;
    const x = (t) => L0 + ((W - L0 - R0) * (t - x0)) / Math.max(DAY, x1 - x0);
    const y = (c) => T0 + (H - T0 - B0) * (1 - c);
    const path = (q) => q.map(([t, c], i) => `${i ? "L" : "M"}${x(t).toFixed(1)},${y(c).toFixed(1)}`).join("");
    const label = (t) => { const d = new Date(t); return `${P.MON[d.getUTCMonth()]} ${d.getUTCFullYear()}`; };

    const svg = s("svg", { class: "chart", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `Confidence over time for: ${p.s}` },
      [0, 0.5, 1].map((c) => s("text", { x: L0 - 6, y: y(c) + 3, "text-anchor": "end" }, `${100 * c}%`)),
      s("line", { x1: L0, x2: W - R0, y1: y(floor), y2: y(floor), stroke: "var(--muted)", "stroke-dasharray": "2 4", "stroke-opacity": 0.6 }),
      s("text", { x: W - R0, y: y(floor) - 4, "text-anchor": "end", class: "faint" }, "let go below this"),
      s("line", { x1: x(today), x2: x(today), y1: T0, y2: H - B0, stroke: "var(--line)" }),
      s("text", { x: x(today), y: H - 8, "text-anchor": "middle", class: "strong" }, "today"),
      x(today) - L0 > 60 ? s("text", { x: L0, y: H - 8 }, label(x0)) : null,
      past.length ? s("path", { d: path(past), fill: "none", stroke: "var(--accent)", "stroke-width": 2.5 }) : null,
      past.length ? s("circle", { cx: x(past[0][0]), cy: y(past[0][1]), r: 4, fill: "var(--good)" },
        s("title", {}, `Last confirmed ${p.last_confirmed}: ${Math.round(100 * past[0][1])}%`)) : null,
      s("text", { x: W - R0, y: H - 8, "text-anchor": "end" }, label(x1)),
      s("path", { d: path(future), fill: "none", stroke: "var(--purple)", "stroke-width": 2, "stroke-dasharray": "5 5" }),
      s("path", { d: path(pts), fill: "none", stroke: "var(--accent)", "stroke-width": 2.5 }),
      pts.map(([t, c]) => s("circle", { cx: x(t), cy: y(c), r: 4, fill: "var(--accent)", tabindex: 0 },
        s("title", {}, `${new Date(t).toISOString().slice(0, 10)}: ${Math.round(100 * c)}%`))));

    const say = p.status === "retired" ? "Pompy has let this go."
      : p.c <= floor ? "Too weak to keep yet: it needs more evidence."
      : `If nothing backs it up again, Pompy lets it go in about ${forgetDay} days.`;
    return h("div", {}, svg,
      h("div", { class: "controls small" },
        past.length ? h("span", { class: "legend" }, h("i", { class: "dot", style: "background:var(--good)" }), `last confirmed, ${P.niceDate(p.last_confirmed, true)}`) : null,
        h("span", { class: "legend" }, h("i", { class: "dot", style: "background:var(--accent)" }), "checked"),
        h("span", { class: "legend" }, h("i", { class: "dot", style: "background:var(--purple)" }), "fading, if not confirmed")),
      h("p", { class: "small", text: say }));
  };
})();
