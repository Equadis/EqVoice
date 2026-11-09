# 04 — Deployment and cost

## What has to be hosted

Only the signalling hub: who is online (names and random session ids, per realm), who reports whom at what
distance, and the WebRTC introductions. It carries a few JSON messages per player per minute and **never any
audio**. Everything else runs on the players' PCs. What it stores: nothing on disk; in memory, per online
session, the character, realm, session id, public key and token (`06-roster-pipeline.md` section 2).

```
python -m eqvoice_signalling --host 0.0.0.0 --port 8787 --trust-proxy
```

| Flag | Meaning |
|---|---|
| `--trust-proxy` | read the player's address from `CF-Connecting-IP` / `X-Forwarded-For` (use behind a proxy, otherwise every client looks like the proxy and the per-address limits bite everyone at once) |
| `--join-code CODE` | optional shared secret clients must present: keeps casual strangers off the server |
| `--stun URL` (repeatable) | STUN/TURN servers offered to clients. Default: Google's and Cloudflare's public STUN |
| `--lease` (30 s) | how long a dropped session's character name stays reserved for its own key |
| `--link-radius` (90), `--distance-tolerance` (12) | yards: sessions farther apart than this are never linked / how far two reports of one pair may disagree |
| `--max-conns-per-ip`, `--registers-per-minute` | tuning |

Endpoints: `/ws` (clients), `/health` (`ok`), `/stats` (counts only, no ids). It needs TLS in front for
`wss://` (Caddy, nginx or Cloudflare); clients take any `ws://` or `wss://` URL in `signalling_url`.

Players set `signalling_url` (and `join_code`) in `%APPDATA%\EqVoice\config.json`, or pass
`--signalling wss://voice.example.org/ws`. Ship a pre-filled config in the client bundle for your community.

## Where to run it

| Option | Cost | Notes |
|---|---|---|
| A PC you already leave on (or a Raspberry Pi) behind a Cloudflare Tunnel | 0 | simplest for a small group; the tunnel provides TLS and hides the home address |
| A free-tier VM at a cloud provider | 0 while the provider keeps that tier | Oracle's Always Free tier has offered small VMs; check current terms before relying on any free tier |
| A small paid VPS | roughly 4-6 USD per month | the process is 70 MB; any 512 MB machine is plenty |
| **Cloudflare Workers + a Durable Object** | 0 within the free plan | `eqvoice-signalling-cf/`: written, replays the same recorded conversations as the Python hub, adapter tested against a mock runtime **and on the real Workers runtime locally (`npx wrangler dev`), including a real hibernation**; **not deployed** (below) |

### The Cloudflare route

