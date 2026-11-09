# 03 — Test plan

Two parts: what has been **run** (automated, no game needed), and what only the **game** can answer. The
game part starts with the very short path that exercises the whole pipeline with two real characters.

## Part A — automated checks

`python tools/run_tests.py` runs everything except the slow real-browser tests; add `--all` for those (about
5 minutes). Last full run: all green.

| Suite | Count | What it covers |
|---|---|---|
| Lua 5.0 lint (`tools/lint_lua50.py`) | 11 files | TOC parity, syntax, no `#` / `%` / `...` / `select` / `string.match`, every global resolves, optional APIs always guarded |
| Addon suite (`luajit eqvoice-addon/tests/run.lua`) | 440 checks | util, protocol parse/reject, frame BF2 encode/decode/cells/optical truncation, roster-file parser (CRC, identity binding, freshness), lookup scheduler (round robin, backoff, recheck of known players, 500-name roster), the whole pipeline roster-in / distances-out, lookup cost benchmark, group harness, logout |
| Bridge vectors | 9 vectors | the Python encoder and decoder agree with the Lua encoder byte for byte and cell for cell (`protocol/bridge_vectors.json`, regenerated and diffed by the runner) |
| Conformance recording | 25 scenarios, 355 steps | `protocol/conformance.json` is current with the Python hub (`tools/gen_conformance.py --check`) |
| Signalling hub (`pytest`) | 72 | registration and signature rules, nonce replay, realm isolation, duplicate login, takeover, reservations and expiry, roster chunks, character change, mutual proximity (agree, one-sided, disagree, out of radius, stale, grace, replacement set), relay rules, limits, timeouts, malformed and spoofed messages, 600 simulated sessions |
| Signalling server (real sockets) | 5 | upgrade, limits, shutdown |
| Hub conformance | 28 | the Python hub replays every recorded conversation; the **JavaScript hub** (`hub.js`) replays the same 355 steps in headless Chromium with identical transcripts (a deliberate one-line change to a limit makes it fail) |
| Cloudflare adapter | 1 (32 checks) | `worker.js` in headless Chromium against a **mock** Workers runtime: routing, registration, attachments, hibernation rebuild, alarms and idle sweep, close handling, legal close codes, address cap |
| Cloudflare Worker on the real runtime (opt-in) | 8 + 2 | `EQVOICE_CF_URL=ws://127.0.0.1:8787/ws` with `npx wrangler dev` running: `test_cf_runtime.py` (registration, link, relay, close codes, alarms, the per-address cap, **a real hibernation**) and the client's pipeline through it (`test_external_hub.py`); `tools/hub_smoke.py` is a 2-second check of any hub |
| Client: frame/pixels | 53 | agreement with Lua, decoding at integer and fractional cell sizes, offsets, noisy scenes, gamma and drift, damage detection, stale-lock recovery |
| Client: roster file | 28 | format, CRC, atomic writer, refresh, clear; **the real Lua `Inbox.Parse` reads what Python wrote** and rejects the same damage |
| Client: bridge sources, config, identity | 17 | fail-closed monitor (live/stale/lost, flags, logout), file source, torn writes, config merge, identity per game login |
| Client: simulated game | 13 | roster in, distances out, report radius, identity mismatch, damaged and stale roster, expiry, round-robin budget, freeze, logout, shared world file |
| Client: peer state machine | 43 | every transition: debounce, hub confirmation, leave grace, hysteresis, volume curve and rate limit, fade then late teardown, reuse of the connection on return, missed scan, signalling loss, roster changes, failure backoff, capacity, 300-player crowd |
| Client: signalling client | 11 | against the real hub: roster, mutual link, coalescing and keepalive, reconnect, identity change, name in use, join code, ordering, old-protocol refusal |
| Client: push-to-talk | 10 | key resolution through the keyboard layout, press/release tail, no flicker on a quick re-press, virtual button |
| Client: app end to end | 20 | **the real-WoW path in simulation** (register, far apart, walk close, voice, volume follows distance, walk away, return), 18/19 yards connects and 60 breaks it, one-sided proximity never connects, a known name alone is not enough, frozen game fails closed and recovers, a game that stays gone leaves the roster, addon off / no lookups, hub loss, logout and re-login, character switch, duplicate login, roster file binding and cleanup, 400-name realm, metrics, controls, page loss, diagnostics |
| Client: optical capture of a real window | 6 | real GDI capture and decode at all four corners; window not found / minimised |
| Developer tools | 8 | SavedVariables parser |
| **Real browsers** (`test_e2e_browser.py`) | 2 | two real headless Edge media engines behind the full pipeline: audio both ways, **page gain follows distance**, **push-to-talk silences the audio**, leaving range fades, **stops sending the microphone to that peer**, closes the RTCPeerConnection, and reconnects. Four engines: six simultaneous connections, no flood-limit trips, one leaves and the triangle is undisturbed |

Load: `python tools/loadtest.py --clients 300` (numbers in `04-deployment-and-cost.md`). Simulation by hand:
`python tools/run_sim.py --fake-media --demo`.

