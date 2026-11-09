// Exercises src/worker.js (the Durable Object adapter) against tests/mock_cloudflare.js.
// Loaded by tests/adapter.html, which maps "cloudflare:workers" to the mock with an import map.

import { MockCtx, MockResponse, MockWebSocketPair } from "./mock_cloudflare.js";
import { b64u, registrationMessage } from "../src/hub.js";

globalThis.WebSocketPair = MockWebSocketPair;
globalThis.Response = MockResponse;

const { default: worker, HubDO, closeCode, limitsFromEnv, iceFromEnv, eventLogFromEnv } = await import("../src/worker.js");

const results = [];
function check(name, ok, detail) {
  results.push({ name, ok: !!ok, detail: ok ? undefined : String(detail ?? "") });
}
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// ---- time travel: the adapter reads Date.now()
const realNow = Date.now.bind(Date);
let offset = 0;
Date.now = () => realNow() + offset;

class Client {
  constructor(dobj, ctx, ip) { this.dobj = dobj; this.ctx = ctx; this.ip = ip; this.ws = null; this.key = null; }

  async connect() {
    const before = this.ctx.sockets.length;
    const res = await this.dobj.fetch(new Request("https://hub.example/ws", { headers: { "CF-Connecting-IP": this.ip } }));
    this.res = res;
    this.ws = this.ctx.sockets[before];
    return this;
  }

  hello() { return this.ws.sent.find((m) => m.t === "hello"); }

  async register(name, realm = "Testrealm") {
    this.key = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
    const pub = new Uint8Array(await crypto.subtle.exportKey("raw", this.key.publicKey));
    const sidBytes = crypto.getRandomValues(new Uint8Array(16));
    this.sid = b64u(sidBytes);
    this.name = name;
    this.realm = realm;
    this.pub = pub;
    const nonce = Uint8Array.from(atob(this.hello().nonce.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - this.hello().nonce.length % 4) % 4)), (c) => c.charCodeAt(0));
    const sig = new Uint8Array(await crypto.subtle.sign("Ed25519", this.key.privateKey, registrationMessage(nonce, this.sid, realm, name)));
    await this.send({ t: "register", v: 2, sid: this.sid, realm, character: name, pub: b64u(pub), sig: b64u(sig) });
    return this.ws.sent.find((m) => m.t === "registered");
  }

  async send(msg) { await this.dobj.webSocketMessage(this.ws, typeof msg === "string" ? msg : JSON.stringify(msg)); }
  take(t) { const out = this.ws.sent.filter((m) => m.t === t); this.ws.sent = this.ws.sent.filter((m) => m.t !== t); return out; }
}

const env = { MAX_CONNS_PER_IP: "3", LEASE_SECONDS: "30" };

