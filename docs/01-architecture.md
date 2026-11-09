# 01 — Architecture

Read `00-feasibility.md` for which parts of the brief are proven, doubtful or false, and `06-roster-pipeline.md`
for how the current design works end to end. Wire formats are in `02-protocol.md`, the in-game test plan in
`03-onwow-test-plan.md`, hosting and cost in `04-deployment-and-cost.md`, why strangers can be found at all in
`05-stranger-proximity.md`.

## 1. Architecture

Four programs, one rule: **the game only ever answers "how far is this named player"; everything about voice
happens on the PCs; the hub only introduces.**

```
  WoW client (1.12.1 + ClassicAPI)                              Player's PC                                  Hub
 +----------------------------------+  file    +------------------------------------------------+       +--------------+
 | EqVoice addon (Lua 5.0)        | <------- | eqvoice_client (Python 3.12)                 |       | presence     |
 |   Inbox   reads the roster file  |  roster  |   InboxWriter, Roster                          |       | roster       |
 |   Lookup  name -> token -> yards |          |   BridgeMonitor (live / stale / lost)          |  wss  | mutual       |
 |   Frame   state -> BF2 bytes     |  frame   |   PeerManager (state machine per player) -----+-------+-> proximity   |
 |   Bridge  file (+ optical copy)--+--------> |   SignallingClient   identity   PTT poller      |       | signalling   |
 +----------------------------------+          |   local web server 127.0.0.1                   |       +--------------+
                                               |        ^   | commands / events                 |
                                               |        |   v                                   |
                                               |   media page in Edge/Chrome (WebRTC, Opus, AEC)|
                                               +----------------------+-------------------------+
                                                                      | SRTP, peer to peer
                                                                      v
                                                             other players' media pages
```

