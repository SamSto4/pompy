// Runs the browser scorer in Node: `node score_cases.js rules.json cases.json` → JSON results.
// Used by tests/test_scorer_parity.py to check the browser and Python agree.
const fs = require("fs");
const path = require("path");
const scorer = require(path.join(__dirname, "..", "..", "pompy", "dashboard", "scorer.js"));
const rules = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const cases = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const s = scorer.make(rules);
const out = cases.map((c) => s.scoreText(c.text, c.paste, c.image));
process.stdout.write(JSON.stringify(out));