try {
  // ---- configuration helpers
  check("closeCode keeps 1000 and 3000-4999", closeCode(1000) === 1000 && closeCode(4408) === 4408 && closeCode(4409) === 4409);
  check("closeCode maps the codes a Worker may not send", closeCode(1008) === 4008 && closeCode(1009) === 4009 && closeCode(1013) === 4013 && closeCode(1001) === 4001);
  check("limits come from the environment", same(limitsFromEnv({ LINK_RADIUS: "50", LEASE_SECONDS: "", DISTANCE_TOLERANCE: "x" }), { link_radius: 50 }), JSON.stringify(limitsFromEnv({ LINK_RADIUS: "50" })));
  check("the global connection cap comes from the environment", same(limitsFromEnv({ MAX_CONNECTIONS: "500" }), { max_connections: 500 }));
  {
    const lines = [];
    const realLog = console.log;
    console.log = (...a) => lines.push(a.join(" "));
    try {
      check("event logging is off unless asked for", eventLogFromEnv({}) === null && eventLogFromEnv({ LOG_EVENTS: "0" }) === null);
      const log = eventLogFromEnv({ LOG_EVENTS: "1" });
      log("registered", { character: "Alice", realm: "Testrealm", sid: "abcd1234", roster: 3 });
      log("proximity", { frm: "Alice", peers: "Bobby:12.0" });
      log("link_up", { a: "Alice", b: "Bobby", distance: 12, reported: "11.0/13.0" });
      log("link_down", { a: "Alice", b: "Bobby", why: "only one side reports the other" });
      log("error", { to: "Alice", code: "name_in_use", text: "Alice is online" });
      log("left", { character: "Alice", realm: "Testrealm", clean: true });
    } finally { console.log = realLog; }
    const text = lines.join("\n");
    check("production event lines carry no names", !/Alice|Bobby|abcd1234/.test(text), text);
    check("and keep what operators need", lines.length === 5 && lines[0] === '{"ev":"registered","realm":"Testrealm","roster":3}' &&
          lines[1] === '{"ev":"link_up","distance":12}' && lines[2].includes("only one side") && lines[3] === '{"ev":"error","code":"name_in_use"}' &&
          lines[4] === '{"ev":"left","realm":"Testrealm","clean":true}', text);
  }
  check("ice servers default and override", iceFromEnv({}).length === 1 && same(iceFromEnv({ STUN: "stun:a, stun:b" }), [{ urls: ["stun:a", "stun:b"] }]));
  check("a TURN relay with fixed credentials is added to the list", same(iceFromEnv({ STUN: "stun:a", TURN_URLS: "turn:t:3478, turns:t:443", TURN_USERNAME: "u", TURN_CREDENTIAL: "p" }), [{ urls: ["stun:a"] }, { urls: ["turn:t:3478", "turns:t:443"], username: "u", credential: "p" }]) && iceFromEnv({ TURN_URLS: "turn:t" }).length === 1);

  // ---- the router in front of the object
  const ctx = new MockCtx();
  let dobj = new HubDO(ctx, env);
  const stubEnv = { HUB: { idFromName: (n) => n, get: () => ({ fetch: (req) => dobj.fetch(req) }) } };
  let r = await worker.fetch(new Request("https://hub.example/health"), stubEnv);
  check("/health answers ok", r.status === 200 && (await r.text()) === "ok\n");
  r = await worker.fetch(new Request("https://hub.example/ws"), stubEnv);
  check("/ws without an upgrade is refused with 426", r.status === 426);
  r = await worker.fetch(new Request("https://hub.example/stats"), stubEnv);
  check("/stats is closed unless a token is configured", r.status === 200 && !(await r.text()).includes("sessions"));

  // ---- two players register through the adapter
  const alice = await new Client(dobj, ctx, "198.51.100.1").connect();
  check("a connection gets a 101 and a hello", alice.res.status === 101 && alice.res.webSocket && alice.hello().v === 2 && alice.hello().ice.length === 1);
  const regA = await alice.register("Alice");
  check("registration works through the adapter", regA && regA.sid === alice.sid && regA.roster.length === 0, JSON.stringify(alice.ws.sent));
  check("the session is saved as a small attachment", alice.ws.attachment && alice.ws.attachment.sid === alice.sid &&
        alice.ws.attachment.character === "Alice" && JSON.stringify(alice.ws.attachment).length < 600, JSON.stringify(alice.ws.attachment));
  check("the attachment holds no secret key material beyond the public key and token", !("priv" in alice.ws.attachment) && alice.ws.attachment.pub);
  const bob = await new Client(dobj, ctx, "198.51.100.2").connect();
  const regB = await bob.register("Bobby");
  check("the second player sees the first in the roster", same(regB.roster, [["Alice", alice.sid]]));
  check("and the first is told", alice.take("roster_update").some((m) => same(m.add, [["Bobby", bob.sid]])));
  check("an alarm is scheduled while sessions exist", ctx.alarmAt !== null && ctx.alarmAt > Date.now(), ctx.alarmAt);

  // ---- mutual proximity and relay
  await alice.send({ t: "proximity", peers: [["Bobby", 18, 1]] });
  await bob.send({ t: "proximity", peers: [["Alice", 19, 1]] });
  const pa = alice.take("peer_available"), pb = bob.take("peer_available");
  check("mutual proximity links the pair", pa.length === 1 && pb.length === 1 && pa[0].distance === 18.5, JSON.stringify([pa, pb]));
  await alice.send({ t: "offer", to: bob.sid, ep: 3, data: { sdp: "x" } });
  check("an offer is relayed to the linked peer", same(bob.take("offer"), [{ t: "offer", from: alice.sid, ep: 3, data: { sdp: "x" } }]));
  check("the alarm is now the shorter one", ctx.alarmAt - Date.now() <= 5000 + 50, ctx.alarmAt - Date.now());

  // ---- binary frames are not protocol
  await alice.dobj.webSocketMessage(alice.ws, new ArrayBuffer(4));
  check("binary frames get an error, not a crash", alice.take("error").some((m) => m.code === "bad_msg"));

  // ---- hibernation: a new object instance, the same connected sockets
  dobj = new HubDO(ctx, env);
  alice.dobj = dobj; bob.dobj = dobj;
  const st = JSON.parse(await (await dobj.fetch(new Request("https://hub.example/stats"))).text());
  check("after waking, both sessions are back", st.sessions === 2 && st.connections === 2 && st.realms === 1, JSON.stringify(st));
  const carol = await new Client(dobj, ctx, "198.51.100.3").connect();
  const regC = await carol.register("Carol");
  check("a newcomer's roster is complete after the wake", regC.roster.length === 2 && regC.roster.some((e) => e[0] === "Alice" && e[1] === alice.sid) &&
        regC.roster.some((e) => e[0] === "Bobby" && e[1] === bob.sid), JSON.stringify(regC.roster));
  alice.take("roster_update"); bob.take("roster_update");
  await alice.send({ t: "proximity", peers: [["Bobby", 18, 1]] });
  await bob.send({ t: "proximity", peers: [["Alice", 19, 1]] });
  check("clients' next reports re-link the pair (links are rebuilt, not stored)", alice.take("peer_available").length === 1 && bob.take("peer_available").length === 1);
  await alice.send({ t: "offer", to: bob.sid, ep: 4, data: {} });
  check("and signalling works again", bob.take("offer").length === 1);
  const dupe = await new Client(dobj, ctx, "198.51.100.4").connect();
  dupe.sid = null;
  const regDupe = await (async () => {
    const k = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
    const pub = new Uint8Array(await crypto.subtle.exportKey("raw", k.publicKey));
    const sid = b64u(crypto.getRandomValues(new Uint8Array(16)));
    const nonceText = dupe.hello().nonce;
    const nonce = Uint8Array.from(atob(nonceText.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - nonceText.length % 4) % 4)), (c) => c.charCodeAt(0));
    const sig = new Uint8Array(await crypto.subtle.sign("Ed25519", k.privateKey, registrationMessage(nonce, sid, "Testrealm", "Alice")));
    await dupe.send({ t: "register", v: 2, sid, realm: "Testrealm", character: "Alice", pub: b64u(pub), sig: b64u(sig) });
    return dupe.ws.sent.find((m) => m.t === "error");
  })();
  check("a restored name is still held against an impostor", regDupe && regDupe.code === "name_in_use", JSON.stringify(dupe.ws.sent));

  // ---- a close removes the player and tells the others
  alice.take("roster_update"); carol.take("roster_update");
  await dobj.webSocketClose(bob.ws, 1005, "", false);
  check("a closed socket leaves the roster at once", same(alice.take("roster_update").map((m) => m.remove), [[bob.sid]]));
  check("and the adapter answers the close frame with a legal code", bob.ws.closed && bob.ws.closed[0] === 1000, JSON.stringify(bob.ws.closed));
  check("and the link ended", alice.take("peer_out_of_range").length === 1);

  // ---- an error event is a close
  await dobj.webSocketError(carol.ws, new Error("boom"));
  check("a socket error leaves the roster too", alice.take("roster_update").some((m) => same(m.remove, [carol.sid])));

  // ---- idle sweep by alarm: Alice keeps pinging, Dina does not
  const dina = await new Client(dobj, ctx, "198.51.100.5").connect();
  await dina.register("Dina");
  alice.take("roster_update");
  offset += 20000;
  await alice.send({ t: "ping" });
  offset += 20000;                                   // 40 s since Dina last spoke, 20 s since Alice did
  await dobj.alarm();
  check("the alarm sweeps a silent session and tells the others", alice.take("roster_update").some((m) => same(m.remove, [dina.sid])) &&
        dina.ws.closed && dina.ws.closed[0] === 4408, JSON.stringify(dina.ws.closed));
  check("a live session survives the sweep", !alice.ws.closed);

  // ---- an unregistered socket is dropped after the register timeout
  const lurker = await new Client(dobj, ctx, "198.51.100.6").connect();
  offset += 12000;
  await dobj.alarm();
  check("a socket that never registers is closed", lurker.ws.closed && lurker.ws.closed[0] === 4408, JSON.stringify(lurker.ws.closed));

  // ---- per-address cap, closed with a legal code
  const e1 = await new Client(dobj, ctx, "203.0.113.9").connect();
  await new Client(dobj, ctx, "203.0.113.9").connect();
  await new Client(dobj, ctx, "203.0.113.9").connect();
  const e4 = await new Client(dobj, ctx, "203.0.113.9").connect();
  check("a fourth connection from one address is refused", e4.ws.closed && e4.ws.closed[0] === 4013 && !e1.ws.closed, JSON.stringify(e4.ws.closed));

  // ---- a socket the object has no record of, with no attachment, is dropped; with one it is adopted
  const stray = new (Object.getPrototypeOf(alice.ws).constructor)("stray");
  await dobj.webSocketMessage(stray, "{}");
  check("an unknown socket is closed", stray.closed && stray.closed[0] === 4000);
} catch (e) {
  results.push({ name: "runner crashed", ok: false, detail: String(e && e.stack || e) });
}

Date.now = realNow;
await fetch("/result", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ results }) });
document.title = "done";