| Part | Choice | Why |
|---|---|---|
| Addon | Lua 5.0, no libraries | the client's language; small enough to audit; every API probed, none assumed |
| Discovery | the hub's roster of online characters, looked up **by name** in the game | addon channels other than guild/party/raid are refused by this client, and nameplate enumeration returns nothing (see 05); name lookup with exact yards works to at least 86 yd |
| Bridge | file in `CustomData\` both ways; optical frame as a second path for the game-to-client direction | the only no-DLL real-time options (section 4) |
| Client logic | **Python 3.12 + aiohttp** | already on this machine and EqUpdater's language; one language for server and client; every decision is pure code with fast tests |
| Media engine | **Chromium (Edge or Chrome)** | the only readily available WebRTC stack with echo cancellation, noise suppression, AGC, Opus, jitter buffer and DTLS-SRTP |
| Global push-to-talk | `GetAsyncKeyState` polling via ctypes; the key is stored as a physical key and resolved through the keyboard layout | reads "is this key down", installs no hook, injects nothing |
| Hub | Python + aiohttp (`eqvoice-signalling`), or a Cloudflare Worker + Durable Object (`eqvoice-signalling-cf`) | same protocol, held together by recorded conversations; free-tier friendly |
| Identity | character and realm from the game, random session id, Ed25519 key per game login | proof of ownership of a session without accounts |

Rejected: **Electron**, **Tauri** (a sensible later slimming step), **aiortc / webrtc-rs** (no audio
processing), **a central SFU** (violates "no central voice hosting"), **per-zone chat channels** (privacy and
reliability, see 05).

The page-versus-Python split matters: the page (`ui/app.js`) only captures the mic, holds
`RTCPeerConnection`s, plays audio and reports. Python decides everything, so the page can be swapped for
another shell without touching the logic, and the logic is tested without a browser.

## 2. Data flow

`06-roster-pipeline.md` section 1 has the nine steps with numbers. In one breath: register as the character the
game reported → receive the realm roster → write its names for the addon → the addon looks them up and returns
yards → report those to the hub → the hub confirms only mutual, agreeing reports → connect over WebRTC → volume
follows yards → leaving range fades, then closes.

## 3. Threat and privacy model

Scale: a fan project among people who opted in, not a bank. The goal is "cheap to attack is not profitable", and
"what leaks is stated".

**Who sees what**

| Who | Learns |
|---|---|
| Nearby players you connect to | your character name (they see it in game anyway), that you run EqVoice, your public IP (WebRTC), your voice |
| The hub | for each online session: character, realm, a random session id, a public key, the connection's IP (not repeated to anyone), and who reports whom at what distance. **Never** accounts, e-mail, positions, audio. Nothing is written to disk |
| Everyone on your realm who runs EqVoice | your character name and a random session id while you are online (the roster) |
| Other addons / server operators | nothing about EqVoice: no addon traffic is sent unless the group harness is switched on |
| Nobody | a stable identifier: session ids and keys rotate with every game login |

**Threats and what answers them** (the honest version of impersonation is in `06`, section 8)

| Threat | Answer | Residual |
|---|---|---|
| Registering someone else's character name | one live holder per name and realm; the same key may take over, another key gets `name_in_use`; 30 s reservation after a drop | a squatter who is online first holds the name (denial of service); there is no way to check against the game server |
| Claiming to be near someone | a link needs the other side to report a character of that name within range, agreeing within 12 yd | someone physically next to a victim can borrow the name of another character the victim also sees |
| Learning who is online | the roster is shared with every EqVoice user of the realm by design | opt-in; names only; no accounts, no addresses, no history |
| Forged roster file | the addon ignores a file written for another character; CRC; 15 s freshness | a local process that can write the game folder can already do anything |
| Replayed registration | per-connection nonce; signed message includes sid, realm, character | |
| Random internet user connects | signalling relays only between linked pairs; optional join code; per-address connection and registration limits | someone in the game next to you is a legitimate neighbour |
| Stale sessions | idle sockets close at 30 s; a game gone for 30 s leaves the roster; keys older than 15 minutes are not reused | |
| Voice after leaving range | volume to 0 and mic detached within 1.5 s of leaving; connection closed 8 s later; **bridge silent for 8 s drops everyone** | up to 4 s of held readings while the bridge is merely stale |
| Signalling abuse | size, rate, per-link and per-address limits; strikes then disconnect; same error for offline and unlinked | a determined flooder can burn a free tier; the Cloudflare edge helps |
| IP leakage | stated in the UI; `relay_only` forces TURN | needs a TURN server (not part of the MVP) |
| Eavesdropping on voice | DTLS-SRTP between peers; the hub never carries media | a nearby player hears you, as with `/say` |
| Local web page attacks on the client UI | binds 127.0.0.1, random per-launch token, `Origin` check | |
| A malicious hub swapping SDP fingerprints | not defended | hardening path: derive the session id from the public key and verify over the data channel (section 16) |

**Privacy note, in plain words.** Peer-to-peer WebRTC exposes your public IP to everyone you connect to. That
is inherent. The mode that hides it is relay-only through a TURN server, which moves the bandwidth cost to
whoever runs TURN. It is a config switch (`relay_only`), not a rebuild.

## 4. Bridge options between game and PC, best to worst

| Rank | Option | Real time | Needs | Status |
|---|---|---|---|---|
| 1 | **File through a mod API** (`WriteCustomFile`/`ReadCustomFile`; `ExportFile`/`ImportFile`) | yes, works minimised, both directions | ClassicAPI/Nampower file functions, which this install has | game → client **confirmed in the real game**; client → game (roster) built, tested against the real Lua parser, **not yet run in the game** |
| 2 | **Optical**: the addon paints a 32 × 10 colour grid, the client reads the pixels | game → client only | nothing; WoW visible, windowed or borderless | built, tested against a real window at all four corners; **not run in the game** |
| 3 | A tiny helper DLL exposing one Lua function | yes | a DLL | not built; fallback in section 16 |
| 4 | Chat-log tail | maybe | a self-echo channel | rejected |
| 5 | SavedVariables | **no**, written at logout / `/reload` only | nothing | used for diagnostics only |
| 6 | Reading process memory, injection, packet capture, input synthesis | | | rejected by the brief |

The roster has to reach the game, so the file bridge is no longer optional: the optical path alone gives
voice-less presence. With `bridge.source = auto` both outbound paths run and either keeps the bridge alive.

## 5. MVP scope

In (built and tested):

1. Addon: roster file reader, scheduled name lookups, BF2 frame (file + optical), identity binding, fail-closed
   rules, status and diagnostic commands, the group harness (off by default).
2. Client: config, device selection, microphone, push-to-talk (global key and button), open mic, mute, deafen,
   per-peer volume, signalling with reconnects, identity per game login, roster file writer, per-peer state
   machine with distance volume, WebRTC audio, fades, metrics panel, logs, diagnostics export, simulation.
3. Hub: presence, realm rosters, mutual proximity, relay, limits, expiry, no media. Python and Cloudflare.
4. Simulation: a pretend game that speaks the real file formats, a shared world file, and a simulator panel.

Out (and why):

* voice activation detection (needs tuning against real mics; open mic and PTT cover testing)
* TURN and relay-only operation (config switch exists; no server)
* a proof of identity between peers over the data channel
* Linux global PTT (the polling API is Windows-only; the on-screen button works)
* crowds above 8 simultaneous callers (a mesh does not scale; the cap is `proximity.max_peers`)
* EqUpdater integration (the contract is in section 14)

## 6. Project structure

```
eqvoice/
  README.md
  docs/                               these documents
  protocol/
    bridge_vectors.json               generated from Frame.lua, checked by both languages
    conformance.json                  recorded hub conversations, replayed by the Python and JavaScript hubs
  tools/
    run_tests.py                      every suite, one summary
    lint_lua50.py                     Lua 5.0 compliance (the suite itself runs on LuaJIT)
    gen_bridge_vectors.lua  gen_conformance.py
    install-addon.ps1                 copy / junction / remove the addon
    read_savedvars.py                 print diagnostics saved by the game
    run_sim.py  sim_world.py          server + two clients with simulated games / edit the simulated world
    loadtest.py                       N simulated clients against the hub
  eqvoice-addon/EqVoice/          EqVoice.toc, Util, Proto, Nearby, Frame, Scan, Inbox, Lookup, Bridge, Comm, Diag, Core
  eqvoice-addon/tests/              stub client + 440 checks
  eqvoice-client/eqvoice_client/
    app.py  peers.py  roster.py  signalling.py  config.py  identity.py  ptt.py  browser.py  logbuf.py
    bridge/ frame.py  inbox.py  source.py  filesource.py  optical.py  sim.py
    ui/     index.html  app.js  style.css
  eqvoice-client/tests/
  eqvoice-signalling/eqvoice_signalling/   hub.py (pure logic)  server.py (aiohttp)
  eqvoice-signalling-cf/            hub.js (port of hub.py)  worker.js (Durable Object)  wrangler.toml  tests/
