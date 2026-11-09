// The EqVoice hub, protocol v2: who is online, who may talk to whom, and the abuse limits.
//
// A line-for-line port of eqvoice-signalling/eqvoice_signalling/hub.py. That file is the reference; the
// two are held together by protocol/conformance.json, a set of recorded conversations that both must
// reproduce message for message (eqvoice-signalling/tests/test_conformance.py).
//
// Pure logic, no platform APIs. A connection is any object with `ip`, `send(obj)` and `close(code, reason)`;
// time, randomness and signature checking are injected. The Cloudflare Durable Object in worker.js, the
// browser-based conformance runner and the unit tests all drive exactly this code.
//
// What the hub holds, per realm, only while a session is connected:
//     character name <-> ephemeral session id (plus a public key and a server-issued token)
// What it never holds: accounts, e-mail, IP addresses in any message or roster, positions, history.
//
// Differences from the Python hub, all deliberate:
//   * handle() returns a Promise, because checking an Ed25519 signature with WebCrypto is asynchronous.
//     Messages from one connection are still processed strictly in order.
//   * restore() rebuilds a session after the Durable Object woke from hibernation (see worker.js).
//   * limits use the same snake_case names as the Python Limits dataclass, so one configuration vocabulary
//     serves both.

export const PROTOCOL_VERSION = 2;

