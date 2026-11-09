// EqVoice signalling on Cloudflare Workers: one Durable Object runs the hub (hub.js), the Worker in front of it
// only routes WebSockets to that object.
//
//   client --wss--> Worker (this file, default export) --> HubDO (one global instance, "main") --> hub.js
//
// No voice passes through here, only a few small JSON messages per player. Voice is peer to peer (WebRTC).
//
// Hibernation. The object uses the WebSocket Hibernation API, so it is evicted from memory between messages
// and billed only while it actually runs. Eviction wipes in-memory state while the sockets stay connected, so
// every connection keeps a small attachment (character, realm, session id, public key, token: about 200
// bytes) from which the constructor rebuilds the roster. What is not rebuilt is rebuilt by the clients
// themselves within a few seconds: proximity reports (repeated every 4 s) and with them the links. Name
// reservations after a dropped connection are held in memory only and are forgotten if the object sleeps.
//
// Needs the Durable Object to be SQLite-backed (see wrangler.toml), which is also what the Workers Free plan
// requires. Compatibility date 2026-04-07 or later is assumed (the runtime then answers WebSocket close
// frames itself).

import { DurableObject } from "cloudflare:workers";
import { Hub } from "./hub.js";

const DEFAULT_ICE = [{ urls: ["stun:stun.cloudflare.com:3478", "stun:stun.l.google.com:19302"] }];
const TICK_BUSY_MS = 5000;        // while links exist: re-evaluate them promptly
const TICK_IDLE_MS = 15000;       // while only sessions exist: idle sweep and reservation expiry
const TICK_UNREGISTERED_MS = 5000;

/** Hub limits from the Worker's environment variables (all optional; names mirror the Python server's flags). */
export function limitsFromEnv(env) {
  const num = (v) => (v === undefined || v === null || v === "" ? undefined : Number(v));
  const pairs = {
    reserve_seconds: num(env.LEASE_SECONDS),
    link_radius: num(env.LINK_RADIUS),
    distance_tolerance: num(env.DISTANCE_TOLERANCE),
    max_conns_per_ip: num(env.MAX_CONNS_PER_IP),
    max_connections: num(env.MAX_CONNECTIONS),
    registers_per_ip_per_minute: num(env.REGISTERS_PER_MINUTE),
  };
  const out = {};
  for (const [k, v] of Object.entries(pairs)) if (Number.isFinite(v)) out[k] = v;
  return out;
}

/** `TRACE=1` (a Wrangler var, e.g. `wrangler dev --var TRACE:1`) narrates the hub in the log. Shows character names: testing only. */
export function traceFromEnv(env) {
  if (!env.TRACE || env.TRACE === "0") return null;
  return (event, fields) => console.log("TRACE hub " + event + " " + Object.entries(fields).map(([k, v]) => k + "=" + v).join(" "));
}

/**
 * `LOG_EVENTS=1`: the production logging. A short JSON line for each event worth knowing about, and never who it was about
 * (no character names, no addresses): enough to see registrations, departures, links forming and ending, refusals and their
 * reasons, in `wrangler tail` or the dashboard's logs. `TRACE=1` (names included) is for testing and wins if both are set.
 */
export function eventLogFromEnv(env) {
  if (!env.LOG_EVENTS || env.LOG_EVENTS === "0") return null;
  const keep = {
    registered: ["realm", "roster"],
    left: ["realm", "clean"],
    error: ["code"],
    link_up: ["distance"],
    link_down: ["why"],
  };
  return (event, fields) => {
    const keys = keep[event];
    if (!keys) return;
    const out = { ev: event };
    for (const k of keys) if (fields[k] !== undefined) out[k] = fields[k];
    console.log(JSON.stringify(out));
  };
}