```

## 7-9. Formats

`02-protocol.md`.

## 10. Connection lifecycle and 11. Failure behaviour

`06-roster-pipeline.md` sections 5 and 6 (state machine with the thresholds, and the fail-closed table).

## 12. Deployment and cost

`04-deployment-and-cost.md`. In one line: a process of about 70 MB serving 300 simulated players on one realm at
under 5% of one core, hostable for nothing on a Cloudflare free plan, a free VM, or a home PC behind a tunnel.

## 13. Test plan

`03-onwow-test-plan.md`: the automated inventory and the very short real-WoW path.

## 14. EqUpdater integration contract

Nothing in EqVoice imports or assumes EqUpdater. To install and update it, EqUpdater needs to:

* copy `eqvoice-addon/EqVoice/` to `Interface\AddOns\EqVoice\` (it carries its own version in `EqVoice.toc`)
* ship the client as one folder or a PyInstaller bundle (the data files to include are `eqvoice_client/ui/*`)
* write `bridge.wow_dir` into `%APPDATA%\EqVoice\config.json` so the client knows the game folder (the roster
  file goes into its `CustomData\`)
* treat the user's `config.json` as theirs

The addon folder is addon-only (no `.exe`). The client writes exactly one file into the game folder
(`CustomData\eqvoice-in.txt`) and removes it when it quits.

## 15. Major unknowns needing a test on OctoWoW

Ordered by how much they change the design. The procedure for all of them is the short path in
`03-onwow-test-plan.md`.

1. Does the game read `CustomData\eqvoice-in.txt` and update within a second? (`/ev diag inbox`)
2. What does one scan of 80 names cost in frame time? (`/ev diag lookupcost 80`)
3. The real `/say` range in yards (**measured 2026-10-17: 40.8 to 41.2 yd**, docs/08; whether low-level characters hear less is still open)
4. How `UnitTokenFromName` behaves for players in other phases, ghosts, stealth, shapeshifts, battlegrounds
5. Whether a crowded capital (hundreds of EqVoice users on the roster) stays within the frame-time budget
6. Cross-faction: lookups work, but is voice between factions wanted?
7. Whether the file functions are safe to call every second (disk writes, antivirus)

Resolved: SAY/YELL/WHISPER/OFFICER/CHANNEL addon messages do not exist on this client; nameplate enumeration returns
nothing; by-name distance lookup works to at least 86 yd; the outbound file bridge works.

## 16. Fallbacks

* **If the inbound file does not work in game:** the optical copy cannot carry a roster into the game. The smallest
  DLL that would: one exported Lua function, `EqVoiceExport(string)` / `EqVoiceImport()`, registered through
  the mod loader that already runs (`dlls.txt`). No reads of game memory, about 100 lines. The client already
  accepts any file that contains `OVB2 <hex>`, so only the transport changes.
* **If lookups are too slow in crowds:** lower `roster.max_checks`, raise `roster.scan_interval_s`, or scope the
  roster by zone (an optional field the hub can add without changing the protocol).
* **Group harness:** guild, party and raid members within a map radius, over documented channels. A test tool only.
* **Hardening path for identity:** make the session id the first 128 bits of a hash of the client's public key,
  advertise it in the roster, and verify over a WebRTC data channel before attaching the microphone. That closes the
  "malicious hub swaps the SDP" gap, at the price of one extra round trip.
