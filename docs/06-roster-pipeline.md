# 06 — The roster pipeline (how strangers find each other)

**Bottom line.** EqVoice now works between players who share nothing but a realm: no group, guild, friend
list, room or nameplates. The hub keeps a roster of who is online; each desktop client hands the roster's names
to the addon; the addon looks those names up in the game and measures the exact distance; the hub joins two
players only when **both** report each other, at distances that agree; WebRTC then carries the voice directly
between them and the distance sets the volume. Everything outside the real game has been run and passes
(addon 440 checks, hub 106, client 211, including two real Chromium engines exchanging audio). **The pipeline has
not yet been run in WoW**; section 12 is the short path that does it.

```
 WoW client (+ ClassicAPI)                   Player's PC                                 Hub (any host)
+--------------------------+      +------------------------------------+          +--------------------+
| addon                    |      | eqvoice_client                   |  wss     | roster (realm ->   |
|  reads roster file  <----+------+-- InboxWriter  <-- Roster <--------+----------+--  name, sid)      |
|  UnitTokenFromName       | file |                          ^         |          |                    |
|  UnitDistanceSquared     |      |  BridgeMonitor --> readings        |          | mutual proximity:  |
|  writes frame BF2   -----+------+->  (name -> yards)  --> PeerManager|          |  A reports B,      |
+--------------------------+      |       |                   |        |          |  B reports A,      |
                                  |       +-- proximity ------+--------+----------+->  distances agree |
                                  |                           v        |          |  -> peer_available |
                                  |              media page (WebRTC, Opus)        +--------------------+
                                  +----------------------------+-------+
                                                               | SRTP, peer to peer
                                                               v
                                                      the other player's page
```

## 1. The flow, one step at a time

| # | Where | What happens |
|---|---|---|
| 1 | game → client | the addon's frame says *who is logged in* (character, realm, a random login id) |
| 2 | client → hub | `register` with that character and realm, a fresh random session id, a signature; the hub answers with the roster of the realm |
| 3 | client → game | the roster's names go into `CustomData\eqvoice-in.txt` (rewritten at least every 4 s, atomically) |
| 4 | game | the addon looks each name up (`UnitTokenFromName`, `UnitDistanceSquared`), a bounded number per scan, and lists the ones it can see with their yards |
| 5 | game → client | frame BF2 carries the results (file `eqvoice.txt`, plus a truncated optical copy) |
| 6 | client → hub | `proximity`: the names the game currently sees and how far (no coordinates) |
| 7 | hub → both | `peer_available` once both reported each other within tolerance; `peer_out_of_range` when that stops (3 s grace) |
| 8 | client | within 85 yards (the furthest a shout carries) for 0.8 s **and** the hub's confirmation → WebRTC (lower session id offers) |
| 9 | client + page | volume follows the game's distance and how loud the speaker is being (doc 11); leaving range fades, then closes the connection later |

Knowing a name is never enough: step 7 is the only door to signalling.

## 2. Who knows what

| | knows | does not know |
|---|---|---|
| Addon | its own character, the roster names it was given, yards to those it can see | the hub, session ids, anyone's address |
| Client | everything above plus the hub's `[name, sid]` roster | other players' positions, only distances |
| Hub | per online session: character, realm, session id, public key, token; who reports whom, and the distances | accounts, e-mail, IP addresses in any message, positions, history (nothing is written to disk) |
| Other players | your character name (they would see it in game anyway), your IP once connected (WebRTC), your voice | |

## 3. Files between the game and the client

