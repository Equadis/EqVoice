// With Node 20+ installed:  node tests/run_node.mjs
// Replays protocol/conformance.json against src/hub.js (the same check test_conformance.py runs in a browser).
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { runAll } from "./conformance_runner.js";

const here = dirname(fileURLToPath(import.meta.url));
const doc = JSON.parse(readFileSync(join(here, "..", "..", "protocol", "conformance.json"), "utf8"));
const res = await runAll(doc);
console.log(`${res.scenarios} scenarios, ${res.steps} steps, ${res.failures.length} failures`);
for (const f of res.failures) console.log(JSON.stringify(f, null, 1).slice(0, 2000));
process.exit(res.failures.length ? 1 : 0);