const SID_RE = /^[A-Za-z0-9_-]{22}$/;
const REALM_RE = /^[A-Za-z0-9][A-Za-z0-9 '.\-]{0,31}$/;
const SIGNAL_KINDS = ["offer", "answer", "ice", "bye"];
const enc = new TextEncoder();

export const REG_CONTEXT = enc.encode("OV2-REG\u0000");

export const DEFAULT_LIMITS = {
  max_msg_bytes: 16 * 1024,
  max_signal_data_bytes: 8 * 1024,
  bucket_capacity: 100.0,
  bucket_rate: 50.0,
  prox_capacity: 8.0,            // proximity messages: burst
  prox_rate: 4.0,                // ... and per second, sustained
  strikes_to_close: 5,
  link_signals_per_minute: 120,
  register_timeout: 10.0,
  idle_timeout: 30.0,            // clients ping every 10 s
  reserve_seconds: 30.0,         // a dropped session's name stays reserved for its own key
  max_conns_per_ip: 20,
  max_connections: 10000,        // all addresses together: a ceiling on what a flood of cheap connections can cost
  registers_per_ip_per_minute: 12,
  max_report_entries: 64,
  max_distance: 500.0,
  report_ttl: 6.0,               // a proximity report older than this counts for nothing
  leave_grace: 3.0,              // a link survives this long without agreeing reports
  distance_tolerance: 12.0,      // yards; both ends sample the same pair at different instants
  link_radius: 90.0,             // yards; beyond this two sessions are never linked (a raised voice carries 85, plus the leave margin)
  realm_cap: 5000,
  roster_chunk: 300,
};

// ------------------------------------------------------------------ helpers

export function b64u(bytes) {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function unb64u(text) {
  if (typeof text !== "string" || text.length > 200 || !/^[A-Za-z0-9_-]*$/.test(text)) throw new Error("bad base64");
  const pad = text + "=".repeat((4 - (text.length % 4)) % 4);
  const bin = atob(pad.replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function concat(...parts) {
  let n = 0;
  for (const p of parts) n += p.length;
  const out = new Uint8Array(n);
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
}

export function registrationMessage(nonce, sid, realm, character) {
  const z = new Uint8Array([0]);
  return concat(REG_CONTEXT, nonce, z, enc.encode(sid), z, enc.encode(realm), z, enc.encode(character));
}

export function validName(name) {
  if (typeof name !== "string") return false;
  const n = [...name].length;
  return n >= 2 && n <= 12 && /^\p{L}+$/u.test(name);
}

export function realmKey(realm) {
  return realm.toLowerCase().split(/\s+/).filter(Boolean).join(" ");
}

/** One decimal, halves rounded up: the same formula as hub.round1 in Python. */
export function round1(x) {
  return Math.floor(x * 10 + 0.5) / 10;
}

const isInt = (v) => typeof v === "number" && Number.isInteger(v);
const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

/** Verify an Ed25519 signature with WebCrypto (Cloudflare Workers and current browsers both have it). */
export async function webCryptoVerify(pub, sig, message) {
  const key = await crypto.subtle.importKey("raw", pub, { name: "Ed25519" }, false, ["verify"]);
  return crypto.subtle.verify({ name: "Ed25519" }, key, sig, message);
}

class Bucket {
  constructor(capacity, rate, now) {
    this.capacity = capacity; this.rate = rate; this.tokens = capacity; this.t = now;
  }
  take(now) {
    this.tokens = Math.min(this.capacity, this.tokens + (now - this.t) * this.rate);
    this.t = now;
    if (this.tokens < 1) return false;
    this.tokens -= 1;
    return true;
  }
}

const pairKey = (a, b) => (a < b ? a + "\u0000" + b : b + "\u0000" + a);

// ---------------------------------------------------------------------- hub

export class Hub {
  constructor({ limits = {}, clock = () => performance.now() / 1000, rng = null, joinCode = null, iceServers = [],
                wallClock = () => Date.now() / 1000, verify = webCryptoVerify, trace = null } = {}) {
    this.limits = { ...DEFAULT_LIMITS, ...limits };
    this.clock = clock;
    this.rng = rng || ((n) => crypto.getRandomValues(new Uint8Array(n)));
    this.wall = wallClock;
    this.joinCode = joinCode;
    this.iceServers = iceServers;
    this.verify = verify;
    this.trace = trace;             // trace(event, fields): narration for debugging a deployment; off by default
    this.states = new Map();        // conn -> Session
    this.bySid = new Map();         // sid -> Session
    this.realms = new Map();        // rkey -> Map(ckey -> Session)
    this.reserved = new Map();      // rkey \0 ckey -> {pub, sid, token, expires}
    this.links = new Map();         // sorted sid pair -> {a, b, last_ok, dist}
    this.reportedBy = new Map();    // rkey \0 target ckey -> Map(sid -> true), in insertion order
    this.ipConns = new Map();
    this.ipRegs = new Map();
    this.counters = { connections: 0, registered: 0, rejected: 0, signals: 0, rate_limited: 0, errors: 0,
                      links_up: 0, proximity: 0, roster_msgs: 0, takeovers: 0 };
  }

  // ------------------------------------------------------------ plumbing

  _t(event, fields) {
    if (this.trace) this.trace(event, fields);
  }

  _send(s, msg) {
    if (!s.closed) s.conn.send(msg);
  }

  _error(s, code, text = "", ref = null) {
    this.counters.errors += 1;
    this._t("error", { to: s.character || "(unregistered)", code, text });
    const msg = { t: "error", code, msg: text };
    if (ref !== null) msg.ref = ref;
    this._send(s, msg);
  }

  _kill(s, code, reason, reserve = true) {
    if (s.closed) return;
    s.conn.close(code, reason);
    this.disconnect(s.conn, reserve);
  }

  _broadcast(rkey, msg, exclude = null) {
    const members = this.realms.get(rkey);
    if (!members) return;
    for (const other of members.values()) {
      if (other !== exclude) {
        this._send(other, msg);
        this.counters.roster_msgs += 1;
      }
    }
  }

  // ------------------------------------------------------------ lifecycle

  /** False when the caller should refuse the connection (global or per-IP cap). */
  connect(conn) {
    const ip = conn.ip;
    if (this.states.size >= this.limits.max_connections || (this.ipConns.get(ip) || 0) >= this.limits.max_conns_per_ip) {
      this.counters.rejected += 1;
      return false;
    }
    this.ipConns.set(ip, (this.ipConns.get(ip) || 0) + 1);
    const now = this.clock(), L = this.limits;
    const s = this._newSession(conn, this.rng(16), now);
    this.states.set(conn, s);
    this.counters.connections += 1;
    this._send(s, {
      t: "hello", v: PROTOCOL_VERSION, nonce: b64u(s.nonce), now: this.wall(),
      join_code: Boolean(this.joinCode), ice: this.iceServers,
      limits: { idle: L.idle_timeout, report_ttl: L.report_ttl, link_radius: L.link_radius, max_report: L.max_report_entries },
    });
    return true;
  }

  _newSession(conn, nonce, now) {
    const L = this.limits;
    return {
      conn, nonce, born: now, last_seen: now,
      bucket: new Bucket(L.bucket_capacity, L.bucket_rate, now),
      prox_bucket: new Bucket(L.prox_capacity, L.prox_rate, now),
      sid: null, pub: null, realm: "", rkey: "", character: "", ckey: "", token: "",
      reports: new Map(),            // target ckey -> [distance, server time]
      links: new Set(),              // sids
      strikes: [], link_budget: new Map(), closed: false, registered_at: 0,
      busy: null, queue: [],
    };
  }

  /** Forget a connection. `reserve=false` (a clean `unregister`) frees the name at once. */
  disconnect(conn, reserve = true) {
    const s = this.states.get(conn);
    if (!s || s.closed) return;
    this.states.delete(conn);
    s.closed = true;
    const ip = conn.ip;
    this.ipConns.set(ip, Math.max(0, (this.ipConns.get(ip) || 1) - 1));
    if (!this.ipConns.get(ip)) this.ipConns.delete(ip);
    if (s.sid === null) return;
    this._t("left", { character: s.character, realm: s.realm, clean: !reserve });
    // links first, while the roster still lists this session
    for (const [pk, link] of [...this.links]) {
      if (link.a === s.sid || link.b === s.sid) this._dropLink(pk, true, "a side disconnected");
    }
    for (const ck of s.reports.keys()) {
      const key = s.rkey + "\u0000" + ck;
      const reporters = this.reportedBy.get(key);
      if (reporters) {
        reporters.delete(s.sid);
        if (reporters.size === 0) this.reportedBy.delete(key);
      }
    }
    if (this.bySid.get(s.sid) === s) this.bySid.delete(s.sid);
    const realm = this.realms.get(s.rkey);
    if (realm && realm.get(s.ckey) === s) {
      realm.delete(s.ckey);
      if (realm.size === 0) this.realms.delete(s.rkey);
      this._broadcast(s.rkey, { t: "roster_update", remove: [s.sid] });
      this._t("roster_update", { remove: s.character, sent_to: realm.size });
    }
    if (reserve) {
      this.reserved.set(s.rkey + "\u0000" + s.ckey,
        { pub: s.pub, sid: s.sid, token: s.token, expires: this.clock() + this.limits.reserve_seconds });
    }
  }

  tick() {
    const now = this.clock(), L = this.limits;
    for (const s of [...this.states.values()]) {
      if (s.sid === null && now - s.born > L.register_timeout) {
        this._error(s, "register_timeout", `register within ${pyInt(L.register_timeout)}s`);
        this._kill(s, 4408, "register timeout");
      } else if (now - s.last_seen > L.idle_timeout) {
        this._kill(s, 4408, "idle");
      }
    }
    for (const [k, r] of [...this.reserved]) if (r.expires <= now) this.reserved.delete(k);
    for (const [ip, q] of [...this.ipRegs]) if (q.length === 0 || now - q[q.length - 1] > 60) this.ipRegs.delete(ip);
    for (const pk of [...this.links.keys()]) {
      const link = this.links.get(pk);
      if (!link) continue;
      const a = this.bySid.get(link.a), b = this.bySid.get(link.b);
      if (!a || !b) this._dropLink(pk, true, "a side is gone");
      else this._evaluate(a, b, now);
    }
  }

  /** True while something time-driven is pending, so a host that sleeps between events knows to schedule a tick. */
  needsTick() {
    if (this.links.size || this.reserved.size) return true;
    for (const s of this.states.values()) if (s.sid === null) return true;
    return false;
  }

  // ------------------------------------------------------------- dispatch

  /** Process one raw message. Returns a Promise only when the message needed one (registration). */
  handle(conn, raw) {
    const s = this.states.get(conn);
    if (!s) return undefined;
    if (s.busy) {                                // a registration is being verified: keep order
      if (s.queue.length >= 32) { this._kill(s, 1008, "backlog"); return undefined; }
      s.queue.push(raw);
      return s.busy;
    }
    const p = this._handleOne(s, raw);
    if (p && typeof p.then === "function") {
      s.busy = p.then(() => this._drain(s), () => this._drain(s));
      return s.busy;
    }
    return undefined;
  }

  _drain(s) {
    s.busy = null;
    while (s.queue.length && !s.busy && !s.closed) {
      const p = this._handleOne(s, s.queue.shift());
      if (p && typeof p.then === "function") {
        s.busy = p.then(() => this._drain(s), () => this._drain(s));
      }
    }
  }

  _handleOne(s, raw) {
    const now = this.clock();
    s.last_seen = now;
    if (typeof raw !== "string") {
      this._error(s, "bad_msg", "text frames only");
      return undefined;
    }
    if (raw.length > this.limits.max_msg_bytes) {
      this._error(s, "too_big", "message too large");
      this._kill(s, 1009, "too big");
      return undefined;
    }
    if (!s.bucket.take(now)) {
      this.counters.rate_limited += 1;
      s.strikes.push(now);
      while (s.strikes.length && now - s.strikes[0] > 60) s.strikes.shift();
      this._error(s, "rate_limited", "slow down");
      if (s.strikes.length >= this.limits.strikes_to_close) this._kill(s, 1008, "rate limit");
      return undefined;
    }
    let msg;
    try {
      msg = JSON.parse(raw);
    } catch (e) {
      this._error(s, "bad_json");
      return undefined;
    }
    if (!isObj(msg) || typeof msg.t !== "string") {
      this._error(s, "bad_msg", "need an object with a string 't'");
      return undefined;
    }
    const ref = (typeof msg.id === "string" || isInt(msg.id)) ? msg.id : null;
    const kind = msg.t;
    if (kind === "ping") {
      const pong = { t: "pong", now: this.wall() };
      if (isNum(msg.ts)) pong.ts = msg.ts;
      this._send(s, pong);
    } else if (kind === "register") {
      return this._register(s, msg, ref);
    } else if (s.sid === null) {
      this._error(s, "not_registered", "register first", ref);
    } else if (kind === "proximity") {
      this._proximity(s, msg, ref);
    } else if (SIGNAL_KINDS.includes(kind)) {
      this._signal(s, kind, msg, ref);
    } else if (kind === "unregister") {
      this._kill(s, 1000, "bye", false);
    } else {
      this._error(s, "bad_msg", "unknown type", ref);
    }
    return undefined;
  }

  // ------------------------------------------------------------- register

  _register(s, msg, ref) {
    const L = this.limits;
    if (s.sid !== null) {
      this._error(s, "already_registered", "reconnect to change identity", ref);
      return undefined;
    }
    if (msg.v !== PROTOCOL_VERSION) {
      this._error(s, "unsupported_version", `speak v${PROTOCOL_VERSION}`, ref);
      return undefined;
    }
    const now = this.clock();
    let regs = this.ipRegs.get(s.conn.ip);
    if (!regs) { regs = []; this.ipRegs.set(s.conn.ip, regs); }
    while (regs.length && now - regs[0] > 60) regs.shift();
    if (regs.length >= L.registers_per_ip_per_minute) {
      this._error(s, "ip_rate", "too many registrations from this address", ref);
      return undefined;
    }
    regs.push(now);
    const { sid, realm, character } = msg;
    if (typeof sid !== "string" || !SID_RE.test(sid)) {
      this._error(s, "bad_sid", "session id must be 22 url-safe base64 characters", ref);
      return undefined;
    }
    if (typeof realm !== "string" || !REALM_RE.test(realm)) {
      this._error(s, "bad_realm", "malformed realm name", ref);
      return undefined;
    }
    if (!validName(character)) {
      this._error(s, "bad_name", "malformed character name", ref);
      return undefined;
    }
    if (this.joinCode !== null) {
      const code = msg.code;
      if (typeof code !== "string" || !constantTimeEqual(enc.encode(code), enc.encode(this.joinCode))) {
        this._error(s, "bad_code", "wrong join code", ref);
        return undefined;
      }
    }
    return this._verifyThenRegister(s, msg, ref, sid, realm, character);
  }

  async _verifyThenRegister(s, msg, ref, sid, realm, character) {
    let ok = false;
    try {
      const pub = unb64u(msg.pub), sig = unb64u(msg.sig);
      if (pub.length !== 32 || sig.length !== 64) throw new Error("length");
      ok = await this.verify(pub, sig, registrationMessage(s.nonce, sid, realm, character));
    } catch (e) {
      ok = false;
    }
    if (s.closed) return;
    if (!ok) {
      this._error(s, "bad_sig", "registration signature did not verify", ref);
      return;
    }
    this._completeRegistration(s, msg, ref, sid, realm, character, unb64u(msg.pub));
  }

  _completeRegistration(s, msg, ref, sid, realm, character, pub) {
    const L = this.limits;
    const now = this.clock();
    const rkey = realmKey(realm), ckey = character.toLowerCase();
    const holder = this.realms.get(rkey)?.get(ckey) ?? null;
    const existing = this.bySid.get(sid) ?? null;
    if (existing !== null && existing !== holder) {
      this._error(s, "sid_taken", "session id in use", ref);
      return;
    }
    if (holder !== null) {
      if (!bytesEqual(holder.pub, pub)) {
        this._error(s, "name_in_use", "that character is already online", ref);
        return;
      }
      this.counters.takeovers += 1;
      this._kill(holder, 4409, "replaced by a newer connection");          // same key reconnecting
    } else {
      const res = this.reserved.get(rkey + "\u0000" + ckey);
      if (res && (!bytesEqual(res.pub, pub) || (res.token && msg.token !== res.token))) {
        this._error(s, "name_in_use", "that character was just online; retry shortly", ref);
        return;
      }
    }
    let members = this.realms.get(rkey);
    if (!members) { members = new Map(); this.realms.set(rkey, members); }
    if (members.size >= L.realm_cap) {
      this._error(s, "realm_full", "too many sessions on this realm", ref);
      return;
    }
    this.reserved.delete(rkey + "\u0000" + ckey);
    s.sid = sid; s.pub = pub; s.realm = realm; s.rkey = rkey; s.character = character; s.ckey = ckey;
    s.token = b64u(this.rng(18));
    s.registered_at = now;
    this.bySid.set(sid, s);
    members.set(ckey, s);
    this.counters.registered += 1;
    const roster = [];
    for (const o of members.values()) if (o !== s) roster.push([o.character, o.sid]);
    const first = roster.slice(0, L.roster_chunk);
    let rest = roster.slice(L.roster_chunk);
    const reply = { t: "registered", sid, token: s.token, lease: L.reserve_seconds, now: this.wall(), roster: first, more: rest.length };
    if (ref !== null) reply.id = ref;
    this._send(s, reply);
    this._t("registered", { character, realm, sid: sid.slice(0, 8), roster: roster.length });
    this._t("roster_sent", { to: character, entries: first.length, more: rest.length });
    while (rest.length) {
      this._send(s, { t: "roster_update", add: rest.slice(0, L.roster_chunk) });
      this.counters.roster_msgs += 1;
      rest = rest.slice(L.roster_chunk);
    }
    this._broadcast(rkey, { t: "roster_update", add: [[character, sid]] }, s);
    this._t("roster_update", { add: character, sent_to: Math.max(0, members.size - 1) });
    // reports other sessions already hold about this name may now complete a pair
    const reporters = this.reportedBy.get(rkey + "\u0000" + ckey);
    if (reporters) {
      for (const repSid of [...reporters.keys()]) {
        const other = this.bySid.get(repSid);
        if (other && other !== s) this._evaluate(s, other, now);
      }
    }
  }

  // ------------------------------------------------------------ proximity

  _proximity(s, msg, ref) {
    const now = this.clock();
    if (!s.prox_bucket.take(now)) {
      this._error(s, "rate_limited", "proximity updates are limited", ref);
      return;
    }
    const peers = msg.peers;
    const L = this.limits;
    if (!Array.isArray(peers)) {
      this._error(s, "bad_msg", "peers must be a list", ref);
      return;
    }
    if (peers.length > L.max_report_entries) {
      this._error(s, "too_many", `at most ${L.max_report_entries} entries`, ref);
      return;
    }
    const next = new Map();
    for (const item of peers) {
      if (!Array.isArray(item) || item.length < 2 || !validName(item[0])) {
        this._error(s, "bad_msg", "entries are [name, yards, ts]", ref);
        return;
      }
      const d = item[1];
      if (!isNum(d) || d < 0 || d > L.max_distance) {
        this._error(s, "bad_msg", "bad distance", ref);
        return;
      }
      const ck = item[0].toLowerCase();
      if (ck === s.ckey) continue;
      next.set(ck, [d, now]);
    }
    this.counters.proximity += 1;
    this._t("proximity", { frm: s.character, peers: peers.map((i) => `${i[0]}:${Number(i[1]).toFixed(1)}`).join(",") || "(none)" });
    const old = s.reports;
    s.reports = next;
    for (const ck of old.keys()) {
      if (!next.has(ck)) {
        const key = s.rkey + "\u0000" + ck;
        const reporters = this.reportedBy.get(key);
        if (reporters) {
          reporters.delete(s.sid);
          if (reporters.size === 0) this.reportedBy.delete(key);
        }
      }
    }
    for (const ck of next.keys()) {
      const key = s.rkey + "\u0000" + ck;
      let reporters = this.reportedBy.get(key);
      if (!reporters) { reporters = new Map(); this.reportedBy.set(key, reporters); }
      reporters.set(s.sid, true);
    }
    const order = [...old.keys(), ...[...next.keys()].filter((k) => !old.has(k))];     // a fixed order: transcripts must repeat
    for (const ck of order) {
      const other = this.realms.get(s.rkey)?.get(ck);
      if (other && other !== s) this._evaluate(s, other, now);
    }
  }

  /** Decide whether a and b are mutually near, and raise or lower their link. */
  _evaluate(a, b, now) {
    const L = this.limits;
    const ra = a.reports.get(b.ckey), rb = b.reports.get(a.ckey);
    const ok = ra !== undefined && rb !== undefined && now - ra[1] <= L.report_ttl && now - rb[1] <= L.report_ttl
      && Math.abs(ra[0] - rb[0]) <= L.distance_tolerance && Math.min(ra[0], rb[0]) <= L.link_radius;
    const pk = pairKey(a.sid, b.sid);
    const link = this.links.get(pk);
    if (ok) {
      const dist = (ra[0] + rb[0]) / 2.0;
      if (!link) {
        this.links.set(pk, { a: a.sid, b: b.sid, last_ok: now, dist });
        a.links.add(b.sid);
        b.links.add(a.sid);
        this.counters.links_up += 1;
        this._t("link_up", { a: a.character, b: b.character, distance: round1(dist), reported: `${ra[0].toFixed(1)}/${rb[0].toFixed(1)}` });
        this._send(a, { t: "peer_available", sid: b.sid, character: b.character, distance: round1(dist) });
        this._send(b, { t: "peer_available", sid: a.sid, character: a.character, distance: round1(dist) });
      } else {
        link.last_ok = now;
        link.dist = dist;
      }
    } else if (link && now - link.last_ok >= L.leave_grace) {
      this._dropLink(pk, true, this._whyNot(ra, rb, now));
    }
  }

  _whyNot(ra, rb, now) {
    const L = this.limits;
    if (ra === undefined || rb === undefined) return "only one side reports the other";
    if (now - ra[1] > L.report_ttl || now - rb[1] > L.report_ttl) return "a report went stale";
    if (Math.abs(ra[0] - rb[0]) > L.distance_tolerance) return `the two distances disagree (${ra[0].toFixed(1)} vs ${rb[0].toFixed(1)})`;
    return `farther than ${L.link_radius.toFixed(0)} yd (${ra[0].toFixed(1)} / ${rb[0].toFixed(1)})`;
  }

  _dropLink(pk, notifyBoth, why = "") {
    const link = this.links.get(pk);
    if (!link) return;
    this.links.delete(pk);
    const a = this.bySid.get(link.a), b = this.bySid.get(link.b);
    this._t("link_down", { a: a ? a.character : "?", b: b ? b.character : "?", why });
    if (a) a.links.delete(link.b);
    if (b) b.links.delete(link.a);
    if (notifyBoth) {
      if (a && b) {
        this._send(a, { t: "peer_out_of_range", sid: b.sid, character: b.character });
        this._send(b, { t: "peer_out_of_range", sid: a.sid, character: a.character });
      } else if (a) {
        this._send(a, { t: "peer_out_of_range", sid: link.b, character: "" });
      } else if (b) {
        this._send(b, { t: "peer_out_of_range", sid: link.a, character: "" });
      }
    }
  }

  // --------------------------------------------------------------- signal

  _signal(s, kind, msg, ref) {
    const to = msg.to, data = msg.data;
    const ep = "ep" in msg ? msg.ep : 0;
    if (typeof to !== "string" || !isInt(ep) || !(ep >= 0 && ep < 2 ** 31)) {
      this._error(s, "bad_msg", "bad signal", ref);
      return;
    }
    // Same answer for "offline" and "not nearby", so this cannot be used to find out who is online.
    if (!s.links.has(to)) {
      this._error(s, "not_linked", "peer unavailable", ref);
      return;
    }
    if (JSON.stringify(data === undefined ? null : data).length > this.limits.max_signal_data_bytes) {
      this._error(s, "too_big", "signal payload too large", ref);
      return;
    }
    const now = this.clock();
    let q = s.link_budget.get(to);
    if (!q) { q = []; s.link_budget.set(to, q); }
    while (q.length && now - q[0] > 60) q.shift();
    if (q.length >= this.limits.link_signals_per_minute) {
      this._error(s, "rate_limited", "too many signals to this peer", ref);
      return;
    }
    q.push(now);
    const peer = this.bySid.get(to);
    if (!peer) {
      this._error(s, "not_linked", "peer unavailable", ref);
      return;
    }
    this.counters.signals += 1;
    this._t("signal", { kind, frm: s.character, to: peer.character, ep });
    this._send(peer, { t: kind, from: s.sid, ep, data: data === undefined ? null : data });
  }

  // ----------------------------------------------------------------- misc

  stats() {
    let largest = 0;
    for (const r of this.realms.values()) largest = Math.max(largest, r.size);
    return {
      connections: this.states.size, sessions: this.bySid.size, realms: this.realms.size, largest_realm: largest,
      links: this.links.size, reserved: this.reserved.size, totals: { ...this.counters },
    };
  }

  /**
   * Put a session back after the host process was evicted and restarted while the socket stayed connected
   * (Durable Object hibernation). `att` is what snapshot() returned earlier. Reports and links are not
   * restored: clients repeat their proximity report every few seconds, which rebuilds them.
   */
  restore(conn, att) {
    if (this.states.has(conn)) return this.states.get(conn);
    const now = this.clock();
    conn.ip = att.ip;
    this.ipConns.set(att.ip, (this.ipConns.get(att.ip) || 0) + 1);
    const s = this._newSession(conn, new Uint8Array(16), now);
    this.states.set(conn, s);
    if (att.sid) {
      Object.assign(s, { sid: att.sid, pub: unb64u(att.pub), realm: att.realm, rkey: realmKey(att.realm),
                         character: att.character, ckey: att.character.toLowerCase(), token: att.token,
                         registered_at: att.registered_at || now });
      let members = this.realms.get(s.rkey);
      if (!members) { members = new Map(); this.realms.set(s.rkey, members); }
      members.set(s.ckey, s);
      this.bySid.set(s.sid, s);
    }
    return s;
  }

  /** The small record worth keeping with a connection so restore() can rebuild it. */
  snapshot(conn) {
    const s = this.states.get(conn);
    if (!s) return null;
    return { ip: conn.ip, sid: s.sid, pub: s.pub ? b64u(s.pub) : null, realm: s.realm, character: s.character,
             token: s.token, registered_at: s.registered_at };
  }
}

function pyInt(x) {
  return String(Math.trunc(x));
}

function bytesEqual(a, b) {
  if (!a || !b || a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a[i] ^ b[i];
  return d === 0;
}

function constantTimeEqual(a, b) {
  return bytesEqual(a, b);
}
