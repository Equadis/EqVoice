# Deploying the EqVoice signalling hub to Cloudflare

**Status: prepared, not deployed.** Nothing in this repository logs in to Cloudflare or publishes anything. Every command below
that changes your account is marked **(changes your account)**; run those yourself, when you decide to.

The hub is one Worker plus one SQLite-backed Durable Object (`HubDO`). No voice passes through it: a few small JSON messages per
player (see `docs/02-protocol.md`). Local development is unchanged: `wrangler.toml`, `npm run dev`, and the Python hub still work.
Production uses its own file, `wrangler.production.toml`, with its own Worker name (`eqvoice-signalling-prod`).

## 0. Before you start

* A Cloudflare account (the Workers **Free** plan is enough to begin; see section 7).
* For a custom domain: the domain added to Cloudflare as a zone. A `*.workers.dev` address is switched **off** in the production
  file on purpose (one public front door, on your domain, behind your rules).
* Node and Wrangler. On this machine: `source D:\Octowow\.eco-work\node-env.sh`, then everything below runs from
  `D:\EqVoice\eqvoice-signalling-cf`.

## 1. Check the build, with no account and no network

```
npx wrangler deploy -c wrangler.production.toml --dry-run --outdir ../dist/worker-build
```

This bundles the Worker exactly as it would be uploaded and prints the bindings it would have. `tests/test_cf_production.py` runs
it for you and also checks the file for mistakes (a secret written in plain text, a missing migration, `workers.dev` left on).

## 2. Settings

Edit `wrangler.production.toml`:

| Setting | What it does | Default |
|---|---|---|
| `routes = [{ pattern = "voice.example.org", custom_domain = true }]` | your domain; Cloudflare creates the DNS record and certificate | commented out: **required** |
| `STUN` | STUN servers given to clients (comma separated) | Cloudflare + Google |
| `LOG_EVENTS` | one JSON log line per registration, departure, link and refusal, no names | `1` |
| `LINK_RADIUS` | the farthest apart two players may be linked, in yards | `90` (at least the clients' `peak_radius` + leave margin) |
| `MAX_CONNS_PER_IP`, `REGISTERS_PER_MINUTE` | per-address caps | `20`, `12` |
| `LEASE_SECONDS` | how long a dropped session's name stays reserved for its owner | `30` |
| `DISTANCE_TOLERANCE` | yards by which the two players' distance figures may disagree | `12` |
| `MAX_CONNECTIONS` | cap on all connections together | `10000` |

Secrets (never in the file). **(changes your account)**:

```
npx wrangler secret put JOIN_CODE   -c wrangler.production.toml     optional: a shared code every client must present
npx wrangler secret put STATS_TOKEN -c wrangler.production.toml     optional: enables https://<domain>/stats?token=...
```

A join code keeps strangers' clients out of your community's roster. Without one, anybody who knows the address can register
(the per-address and total caps still apply).

## 3. Deploy **(changes your account)**

```
npx wrangler login
npx wrangler deploy -c wrangler.production.toml
```

The first deploy creates the Durable Object namespace (migration `v1`). Never rename `HubDO` afterwards; a later change to the
object's storage needs a new `[[migrations]]` entry with a new tag, and migrations cannot be rolled back.

## 4. Verify

```
curl https://voice.example.org/health                        -> ok
python ../tools/hub_smoke.py wss://voice.example.org/ws      -> two simulated players register, link and talk
python -m eqvoice_client --netcheck --signalling wss://voice.example.org/ws       (from any player's PC)
npx wrangler tail -c wrangler.production.toml --format pretty     live log lines
```

In the dashboard (Workers & Pages > eqvoice-signalling-prod > Observability) you see request counts, errors, CPU time and the
log lines. `/stats` (with `STATS_TOKEN`) gives the hub's own counts: connections, sessions, links, refusals.

## 5. Point the clients at it

Players set `signalling_url` in their `config.json` (or `--signalling wss://voice.example.org/ws`) and `join_code` if you set one. For a
test group, `python tools/make_test_pack.py --hub-url wss://voice.example.org/ws --join-code CODE` builds the zip.

## 6. Roll back

```
npx wrangler deployments list -c wrangler.production.toml           the last ten deployments
npx wrangler versions list    -c wrangler.production.toml           the last ten versions, with ids
npx wrangler rollback <version-id> -c wrangler.production.toml      **(changes your account)** make an earlier version live again
```

A rollback swaps the code; the Durable Object's data and the secrets stay. Connected players are dropped and reconnect on their own
within seconds (the clients retry with back-off and keep their session id). To take the service offline at once, remove the route in
the dashboard or run `npx wrangler delete -c wrangler.production.toml` **(changes your account; deletes the Worker and its object)**.

## 7. What it costs (estimate; check the dashboard after a week)

`python tools/cost_estimate.py` prints the table. With 2.5 online hours per player per day and someone in range 30% of the time:

* The Free plan (100,000 Durable Object requests and 13,000 GB-s a day) holds about **970 daily players**; requests run out first, and the
  hub's duration use is about 85% of the free allowance whenever somebody is always connected (one object, 128 MB, awake all day).
* The Paid plan is **$5 a month** up to roughly 1,000 daily players and about $14 a month at 20,000.
* Where the requests come from at 1,000 players: client pings (44%), proximity keepalives (51%), connections and signalling (5%).
  If you ever need to cut them: ping every 20 s instead of 10 (the idle timeout is 30 s), or answer pings with
  `setWebSocketAutoResponse` (loses the round-trip display).

## 8. Abuse and rate limits

| Layer | What it does | Where |
|---|---|---|
| Message size and rate | 16 KB per message; 100-message burst then 50/s; proximity 4/s; five strikes in a minute close the socket | `hub.js` (`Limits`) |
| Per address | 20 connections; 12 registrations a minute (uses `CF-Connecting-IP`, the real client address) | `MAX_CONNS_PER_IP`, `REGISTERS_PER_MINUTE` |
| Whole hub | at most 10,000 connections; 5,000 sessions per realm | `MAX_CONNECTIONS`, built in |
| Identity | a name belongs to the key that first registered it (Ed25519 signature over a fresh nonce); takeover needs that key | protocol |
| Relays | `offer/answer/ice` only between linked players, 120 a minute per link | protocol |
| Gate | optional `JOIN_CODE` | secret |
| At Cloudflare's edge (set in the dashboard, not in code) | a **Rate limiting rule** on `/ws` (for example 30 requests per 10 s per address) and **Bot Fight Mode** or a firewall rule for unwanted countries or ASNs. Which options exist depends on your plan | dashboard > Security > WAF |
| Kill switch | change `JOIN_CODE` (everyone new is refused at once), or delete the route | dashboard / `wrangler secret put` |

Logs never contain character names or addresses (`LOG_EVENTS`); `TRACE` does and must stay off in production.

## 9. Rehearsal that does not touch your account

`wrangler dev -c wrangler.production.toml --local` runs the production settings (minus the custom domain) on your PC: the same
Worker and Durable Object on the real runtime, which is how the test suite exercises it.


## TURN: for players whose routers allow no direct path

Two strict NATs cannot reach each other directly; their voice then needs a relay. The hub hands every client the relay's address with its other ICE servers.

* **Cloudflare's own TURN service** (what the test build uses): in the dashboard, Realtime > TURN Server > Create. Put the key's id in `wrangler.production.toml` as `TURN_KEY_ID`
  and its token in a secret: `wrangler secret put TURN_KEY_API_TOKEN -c wrangler.production.toml`. The Worker makes day-long credentials from them and renews them every six hours;
  no credential is ever in the repository.
* **A fixed relay** (coturn, or a paid service): the variables `TURN_URLS` (comma separated), `TURN_USERNAME` and `TURN_CREDENTIAL`.

Check it: connect to `/ws` and read the `ice` list in the first message; a `turn:` entry with a username and credential means clients get the relay. Audio through the relay is still end to end encrypted.