export function iceFromEnv(env) {
  const list = (env.STUN || "").split(",").map((s) => s.trim()).filter(Boolean);
  const out = list.length ? [{ urls: list }] : [...DEFAULT_ICE];
  // A TURN relay with fixed credentials (TURN_URLS comma separated, TURN_USERNAME, TURN_CREDENTIAL): for the players whose network allows no
  // direct path (two strict NATs). Voice stays end to end encrypted; the relay only carries it.
  const turn = (env.TURN_URLS || "").split(",").map((s) => s.trim()).filter(Boolean);
  if (turn.length && env.TURN_USERNAME && env.TURN_CREDENTIAL) out.push({ urls: turn, username: String(env.TURN_USERNAME), credential: String(env.TURN_CREDENTIAL) });
  return out;
}

const TURN_TTL_S = 86400;                  // Cloudflare TURN credentials last a day ...
const TURN_REFRESH_MS = 6 * 3600 * 1000;   // ... and are renewed every six hours, so a player always gets at least eighteen hours

/** Close codes a Worker may send: 1000 or 3000-4999. The hub's other codes are mapped into 4xxx. */
export function closeCode(code) {
  if (code === 1000 || (code >= 3000 && code <= 4999)) return code;
  return 4000 + (code % 1000);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/ws") {
      if (request.headers.get("Upgrade") !== "websocket") return new Response("This is a WebSocket endpoint.\n", { status: 426 });
      return env.HUB.get(env.HUB.idFromName("main")).fetch(request);
    }
    if (url.pathname === "/health") return new Response("ok\n");
    if (url.pathname === "/stats" && env.STATS_TOKEN && url.searchParams.get("token") === env.STATS_TOKEN) {
      return env.HUB.get(env.HUB.idFromName("main")).fetch(request);
    }
    return new Response("EqVoice signalling. Connect a WebSocket to /ws.\n", { status: 200 });
  },
};

