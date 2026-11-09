// Just enough of the Cloudflare Workers runtime to run src/worker.js in a browser: the DurableObject base class,
// WebSocketPair, the Durable Object state (ctx), and a Response that accepts status 101.
//
// This mock encodes my reading of the documented API, not workerd itself: it can catch mistakes in the
// adapter's own logic (hibernation rebuild, alarms, close handling) but cannot prove the real runtime agrees.
// Run `npx wrangler dev` for that.

export class DurableObject {
  constructor(ctx, env) {
    this.ctx = ctx;
    this.env = env;
  }
}

export class MockWebSocket {
  constructor(label) {
    this.label = label;
    this.sent = [];
    this.raw = [];
    this.closed = null;
    this.attachment = null;
  }
  send(data) {
    if (this.closed) throw new TypeError("Can't call WebSocket send() after close().");
    this.raw.push(data);
    this.sent.push(typeof data === "string" ? JSON.parse(data) : data);
  }
  close(code, reason) {
    if (!(code === undefined || code === 1000 || (code >= 3000 && code <= 4999))) {
      throw new TypeError("Invalid WebSocket close code: " + code + ".");      // what workerd enforces for server sockets
    }
    if (this.closed) return;
    this.closed = [code, reason];
  }
  serializeAttachment(value) {
    const copy = structuredClone(value);
    if (JSON.stringify(copy).length > 16384) throw new RangeError("attachment too large");
    this.attachment = copy;
  }
  deserializeAttachment() {
    return this.attachment === null ? null : structuredClone(this.attachment);
  }
}

export class MockWebSocketPair {
  constructor() {
    this[0] = new MockWebSocket("client");
    this[1] = new MockWebSocket("server");
  }
}

export class MockCtx {
  constructor() {
    this.sockets = [];
    this.alarmAt = null;
    const self = this;
    this.storage = {
      async getAlarm() { return self.alarmAt; },
      async setAlarm(t) { self.alarmAt = t; },
      async deleteAlarm() { self.alarmAt = null; },
    };
  }
  acceptWebSocket(ws, tags) {
    this.sockets.push(ws);
  }
  getWebSockets() {
    return this.sockets.filter((s) => !s.closed);
  }
}

export class MockResponse {
  constructor(body, init = {}) {
    this.body = body;
    this.status = init.status ?? 200;
    this.headers = new Headers(init.headers || {});
    this.webSocket = init.webSocket || null;
  }
  async text() { return String(this.body); }
}