Both files live in the game folder's `CustomData\` (written/read with the ClassicAPI/Nampower file functions;
`Imports\` is the fallback the client also looks at). Neither side relies on the other being present: each
treats silence as "gone" and fails closed.

### 3.1 Roster file, client → game: `eqvoice-in.txt` (`IN1`)

```
OVI1 <seq> <unix-ts> <crc> <character>
C radius=40 interval=0.5 report=60 checks=80
R Bobby,Carol,Dina                      up to 25 names per line, as many lines as needed
T free text                             optional debug message the addon logs
```

* `crc`: CRC-16/CCITT-FALSE of everything after the first newline, four upper-case hex digits (CR stripped).
* `seq` changes on every write; the client rewrites at least every 4 s even if nothing changed. The addon
  treats **15 s without a new seq as "the client is gone"** and reports nobody.
* `character` is the character the client believes is playing. **The addon ignores the file if it names anyone
  else** (identity binding): a roster written for a previous character never leaks into a new login.
* Written with a temp file and an atomic rename (retries if the game has the target open). A torn or damaged
  file fails the CRC and the previous roster stays in force.
* Names: 2 to 24 bytes, no control characters or spaces, at most 2000 per file. Above that the client sends all
  names near or recently near you plus a rotating window of the rest.
* Python and Lua parsers are tested against each other (`tests/test_inbox.py` runs the real Lua `Inbox.Parse`).

### 3.2 Frame, game → client: `eqvoice.txt` (`BF2`, line `OVB2 <hex>`)

```
byte 0       version = 2
bytes 1-2    counter (u16), +1 per publish
bytes 3-4    payload length
payload      flags(1) loginId(u32) wall(u32) rosterAck(u16) rosterSize(u16) checked(u8) loaded(u8) misses(u8)
             intervalMs(u16) costTenthsMs(u16) character(len,bytes<=24) realm(len,bytes<=32)
             count(u8) then count x [name(len,bytes<=24) distance(u8, half yards, 254 = 127+) flags(u8)]
last 2       CRC-16/CCITT-FALSE
```

Flags: 1 enabled, 2 file bridge, 4 optical bridge, 8 roster fresh, 16 logging out, 32 lookups available,
64 roster was written for another character, 128 results truncated.
`protocol/bridge_vectors.json` is generated from the Lua encoder; the Python codec reproduces every vector
byte for byte and cell for cell. The optical copy (32 x 10 colour cells, 234 bytes at most) keeps the nearest
results when the list does not fit; the file copy holds up to 200.

## 4. Signalling protocol v2 (hub ↔ client)

JSON text frames, `v: 2`. The Python hub (`eqvoice_signalling/hub.py`) is the reference; the Cloudflare port
(`eqvoice-signalling-cf/src/hub.js`) must reproduce `protocol/conformance.json` message for message.

| Direction | `t` | Fields |
|---|---|---|
| hub → client | `hello` | `v`, `nonce`, `now`, `join_code` (bool), `ice`, `limits` |
| client → hub | `register` | `v:2`, `sid` (22 base64url, 128 random bits), `realm`, `character`, `pub` (Ed25519), `sig` over `"OV2-REG\0" nonce \0 sid \0 realm \0 character`, `code?`, `token?` |
| hub → client | `registered` | `sid`, `token`, `lease`, `now`, `roster` (`[[name, sid], …]`, ≤300), `more` |
| hub → client | `roster_update` | `add: [[name, sid]]` and/or `remove: [sid]` (sent to the realm only) |
| client → hub | `proximity` | `peers: [[name, yards, ts], …]`, ≤64, the **complete** current set (replaces the last) |
| hub → client | `peer_available` / `peer_out_of_range` | `sid`, `character`, `distance` (shared, 0.1 yd) |
| client ↔ hub ↔ client | `offer` `answer` `ice` `bye` | to: `{to, ep, data}`; relayed as `{from, ep, data}` **only between linked sessions** |
| both | `ping` / `pong` | `ts` echoed |
| client → hub | `unregister` | leaves at once, frees the name |
| hub → client | `error` | `code`, `msg`, `ref?` |

**Mutual proximity rule.** A link exists while A reports B, B reports A, both reports are fresh (6 s), the two
distances differ by at most 12 yards and the nearer is within 45 yards. It survives 3 s without that
(`leave_grace`). The 12 yards are not a precision claim: two clients sample the same pair at different
instants and movers cover up to ~14 yards a second.

**Names and realms.** A name is 2–12 Unicode letters. A realm is matched case-insensitively with spaces folded;
sessions of different realms never see each other. One live session holds a name; a different key gets
`name_in_use`; the same key reconnecting takes over (`4409` to the old socket). A dropped session's name stays
reserved for its own key and token for 30 s; a clean `unregister` frees it at once.

**Limits (defaults).** 16 KB per message; 100-message burst and 50/s; proximity 8 burst, 4/s; 120 signals a
minute per link; 20 connections and 12 registrations a minute per address; 10 s to register; 30 s idle; 5000
sessions per realm; roster sent in chunks of 300.

Roster privacy rules (all enforced in `hub.py` and tested): the roster carries only `[name, sid]`; never an
account, e-mail, Discord id or address; only currently registered sessions; removed on disconnect, idle timeout,
logout, character change; no persistent store, no search.

## 5. The client's state machine

Per player the game can see (`peers.py`; every transition is logged and shown in the UI):

```
 (UNKNOWN) -- the game sees them ----------------------------------------> SEEN
 SEEN --inside 85 yd------------------------------------------------------> NEARBY_PENDING
 NEARBY_PENDING --0.8 s inside AND hub link AND I am the lower session id--> CONNECTING
 NEARBY_PENDING --valid offer from the lower id----------------------------> CONNECTING
 CONNECTING --media connected----------------------------------------------> CONNECTED       volume follows distance
 CONNECTED --outside 88 yd / unseen for 1.5 s, or the hub withdrew the link-> OUT_OF_RANGE_GRACE  fade out, stop sending
 OUT_OF_RANGE_GRACE --back inside 85 yd and linked-------------------------> CONNECTED       same connection, no renegotiation
 OUT_OF_RANGE_GRACE --8 s-------------------------------------------------> DISCONNECTED     close, say bye
 DISCONNECTED --cooldown over---------------------------------------------> SEEN / NEARBY_PENDING
