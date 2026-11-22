/* Pompy's prompt scorer, in the browser. A line-by-line port of score.score_text and the
 * keyword tokenizer it uses (pompy/score.py, pompy/keywords.py).
 *
 * The rules themselves (regexes, word lists) are NOT written here: they come from Python in
 * data.json (`rules`), so there is one source of truth. tests/test_scorer_parity.py runs this
 * file in Node and checks it agrees with Python on every case.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.PompyScorer = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  function make(rules) {
    const R = rules.regex;
    const W = rules.words;
    // Non-global regexes for tests (a /g regex keeps lastIndex between .test() calls).
    const rx = (r) => new RegExp(r.source, r.flags);
    const rxg = (r) => new RegExp(r.source, r.flags + "g");
    const set = (a) => new Set(a);

    const STOP = set(W.stopwords);
    const VERBS = set(W.action_verbs);
    const FILLER = set(W.opening_filler);
    const SHELL = set(W.shell_words);
    const QOPEN = set(W.question_openers);
    const REPLY = set(W.reply_words);
    const SHORT = set(W.short_tokens);
    const ROUTINE = set(W.routine_verbs);
    const REQUEST = W.request_openers;

    const T = {};
    for (const k of Object.keys(R)) if (!Array.isArray(R[k])) T[k] = rx(R[k]);
    const DOTTED = rxg(R.dotted_acronym);
    const CLEAN = R.clean.map(rxg);
    const IDENT = rxg(R.identifier);
    const WORD = rxg(R.word);

    // Python's str.split(): whitespace runs, no empty strings.
    const split = (s) => {
      const t = s.trim();
      return t ? t.split(/\s+/) : [];
    };

    function cleanForWords(text) {
      text = text.replace(/’/g, "'").replace(DOTTED, "$1$2");
      for (const p of CLEAN) text = text.replace(p, " ");
      return text.replace(IDENT, " ").toLowerCase();
    }
    function words(text) {
      return cleanForWords(text).match(WORD) || [];
    }
    function isContent(t) {
      if (STOP.has(t) || VERBS.has(t)) return false;
      return t.length >= 3 || SHORT.has(t);
    }
    function contentTerms(text) {
      return words(text).filter(isContent);
    }
    function openingVerb(text) {
      for (const t of words(text).slice(0, 6)) {
        if (FILLER.has(t)) continue;
        return VERBS.has(t) ? t : null;
      }
      return null;
    }
    function openingStyle(text) {
      const parts = split(text);
      const first = parts.length ? parts[0].toLowerCase() : "";
      if (SHELL.has(first) && parts.length < 8) return "command";
      const toks = words(text);
      const lowered = toks.join(" ");
      if (!toks.length) return "other";
      if (toks.length <= 4 && REPLY.has(toks[0])) return "reply";
      let stripped = toks.filter((t) => !FILLER.has(t));
      if (!stripped.length) stripped = toks;
      const joined = stripped.join(" ");
      if (REQUEST.some((o) => joined.startsWith(o) || lowered.startsWith(o))) return "request";
      if (VERBS.has(stripped[0])) return "command";
      if (QOPEN.has(stripped[0]) || text.trimEnd().endsWith("?")) return "question";
      return "statement";
    }
    function isCorrection(text) {
      const t = text.trim();
      return T.correction_start.test(t) || T.correction_any.test(t);
    }
    function detect(text, hasPaste, hasImage) {
      const terms = contentTerms(text);
      const style = openingStyle(text);
      const hasVerb = words(text).some((t) => VERBS.has(t));
      return {
        goal: terms.length > 0 && (hasVerb || ["request", "question", "command"].includes(style)),
        context: T.context.test(text) || !!hasPaste,
        constraints: T.constraints.test(text),
        done: T.done.test(text),
        format: T.format.test(text),
        example: T.example.test(text) || !!hasImage,
        reference:
          !!hasPaste ||
          [T.ref_path, T.ref_url, T.ref_code, T.ref_mention, T.ref_ident, T.error].some((p) =>
            p.test(text)
          ),
      };
    }
    function role(text) {
      const style = openingStyle(text);
      if (style === "reply") return "reply";
      if (split(text).length <= 12 && isCorrection(text)) return "correction";
      if (style === "other") return "other";
      if (words(text).length <= 6 && ROUTINE.has(openingVerb(text))) return "routine";
      return "task";
    }
    function scoreText(text, hasPaste, hasImage) {
      const features = detect(text, hasPaste, hasImage);
      const r = role(text);
      const names = rules.features.map((f) => f.name);
      return {
        role: r,
        score: r === "task" ? names.filter((n) => features[n]).length : null,
        features,
        missing: names.filter((n) => !features[n]),
      };
    }
    return { scoreText, words, contentTerms, openingStyle, role, detect };
  }

  return { make };
});