What the automated checks **cannot** show: anything the 1.12.1 client or the OctoWoW server does. The Lua suite
uses a stub client, so API behaviour is as understood, not as observed; the pipeline's game half has only been
exercised against the simulated game.

## Part B — in the game

**Before anything:** `python tools\realtest.py doctor` (no game needed). It checks the game folder, that the addon in
`Interface\AddOns\EqVoice` is identical to this repository's, Node and Wrangler, Edge, ports. Use throwaway
characters on the **same realm**, not grouped, not guilded, not friends. After installing the addon
(`powershell tools\install-addon.ps1 -AddOns "D:\Octowow\Interface\AddOns"`) the game needs a `/reload`.

Every boundary of the pipeline narrates itself with `TRACE` lines, and `tools/realtest.py` turns them into a live
20-step checklist, so a failure names the boundary instead of leaving you to guess.

### Stage 0: can the game read a file? (two minutes, no desktop client)

```
/reload
/ev diag inbox
```

`CustomData\eqvoice-in.txt` holds the plain test text `OVI1 hello from the client`. Expected:
`CustomData\eqvoice-in.txt -> 38 bytes: OVI1 hello from the client 2026-10-15 | readable, but not a roster file (numbers)`.
That proves the game reads a file written outside it. **If it says `no file read`, stop here**: the roster cannot reach the
addon, and nothing after this can work. Check the file really is in `D:\Octowow\CustomData\` and that the mod provides
`ReadCustomFile` (`/ev diag api` lists it).

### Stage 1: one game, one client (five minutes)

```
python tools\realtest.py run --test-roster <name of any player standing near you>
```

The launcher starts the hub (Wrangler, tracing on) and waits for the addon's `eqvoice-<Character>.txt`. Log in and
stand near any other player (an EqVoice user or not: the lookup is by name). The checklist should pass steps 1 to 10 and
mark the rest `n/a`: your identity came from the game, the name reached the addon (`inbound_acked`), the addon found that
player by exact name (`lookup X: FOUND by exact name, 12.3 yd`) and the desktop read the distance. The client window's
*Pipeline* panel shows a *Test names* row with the same yards. Step 7 is the inbound bridge, the one boundary never yet
run in the real game: if it fails the report says `written but the game never acknowledged it`.

### Stage 2: two characters, the full test

```
python tools\realtest.py run
```

Then log both characters in (two game instances from one folder are fine: each addon writes its own
`eqvoice-<Character>.txt`; if your games run from two folders, add `--wow-dir` for each). The launcher starts one
desktop client per character it finds, each with its own window and push-to-talk key (**F9**, then **F10**), and prints
each step as it passes. Two clients on one PC share your microphone and speakers: **wear headphones**, and use one
client at a time (or the *Hold to talk* button in a window).

Before you walk anywhere, press **Test speakers** in each client window: two rising notes should play through the speakers
chosen there. Then check the *mic level* bar moves when you speak. A red line at the top of a window (and a `!!` line in the
launcher) means the voice engine is wrong: a hidden test browser, or Chromium's fake microphone (it plays a **beep** instead
of your voice). `realtest run` stops every leftover test browser before it starts, and gives each client window a fresh
random token, so an old one cannot reattach; if you still see that warning, close all Edge windows and start again.

| What you do | Steps that should pass |
|---|---|
| log in, stand anywhere | 1-6: hub, clients, frames, identities from WoW, both registered, each sees the other's name |
| stay within ~86 yards of each other | 7-10: the name reaches the addon, the addon finds it by name, reports the distance, the desktop reads it |
| walk within ~40 yards | 11-13: both report each other, the hub links them, WebRTC connects |
| hold F9 and talk, release; hold F10 and talk | 14: audio in both directions |
| walk straight away | 15-17: the distance climbs, the volume falls |
| keep going past ~43 yards (or out of sight) | 18-19: leaves range, audio stops |
| wait about 8 seconds | 20: the peer connection is torn down |

Only one run at a time: `run` refuses to start while the previous run's clients still hold ports 17421-17424 (two overlapping
runs crash-loop their clients and close each other's voice windows). If a run is still going and you cannot find its terminal,
`python tools\realtest.py stop` ends its hub, clients and voice windows (its final checklist is then rebuilt with
`python tools\realtest.py report`).

Ctrl+C stops everything and prints the final checklist (also saved to `D:\Octowow\.eco-work\realtest\run-*\report.txt`).
`python tools\realtest.py report` prints it again later. Logs: `...\realtest\run-*\hub.log` and
`...\realtest\home-<Character>\logs\eqvoice.log`.

### What narrates what

| Component | Where | Boundary lines (`TRACE ...`) |
|---|---|---|
| Hub | `hub.log` (Wrangler's output; `--trace` for the Python hub) | `registered`, `roster_sent`, `roster_update`, `proximity` (every report received), `link_up`, `link_down why=...`, `signal`, `left`, `error` |
| Desktop | `eqvoice.log`, the console, the client window's log view | `start`, `identity`, `hub_state`, `hub_registered`, `roster_received`, `roster_update`, `inbound_written`, `inbound_acked`, `outbound_read`, `distance`, `proximity_sent`, `hub_link`, `peer A -> B`, `webrtc`, `webrtc_signal`, `webrtc_closed`, `media_engine`, `mic` (label, `fake=`), `audio_devices`, `media_warning`, `speaker_test`, `audio_mic`, `audio_gain`, `audio_tx`, `audio_rx`, `pipeline` (every 5 s) |
| Addon | game chat (green), and the bridge file, so the desktop logs it as `TRACE addon ...` | `identity`, `inbound: read <file> seq N ... roster is now N name(s)`, `inbound: no roster file yet`, `lookup X: FOUND by exact name, N yd`, `lookup X: not available (reason)`, `scan: ...`, `outbound: frame #N written (...) results`, `logout` |

