// Replays protocol/conformance.json against src/hub.js and reports every difference from the recorded
// (Python) transcripts. Runs in any browser or runtime with ES modules, fetch and WebCrypto Ed25519:
// eqvoice-signalling/tests/test_conformance.py serves it to headless Chromium; with Node installed,
// `node tests/conformance_runner.js conformance.json` (see run_node.mjs) does the same.

import { Hub } from "../src/hub.js";

export function makeRng(seed = 0x9E3779B9) {
  let x = seed >>> 0;
  return (n) => {
    const out = new Uint8Array(n);
    for (let i = 0; i < n; i++) {
      x = (x ^ (x << 13)) >>> 0;
      x = (x ^ (x >>> 17)) >>> 0;
      x = (x ^ (x << 5)) >>> 0;
      out[i] = (x >>> 24) & 0xff;
    }
    return out;
  };
}

function canon(v) {
  if (Array.isArray(v)) return "[" + v.map(canon).join(",") + "]";
  if (v !== null && typeof v === "object") {
    return "{" + Object.keys(v).sort().map((k) => JSON.stringify(k) + ":" + canon(v[k])).join(",") + "}";
  }
  return JSON.stringify(v);
}

function fromHex(hex) {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16);
  return out;
}

class Replayer {
  constructor(setup) {
    this.now = 1000.0;
    this.events = [];
    this.conns = new Map();
    this.hub = new Hub({
      limits: setup.limits || {}, clock: () => this.now, rng: makeRng(), joinCode: setup.join_code ?? null,
      iceServers: setup.ice || [], wallClock: () => 1.7e9,
    });
  }

  async apply(step) {
    this.events = [];
    const ev = this.events;
    const self = this;
    switch (step.op) {
      case "connect": {
        const c = {
          name: step.conn, ip: step.ip || "10.0.0.1",
          send(msg) { self.events.push([c.name, msg]); },
          close(code, reason) { self.events.push([c.name, "close", code, reason]); },
        };
        this.conns.set(step.conn, c);
        if (!this.hub.connect(c)) ev.push([c.name, "refused"]);
        break;
      }
      case "send":
        await this.hub.handle(this.conns.get(step.conn), step.text);
        break;
      case "rawbytes":
        await this.hub.handle(this.conns.get(step.conn), fromHex(step.hex));
        break;
      case "advance": {
        const end = this.now + step.seconds;
        while (this.now < end - 1e-9) {
          this.now += Math.min(step.step ?? 1.0, end - this.now);
          this.hub.tick();
        }
        break;
      }
      case "disconnect":
        this.hub.disconnect(this.conns.get(step.conn), step.reserve ?? true);
        break;
      default:
        throw new Error("unknown op " + step.op);
    }
    return this.events;
  }
}

export async function runAll(doc) {
  const failures = [];
  let steps = 0;
  for (const sc of doc.scenarios) {
    const r = new Replayer(sc.setup);
    for (let i = 0; i < sc.steps.length; i++) {
      let got;
      try {
        got = await r.apply(sc.steps[i]);
      } catch (e) {
        failures.push({ scenario: sc.name, step: i, error: String(e && e.stack || e) });
        break;
      }
      steps += 1;
      const want = sc.transcript[i];
      if (canon(got) !== canon(want)) {
        failures.push({ scenario: sc.name, step: i, input: sc.steps[i], want, got });
        break;                                  // later steps would only repeat the damage
      }
    }
    if (!failures.length || failures[failures.length - 1].scenario !== sc.name) {
      const stats = r.hub.stats();
      if (canon(stats) !== canon(sc.stats)) failures.push({ scenario: sc.name, step: "stats", want: sc.stats, got: stats });
    }
  }
  return { scenarios: doc.scenarios.length, steps, failures };
}

async function cryptoSupport() {
  try {
    const key = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
    return !!key;
  } catch (e) {
    return false;
  }
}

if (typeof document !== "undefined") {
  (async () => {
    let result;
    try {
      if (!(await cryptoSupport())) {
        result = { unsupported: "WebCrypto Ed25519 is not available in this browser" };
      } else {
        const doc = await (await fetch("/conformance.json")).json();
        result = await runAll(doc);
      }
    } catch (e) {
      result = { crashed: String(e && e.stack || e) };
    }
    await fetch("/result", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(result) });
    document.title = "done";
  })();
}