```

| Rule | Value (config `proximity.*`) |
|---|---|
| connect radius / leave radius | 85 yd / 88 yd (`peak_radius`, `leave_hysteresis`): a call is kept for as long as a shout could be heard. An ordinary voice still ends at 41 yd (`say_radius`, the measured `/say` reach, docs/08) by volume alone. Jitter at the edge does not flap |
| debounce before connecting | 0.8 s (`debounce_s`) |
| leave grace before fading | 1.5 s (`leave_grace_s`): **one missed scan changes nothing**, volume is held |
| fade out | 500 ms; the page also stops sending your microphone to that peer |
| delay before closing the peer connection | 8 s (`teardown_s`); never closed for a single missed scan |
| signalling lost | existing calls continue 8 s (`fail_closed_s`), then fade |
| retry after a failure | 3, 6, 12, 24, 48 s; a refused offer counts as a failure |
| simultaneous connections | 8 (`max_peers`) |

**Volume curve** (`peers.voice_gain`; the page applies the same curve itself, `ui/voicemodel.js` `curveGain`, and a test checks the two against
each other): the **boost** (`audio.boost`, normal = 2 times at 0 yd), easing away smoothly (a half cosine, in decibels, no flat stretch) to nothing at 10 yd, then the voice's own loudness (100%) out to 30 yd
(`full_radius`), then `(1 - t) ^ 1.6` with `t = (d - 30) / (85 - 30)`, 0 at 85 yd (`peak_radius`). A raised voice keeps 100% out to 45 yd
(`loud_full_radius`) and ends in the same place. See doc 11 for the reasons and the settings.

| yards | 0 | 2 | 3 | 5 | 8 | 10 | 30 | 40 | 50 | 60 | 70 | 80 | 85 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| an ordinary voice | 200% | 187% | 173% | 141% | 107% | 100% | 100% | 73% | 49% | 28% | 13% | 2% | 0 |
| a shout (full volume to 45) | 200% | 187% | 173% | 141% | 107% | 100% | 100% | 100% | 81% | 47% | 21% | 4% | 0 |

Updates go to the page at most 4 times a second, when the volume has moved 3 points or the player a quarter of a yard. The page does not jump to a new
reading: it follows the distance (0.3 s) and the volume (0.08 s) smoothly, so the volume changes continuously as you walk.

## 6. Fail closed

"Closed" means: nobody is audible, nothing is transmitted to them, and the game is told to look nobody up if
the hub cannot vouch for them.

| Condition | Result | Within |
|---|---|---|
| bridge frames stop | readings held while `stale` (≥4 s), then nobody (`lost`) | 8 s |
| game gone for 30 s (`ghost_s`) | the client unregisters: the character leaves the roster | 30 s |
| logout flag in a frame | unregister at once, saved key deleted | next frame |
| character or login id changes | old session ends, new identity, new session id and key | next frame |
| addon switched off / no lookups / roster file for another character | frame flags say so; readings ignored | next frame |
| roster file stale (client died) | addon reports nobody | 15 s |
| hub unreachable | calls continue briefly, then fade; roster file emptied so the game stops looking people up | 8 s |
| only one side reports (or the distances disagree) | the hub never links; an existing link drops | 3 s |
| client quits | roster file deleted, `unregister` sent | at once |

## 7. Identity

The character, realm and a random login id come from the game, never from the user. The client derives a
session identity from them: a random 128-bit session id, an Ed25519 key, and the token the hub issues. It is
saved only so a restart of the **same** login can resume; another character, realm or login id gets all-new
values, and a clean logout deletes the file. Nothing links one play session to the next.

## 8. How hard is it to impersonate someone? (honest answer)

Without a check against the OctoWoW server, **nothing can prove that a client really is the character it
names**. What the design adds up to:

* a stranger must register the name first (the hub allows one live holder per name per realm; the real owner's
  later registration is refused with `name_in_use` until they leave or time out);
* a link needs the *other* side to see a character of that name within range, with a distance that agrees, so
  an impostor can only be linked to players who actually have someone of that name next to them;
* the addon ignores a roster file written for another character, so one PC cannot feed another's game;
* 128-bit session ids, per-connection nonces, signed registration, per-address and per-session rate limits,
  strict message validation, same error for "offline" and "not nearby".

What remains: **name squatting** (someone online with your name stops you registering until they leave: a
denial of service, mitigated by the optional join code), and an attacker physically next to a victim can claim
the name of a *different* character the victim also sees (limited to people already in earshot). A later
hardening, not built, is a short proof over the WebRTC data channel (derive the session id from the public key,
and have peers verify it) which would also close the "malicious hub swaps the SDP" gap.

## 9. Scale and cost

* **Game side**: round-robin over the roster, `checks` (default 80) names per scan every 0.5 s; players
  recently seen are rechecked every scan; names that keep failing back off to 0/1/2/4 s; results live 1.3 s.
  A 2000-name roster is covered in about 12 s; 400 names in about 2.5 s. **The real frame cost is not measured
  yet**: `/ev diag lookupcost 80` in game benchmarks it (the stub reports microseconds, which means nothing).
* **Metrics** (UI "Pipeline" panel, `/api/diag`, and an INFO log line every 30 s): roster size, names checked
  per scan, seen, not loaded, scan duration in ms, addon interval, measured frame interval, roster
  acknowledged by the game, names reported, players nearby and connected, proximity messages sent.
* **Client side**: a tick with a 400-name roster takes 0.12 ms (measured in `test_a_crowded_realm_is_handled_with_a_bounded_budget`, which also asserts under 50 ms).
* **Hub**: 300 simulated players on one realm: 74 MB, 4.5% of one core, 5 MB of roster traffic for the whole
  join storm (17 kB per client); signal latency median 1.3 ms. See `04-deployment-and-cost.md`.

## 10. Servers

| | Python (`eqvoice-signalling`) | Cloudflare (`eqvoice-signalling-cf`) |
|---|---|---|
| Hosts on | any machine, tunnel or VPS | Workers Free plan, one Durable Object |
| Tested | 72 hub tests, 5 socket tests, 25 conformance scenarios (355 steps), load test | the same 25 conversations replayed by `hub.js` in headless Chromium; the adapter against a mock runtime **and on the real Workers runtime locally (`npx wrangler dev`): 8 runtime tests, the client pipeline through it, a real hibernation survived**. Not deployed |
| Protocol | reference | identical (conformance file) |

## 11. Simulation (no WoW needed)

`python tools/run_sim.py --fake-media` starts a hub and two clients, each with a **simulated game** that reads the
real roster file and writes the real frame file, over a shared world file. `--demo` walks Bobby in and out of range.
`python tools/sim_world.py <world.json> pair Alice Bobby 18 19` sets what each game sees (Alice sees Bobby at 18
yards, Bobby sees Alice at 19: voice connects); `see Alice Bobby 60` makes the sides disagree: the link ends.
In code: `tests/scene.py` builds the same thing in-process; `tests/test_app.py` is the scenario list.

## 12. The very short real-WoW test path

```
python tools\realtest.py doctor
python tools\realtest.py run                       (add --test-roster SomeNearbyPlayer for a one-player check)
```

`doctor` needs no game: it checks that the addon in the game folder is identical to the repository's, Node and Wrangler,
Edge, ports. `run` starts the local Cloudflare hub (Wrangler, tracing on), waits for the addon's
`CustomData\eqvoice-<Character>.txt`, starts one desktop client per character it finds (push-to-talk **F9**, **F10**;
wear headphones), and prints a live checklist of the 20 boundaries: hub, clients, frames, identity from WoW, registration,
roster, **inbound file to the addon**, **exact-name lookup**, **distance back through the outbound file**, desktop read,
both report nearby, hub link, WebRTC, audio both ways, walk apart, distance climbs, volume falls, leaves range, audio
stops, connection torn down. Ctrl+C prints the final checklist and saves it. The full runbook, the stages, and what each
failure means are in `03-onwow-test-plan.md`.

`run` first stops any browser left from an earlier run (found by its profile folder under `...\realtest\`), gives each client
a random window token (printed with the window address), and ends by stopping the media engines by profile as well. This is
because a leftover headless test browser once reattached to a real client (fixed port, fixed token), became its voice engine
and played Chromium's fake-microphone beep instead of a voice. The client now also says what its engine is: `media_engine`,
`mic ... fake=`, `audio_devices`, `media_warning`, a **Test speakers** button, and playback facts in `audio_rx`.

Every component narrates its boundary as `TRACE` lines (the addon's travel to the desktop log inside the bridge file, so
all three appear in one place); `tools/pipeline_report.py` reads them. A full rehearsal with simulated games passes all 20
steps (`eqvoice-client/tests/test_rehearsal.py`), and again against the real Wrangler hub
(`tools/realtest.py run --headless` with `tools/sim_games.py`).

## 13. What is verified, and what is not

**Verified in the real game (2026-10-16 and 2026-10-17, two characters on N'Zoth, one PC, Cloudflare Worker under local Wrangler):**
the game reads `eqvoice-in-<Character>.txt` and acknowledges it within about a second; exact-name lookups report real
distances at 8 to 60 yards with no group, guild or nameplates; both clients register, receive each other's name, report
each other, and the hub links them; WebRTC connects; push-to-talk voice (real microphone, real speakers) is heard clearly in
both directions; the volume falls with distance (1.0 at 8 yd, 0.5 at about 17, 0 at 38-40); the peer leaves range past
43 yd, audio stops, and the connection is torn down 8 s later. The checklist is `tools/pipeline_report.py`; the saved runs
are under `D:\Octowow\.eco-work\realtest\`.

**Not verified:**

* lookup cost at roster size 80+ (`/ev diag lookupcost 80`), the optical copy, and the real `/say` range for other servers (the 41-yard
  default is an assumption); only two players have ever been in the game together;
* the Cloudflare Worker deployed on Cloudflare's edge (it has run on the real runtime locally, not on the network);
* WebRTC across the internet (every real test so far had both players on one PC; STUN is configured but not exercised, TURN
  is not built);
* the 15 s roster TTL and 4 s refresh interact with the game's file-write behaviour in ways only the game shows.