export class HubDO extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.hub = new Hub({
      limits: limitsFromEnv(env), iceServers: iceFromEnv(env), joinCode: env.JOIN_CODE ? String(env.JOIN_CODE) : null,
      clock: () => Date.now() / 1000, trace: traceFromEnv(env) || eventLogFromEnv(env),
    });
    this.conns = new Map();                 // WebSocket -> connection object the hub knows
    this.lastTick = 0;
    this.logStats = !!env.LOG_EVENTS && env.LOG_EVENTS !== "0";
    this.lastStatsLog = 0;
    // Woke from hibernation (or first start): put back everyone who is still connected.
    for (const ws of ctx.getWebSockets()) {
      let att = null;
      try { att = ws.deserializeAttachment(); } catch (e) { att = null; }
      if (att) this._adopt(ws, att);
    }
    console.log(`hub: object started; restored ${this.conns.size} connection(s), ${this.hub.bySid.size} session(s)`);
  }

  // --------------------------------------------------------------- plumbing

  _conn(ws, ip) {
    const conn = {
      ip,
      send(msg) { try { ws.send(JSON.stringify(msg)); } catch (e) { /* the socket is already gone */ } },
      close(code, reason) { try { ws.close(closeCode(code), String(reason || "").slice(0, 100)); } catch (e) { /* ditto */ } },
      saved: false,
    };
    return conn;
  }

  _adopt(ws, att) {
    const conn = this._conn(ws, att.ip || "?");
    this.hub.restore(conn, att);
    conn.saved = true;
    this.conns.set(ws, conn);
    return conn;
  }

  _persist(ws, conn) {
    const snap = this.hub.snapshot(conn);
    if (snap && snap.sid && !conn.saved) {
      try { ws.serializeAttachment(snap); conn.saved = true; } catch (e) { /* attachment is best effort */ }
    }
  }

  async _afterEvent() {
    const now = Date.now() / 1000;
    if (now - this.lastTick >= 1) {         // the Python server ticks every second; here, at most once a second, when something happens
      this.lastTick = now;
      this.hub.tick();
    }
    await this._schedule();
  }

  async _schedule() {
    const h = this.hub;
    let unregistered = false;
    for (const s of h.states.values()) if (s.sid === null) { unregistered = true; break; }
    const wait = unregistered ? TICK_UNREGISTERED_MS : h.links.size ? TICK_BUSY_MS : (h.states.size || h.reserved.size) ? TICK_IDLE_MS : 0;
    if (!wait) return;
    const target = Date.now() + wait;
    const current = await this.ctx.storage.getAlarm();
    if (current === null || current > target) await this.ctx.storage.setAlarm(target);
  }

  // ----------------------------------------------------------------- events

  /** Cloudflare's TURN service: short-lived credentials made from a TURN key (TURN_KEY_ID, secret TURN_KEY_API_TOKEN), added to the STUN list. */
  async _refreshIce() {
    const id = this.env.TURN_KEY_ID, token = this.env.TURN_KEY_API_TOKEN;
    if (!id || !token) return;
    const now = Date.now();
    if (this.turnAt && now - this.turnAt < TURN_REFRESH_MS) return;
    try {
      const r = await fetch(`https://rtc.live.cloudflare.com/v1/turn/keys/${id}/credentials/generate`, {
        method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify({ ttl: TURN_TTL_S }),
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const j = await r.json();
      const t = j && j.iceServers;
      if (!t || !t.urls || !t.username || !t.credential) throw new Error("no credentials in the answer");
      this.hub.iceServers = [...iceFromEnv(this.env), { urls: t.urls, username: t.username, credential: t.credential }];
      this.turnAt = now;
      console.log("hub: TURN credentials renewed");
    } catch (e) {
      console.log("hub: TURN credentials could not be made: " + (e && e.message));
    }
  }

  async fetch(request) {
    const url = new URL(request.url);
    if (url.pathname === "/stats") {
      return new Response(JSON.stringify(this.hub.stats()), { headers: { "content-type": "application/json" } });
    }
    await this._refreshIce();
    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    this.ctx.acceptWebSocket(server);
    const ip = request.headers.get("CF-Connecting-IP") || "?";
    const conn = this._conn(server, ip);
    this.conns.set(server, conn);
    if (!this.hub.connect(conn)) {
      this.conns.delete(server);
      conn.close(1013, "too many connections");
    } else {
      try { server.serializeAttachment({ ip, sid: null }); } catch (e) { /* best effort */ }
      await this._schedule();
    }
    return new Response(null, { status: 101, webSocket: client });
  }

  async webSocketMessage(ws, message) {
    let conn = this.conns.get(ws);
    if (!conn) {                            // a socket we have no record of: adopt it from its attachment, else drop it
      let att = null;
      try { att = ws.deserializeAttachment(); } catch (e) { att = null; }
      conn = att ? this._adopt(ws, att) : null;
      if (!conn) { try { ws.close(4000, "unknown connection"); } catch (e) { /* gone */ } return; }
    }
    await this.hub.handle(conn, message);
    this._persist(ws, conn);
    await this._afterEvent();
  }

  async webSocketClose(ws, code, reason, wasClean) {
    this._forget(ws);
    try { ws.close(code === 1005 || code === 1006 || code === 1015 ? 1000 : closeCode(code), ""); } catch (e) { /* already closed */ }
    await this._afterEvent();
  }

  async webSocketError(ws, error) {
    this._forget(ws);
    await this._afterEvent();
  }

  _forget(ws) {
    const conn = this.conns.get(ws);
    if (conn) {
      this.hub.disconnect(conn);
      this.conns.delete(ws);
    }
  }

  async alarm() {
    this.lastTick = Date.now() / 1000;
    this.hub.tick();
    if (this.logStats && this.lastTick - this.lastStatsLog >= 300) {      // a snapshot every five minutes while anyone is connected
      this.lastStatsLog = this.lastTick;
      console.log(JSON.stringify({ ev: "stats", ...this.hub.stats() }));
    }
    await this._schedule();
  }
}