`/ev trace off` silences the chat copy (the file copy continues). Hub trace lines show character names: leave `TRACE`
off on a hub other people use.

### Where it can stop

| Step the report stops at | What it means | Check |
|---|---|---|
| 1-2 | hub or client did not start | `realtest.py doctor`; `hub.log`; ports 8787 and 17421+ |
| 3 | no frames from the game | the addon is enabled and `/reload`ed? `/ev status` says `bridge file on`; CustomData files change; `--wow-dir` is the folder with `WoW.exe` |
| 4 | no identity / two characters on different realms | the roster is per realm |
| 5 | not registered | `hub_state` and `hub_error` lines (`name_in_use` = that name is already online) |
| 6 | the other's name never arrived | different realm, or the other client has not registered |
| 7 | **inbound bridge**: written, never acknowledged | `/ev status` in game: `inbox: NO FILE` (file name not readable: does ReadCustomFile accept `eqvoice-in-<Name>.txt`?) or `IGNORED: written for X`; `/ev diag inbox` |
| 8 | the addon cannot find the player by name | they must be loaded near you (within ~86 yards); `/ev diag name <Player>` |
| 9-10 | distance found but not delivered | outbound file path or `--character`; the `outbound_read` lines |
| 11 | one-sided: only one game sees the other | the hub's `proximity` lines, then that game's lookups |
| 12 | no mutual link | `link_down why=` (disagree / stale / one side / farther than 45 yd) |
| 13 | WebRTC failed or never started | `webrtc ice=failed` means the two PCs cannot reach each other directly (no TURN) |
| 14 | no audio | `audio_tx SENDING` on the speaker but no `audio_rx RECEIVING` on the listener: network; neither: mic permission or PTT. The report says **NOT REAL VOICE** when a client's microphone was Chromium's fake device (`TRACE desktop mic ... fake=yes`) or its engine a hidden browser (`media_engine ... headless=yes`): that is a leftover test browser, not a fault in the pipeline. `audio_rx RECEIVING` carries `playing`, `muted`, `element_volume` and `speakers`: if it says RECEIVING but you heard nothing, the answer is in those four, or in the Test speakers button |
| 15-20 | the walk | wrong distance, wrong volume, or teardown too early or too late: the lines show distances and gains |

### B-measure: numbers only the game can give

```
/ev diag lookupcost 80      frame time with and without 80 name lookups per frame (the real cost of one scan)
/ev diag name <Player>      one exact-name lookup, with the distance
/ev diag inbox              what ReadCustomFile / ImportFile return for eqvoice-in.txt
```

Record: frame time idle vs loaded (should be well under a millisecond per scan: the addon scans every 0.5 s, so
this is spread out), the real `/say` range (a second player says numbered lines while you walk straight away;
compare the last number you can read with the last distance shown), and whether anything changes in a capital
with hundreds of names on the roster.

### Experiments already answered (kept for other builds)

| Question | Answer on OctoWoW's client (2026-10-15) | Command to repeat it |
|---|---|---|
| Do SAY / YELL / WHISPER / OFFICER / CHANNEL addon messages exist? | no: `Unknown addon chat type`; GUILD / PARTY / RAID / BATTLEGROUND do | `/ev diag all` |
| Does nameplate enumeration find players? | no (0 units, `GetNamePlateGUIDs` 0) | `/ev diag nearby` |
| Does a by-name lookup return exact yards? | yes, 7.7 / 18.3 / 47.8 / 86.1 yd confirmed | `/ev diag name <Player>` |
| Does the outbound file bridge work? | yes: `WriteCustomFile` lines read back by the client | `/ev diag file` |
| Does guild-channel addon traffic loop back? | yes (3/3, average 271 ms) | `/ev diag guild` |

The group harness (`/ev transport group`, guildmates only) remains for testing the client without ClassicAPI; it does
not feed the roster pipeline. Behaviour of shapeshifts, ghosts, instances, battlegrounds and cross-faction
lookups should be noted from the short path in those situations: report what `/ev status` and the Pipeline
panel say.

### Reporting template

Paste `report.txt` from the run directory, and for the first step that did not pass, the 20 log lines around it from
`eqvoice.log` (the desktop and the addon's `TRACE addon` lines) and from `hub.log`. Plus, from game:
`/ev status` and `/ev diag api`.