`eqvoice-signalling-cf/` holds a JavaScript port of the hub (`hub.js`) and a Worker plus one Durable Object
(`worker.js`, WebSocket Hibernation API). `README.md` there has the deploy steps (`npx wrangler deploy`, run by
you, never by this repository's tooling) and what has and has not been verified.

Limits and prices, from Cloudflare's published pages (fetched 15 Oct 2026): the Workers Free plan includes
**100,000 requests/day** and **13,000 GB-s of duration/day**; Durable Objects are available on Free with the
SQLite backend only; **incoming WebSocket messages are billed 20 to 1** as requests; outgoing messages and
auto-answered pings are free; an object that is hibernating is not billed for duration.

Per player, the clients send a ping every 10 s and repeat a non-empty proximity report every 4 s:

| situation | incoming messages | requests |
|---|---|---|
| online, nobody nearby | about 360 / hour | about 18 / hour |
| standing near others | about 1,300 / hour | about 65 / hour |
| walking about near others (worst case) | about 3,600 / hour | about 180 / hour |

At the worst case the free allowance covers about **550 player-hours a day**; when players are mostly alone, over
5,000. Duration is not the limit: an object that never hibernates uses 0.125 GB x 86,400 s = 10,800 GB-s a day.
Estimates from the message rates in the clients, not measurements on Cloudflare.

With real clients (a ping every 10 s) the object stays resident while anyone is connected, so plan for it being awake all day (0.125 GB x 86,400 s = 10,800 GB-s, inside the free allowance); it hibernates when connections go quiet. Hibernation wipes the object's memory while sockets stay connected (verified on workerd: two silent clients, the object restarted "restoring 2 connection(s), 2 session(s)", everything kept working). The adapter keeps each session in its
socket's attachment (about 200 bytes) and rebuilds the roster from them; links and reports rebuild themselves
from the clients' next reports. The only thing lost is the 30 s reservation of a just-dropped name.

## What it costs to run (measured)

`python tools/loadtest.py --clients 300 --seconds 15`: the hub and 300 simulated players on **one realm**, on one
Windows PC. Every player registers (so every join is broadcast to all), then clusters of six report each other and
trade signalling messages twice a second:

| | |
|---|---|
| Hub memory | 72 MB with all 300 connected, 74 MB peak |
| Hub CPU | 0.70 s over 15.6 s, **4.5% of one core** |
| Join storm | 300 registrations in 1.2 s; 85,949 roster messages, 5.2 MB in total (17 kB per client) |
| Traffic after that | 840 messages/s in, 6,200/s out (each position report fans out to its cluster) |
| Links formed | 1,598 (five per player) |
| Signal delivery latency | median 1.3 ms, p99 12.9 ms |
| Errors | 4 `not_linked`, from signals sent as a link dropped, which is correct behaviour |

300 players each sending a signal every half second is far busier than reality. The roster is the one part that
grows with the square of the crowd during a login storm (every join goes to every other session of the realm):
at 1,000 simultaneous logins that is about a million small messages, which the 5000-session cap and the 300-entry
chunks keep bounded; a realm that large would be sharded (see the Cloudflare README).

## What it costs the players (bandwidth)

Voice is peer to peer, so each player uploads one stream per person they are connected to and downloads
the same number.

Measured in the browser test (Opus, 48 kHz mono, our SDP settings: 32 kbit/s average cap, DTX, in-band FEC),
RTP payload at the receiver, loopback:

| | Received at B (payload only) |
|---|---|
| Key up (microphone gated) | **77 bytes/s**, about 0.6 kbit/s: DTX and silence frames work |
| Key down, Chromium's fake microphone | 856 bytes/s, about 6.8 kbit/s |

The fake microphone is a short beep once a second, mostly silence, so the second row is *not* what speech
costs. Continuous speech runs at the encoder's cap, 32 kbit/s average, which is a configured value, not a
measurement from this test.

Add roughly 40-45% for RTP, SRTP, UDP and IP headers (about 50 packets per second). A rule of thumb for
planning: **about 50 kbit/s each way per active connection while someone is talking, close to nothing in
silence** (DTX sends occasional comfort-noise packets). With the default cap of 8 simultaneous
connections an always-open microphone costs a worst case of 7 x 50 = 350 kbit/s upload, which is why
push-to-talk is the default. Crowds beyond ~8 would need a mixing server, which is out of scope.

## TURN (optional, separate)

STUN lets most home connections connect directly. Some networks (symmetric NAT, corporate and some mobile
networks) need a relay. That relay is a TURN server, and it **does carry audio**, so its bandwidth bill is
real: about 50 kbit/s x 2 directions per relayed call.

TURN is kept strictly apart from the basic architecture:

* nothing in the MVP requires it; the default ICE list is STUN only
* to add one, put it in the server's `--stun` list or the client's `ice_servers` (with credentials, e.g.
  coturn's time-limited REST credentials); no code change
* `relay_only: true` makes the client use *only* relays, so peers never see each other's IP. That is the
  privacy mode, at the cost of every call going through the TURN host
* managed TURN services have free tiers of a few GB a month; check current terms. Self-hosting coturn on a
  VPS costs the VPS plus its bandwidth

## Packaging the client

```
cd eqvoice-client
python -m PyInstaller --noconfirm --onedir --name EqVoice --paths . ^
  --add-data "eqvoice_client/ui;eqvoice_client/ui" run_eqvoice.py
```

Built and run on the earlier (group-heartbeat) version of the client: 20 seconds, **39 MB** folder, `EqVoice.exe`,
two copies exchanged audio. **Not rebuilt since the roster pipeline**; rebuild and rerun `tools/run_sim.py` against
the bundle before shipping it. Players still need Edge or
Chrome, which every current Windows has. On Linux use `chromium` or Chrome; global push-to-talk is
Windows-only for now.

## Releases and updates

* addon: copy the `EqVoice` folder; version in the `.toc`; each file format carries its version in its first
  field (`OVI1`, `BF2`, signalling `v: 2`), so mismatched versions refuse each other instead of mis-parsing
* client: the bundle folder; `--help` lists everything; config and logs in `%APPDATA%\EqVoice`; it removes its
  roster file from the game folder when it quits
* hub: the Python package or the Worker; no state on disk, restart at will (clients reconnect and re-register
  within the 30 s lease, keeping their names)
