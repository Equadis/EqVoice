# EqVoice signalling on Cloudflare (Worker + one Durable Object)

The same signalling server as `eqvoice-signalling` (roster, mutual proximity, WebRTC introductions), hosted
on Cloudflare's free plan instead of a machine you keep running. **No voice passes through it**: players'
audio goes peer to peer. The server sees a few small JSON messages per player.

| | |
|---|---|
| `src/hub.js` | the protocol, a port of `eqvoice-signalling/eqvoice_signalling/hub.py` (pure logic, no platform APIs) |
| `src/worker.js` | the Worker (routes `/ws`) and the Durable Object (`HubDO`, WebSocket Hibernation API) |
| `wrangler.toml` | development configuration: one SQLite-backed Durable Object, a few optional variables |
| `wrangler.production.toml`, `DEPLOY.md` | **prepared, not deployed**: production configuration (own Worker name, no workers.dev, observability, written-out limits), the step-by-step guide, rollback, free-tier estimate, abuse limits |
| `tests/` | conformance runner, adapter test against a mock Workers runtime (both run by the Python test suite); the opt-in tests against the real runtime live in `../eqvoice-signalling/tests/test_cf_runtime.py` |

## Status: what has and has not been run

* **`hub.js`**: replays 26 recorded conversations (`../protocol/conformance.json`, 366 steps) message for message
  identically to the Python hub, in headless Chromium and under Node 24. A deliberate one-line change to a limit
  makes it fail, so the check is real.
* **`worker.js` against a mock** of the Workers runtime (`tests/mock_cloudflare.js`, in headless Chromium, part
  of the normal test run).
* **`worker.js` on the real Workers runtime (`workerd`), locally** (`npx wrangler dev`, Wrangler 4.151.0, workerd
  1.20261012.1, 15 Oct 2026): `../eqvoice-signalling/tests/test_cf_runtime.py` passes, 8 tests. Registration with
  WebCrypto Ed25519, roster, mutual-proximity link, offer relay, clean leave, takeover (`4409`), `bad_sig` /
  `bad_name`, an oversize message (`4009`), a flood (`4008`), the register-timeout alarm (`4408`), the per-address
  cap (`4013`), and **a real hibernation**: with two silent clients the object was evicted and restarted with
  "restored 2 connection(s), 2 session(s)"; the roster, a new registration, the link, the relay and the name hold
  all still worked. The client's own tests (`eqvoice-client/tests/test_external_hub.py`) and two real client
  processes with headless Edge media engines also ran through it: register, walk into range, WebRTC connected,
  walk away, closed.
* **Not run:** a deployment on Cloudflare's edge (nothing has been deployed), real billing, and `webSocketError`
  (never fired in these tests; the adapter handles it like a close).

Two things the real run taught: (1) with real clients (a ping every 10 s) the object does **not** hibernate while
anyone is connected, so budget for it being resident all day (still inside the free duration allowance, see
below); it hibernates when connections are silent. (2) npm 11 blocks install scripts by default and prints a
warning about `esbuild` and `workerd`; Wrangler worked without approving them.

Run it yourself:

```
cd eqvoice-signalling-cf
npm install
npx wrangler dev --local --port 8787                           # leave running
# another terminal, from the repository root:
python tools/hub_smoke.py ws://127.0.0.1:8787/ws               # about 2 s; add  --idle 25 --no-ping  to force hibernation
set EQVOICE_CF_URL=ws://127.0.0.1:8787/ws
python -m pytest eqvoice-signalling/tests/test_cf_runtime.py # 8 tests, about 40 s
python -m pytest eqvoice-client/tests/test_external_hub.py   # the client's pipeline through it, about 40 s
python tools/run_sim.py --hub ws://127.0.0.1:8787/ws --fake-media --demo   # two clients, simulated games, walked in and out of range
```

## Deploy (when you want it online)

```
cd eqvoice-signalling-cf
npm install
npx wrangler dev --local         # local test first (see above)
npx wrangler login               # opens your browser
npx wrangler deploy              # prints https://eqvoice-signalling.<you>.workers.dev
npx wrangler secret put JOIN_CODE   # optional: require a shared code from clients
```

Point the desktop clients at it: `"signalling_url": "wss://eqvoice-signalling.<you>.workers.dev/ws"` in the
client's `config.json` (or `--signalling`). Variables in `wrangler.toml`: `STUN`, `LEASE_SECONDS`,
`LINK_RADIUS`, `DISTANCE_TOLERANCE`, `MAX_CONNS_PER_IP`, `REGISTERS_PER_MINUTE`; secrets `JOIN_CODE` and
`STATS_TOKEN` (enables `/stats?token=...`, counts only).

## What it costs (Cloudflare pricing page, checked 15 Oct 2026)

Free plan: 100,000 requests/day, 13,000 GB-s/day of duration, SQLite-backed Durable Objects allowed.
Incoming WebSocket messages count 20:1 as requests; outgoing messages and auto-answered pings are free.

Per player (clients ping every 10 s and repeat a non-empty proximity report every 4 s):

| situation | incoming messages | requests |
|---|---|---|
| online, nobody nearby | ~360 / hour | ~18 / hour |
| standing near others | ~1,300 / hour | ~65 / hour |
| walking about near others (worst case) | ~3,600 / hour | ~180 / hour |

So the free request allowance covers roughly **550 player-hours a day at the worst case, over 5,000 when mostly
alone**. Duration: even an object that never hibernates uses 0.125 GB x 86,400 s = 10,800 GB-s a day, inside the
free 13,000. The paid plan ($5/month) raises both by orders of magnitude. Wrangler deploys never bill by
themselves: the free plan stops at its limits rather than charging.

## Hibernation, and what it does to state

The object is evicted from memory after a short idle spell while sockets stay connected. On the next message it
is rebuilt: `HubDO`'s constructor re-reads each connection's attachment (character, realm, session id, public
key, token, ~200 bytes) and restores the roster. Links and proximity reports are not stored; clients repeat their
reports every 4 s, which rebuilds them (they may see a second `peer_available`, which the client treats as a
no-op). Name reservations after a dropped connection live in memory only and are forgotten if the object sleeps
(a rare weakening of the 30 s "nobody else can take your name" rule).

## One object, one realm list

Everything runs in one Durable Object named `main`: up to 32,768 connections (Cloudflare's limit), far above
what a Vanilla-sized community needs. If you ever outgrow it, shard by realm: route `/ws?realm=X` to
`idFromName("realm:X")`. The protocol already keeps realms apart, so nothing else changes.

## Keeping it equal to the Python server

`../protocol/conformance.json` is recorded from the Python hub (`python tools/gen_conformance.py`). Change
the protocol in Python, regenerate, make `hub.js` pass, in that order. With Node installed,
`node tests/run_node.mjs` replays the recording without a browser.

Known, deliberate differences: `handle()` is asynchronous (WebCrypto verifies signatures asynchronously),
`restore()`/`snapshot()` exist for hibernation, and a JSON number such as `5.0` is an integer in JavaScript but a
float in Python (so `{"ep": 5.0}` is accepted here and refused there; no real client sends it).
