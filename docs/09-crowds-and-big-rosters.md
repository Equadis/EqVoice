# 09 — Crowds and big rosters: what was measured, what was changed

Two separate questions: how much does it cost a player to be near several others at once (a **mesh** of WebRTC connections), and how much
does it cost the game and the client to hold a roster of hundreds or thousands of opted-in players (**roster scaling**)? Measure first;
change only what a measurement justified.

## A. Five (and more) players in one place

Every pair of nearby players has its own WebRTC connection (a full mesh), so each player holds N-1 connections and encodes their microphone
once per connection. `max_peers` (default 8) caps it.

**Tests that pin the behaviour** (`eqvoice-client/tests/test_mesh.py`, scripted media pages, fast; and
`test_e2e_browser.py::test_five_strangers_real_engines`, five real Edge engines):

| Scenario | Checked |
|---|---|
| five strangers on one road | full mesh forms: 10 hub links, 20 calls opened (one per pair and side, no storm), no rate-limit trips; every pair's volume equals `gain_for(that pair's distance)` and we only transmit to people who can hear us |
| one arrives, then two at once | the others open exactly one new call each; nobody already connected is touched |
| one leaves, then two together | everybody fades them first (volume 0, sending stops), then the calls are closed, on every survivor; the rest of the mesh is undisturbed; the hub drops the links |
| one pair fails (ICE failed) | only that pair retries (back-off, then one new call each); the other three players open nothing |
| one player's voice window restarts | all her calls drop, nobody else is touched, and the whole mesh re-forms |
| three talk at once, a fifth is silent (real engines) | the listener decodes all three simultaneously and nothing from the silent one |
| a real engine's page connection is cut | the engine reconnects by itself and the mesh heals |

**Instrumentation.** `TRACE desktop resources ...` every 10 s while any call or engine exists (active and connected calls, audio in/out kbit/s,
client and engine CPU and memory, set-up time average and maximum); `webrtc_connected ... total_ms=` for every set-up; `webrtc_stats` every
5 s per call; the window's *Voice links* and *This PC* rows. CPU and memory come from the Windows process APIs (no extra packages): the engine
is the browser and every helper process it started.

**Measurements** (`python tools/mesh_bench.py`; Windows 10, Ryzen 5 5600X; N headless Edge engines with fake microphones **and** N clients all on this one
PC, otherwise idle; each row averaged over 20 s; *one* = one person holds push-to-talk, *all* = everybody at once):

| Players | Links | Mesh up | Set-up per call avg / max | Situation | Engine CPU avg / max (% of one core) | Engine memory (MB) | Audio in / out per player (kbit/s) |
|---|---|---|---|---|---|---|---|
| 2 | 1 | 1.1 s | 0.32 / 0.41 s | idle / one / all | 2 / 4 · 10 / 26 · 7 / 17 | 316 | 0.7 · 4.2 · 7.6 |
| 3 | 3 | 1.3 s | 0.38 / 0.58 s | idle / one / all | 7 / 11 · 5 / 12 · 5 / 10 | 316 | 1.4 · 5.8 · 14.5 |
| 4 | 6 | 2.1 s | 0.73 / 1.2 s | idle / one / all | 8 / 11 · 9 / 11 · 12 / 29 | 300 | 2.1 · 6.6 · 22.1 |
| **5** | **10** | **2.7 s** | **1.2 / 1.9 s** | idle / one / all | **12 / 17 · 10 / 19 · 13 / 36** | **320** | **2.8 · 8.3 · 30** |
| 6 | 15 | 4.3 s | 2.0 / 3.5 s | idle / one / all | 24 / 41 · 21 / 28 · 19 / 24 | 330 | 3.5 · 9 · 36 |
| 7 | 21 | 9.9 s | 5.2 / 9.2 s | idle / one / all | 26 / 36 · 30 / 46 · 35 / 48 | 410 | 4.1 · 9.6 · 44 |
| 8 | 28 | 15.4 s | 7.0 / 14.3 s | idle / one / all | 32 / 43 · 44 / 143 · 42 / 81 | 410 | 4.9 · 10.8 · 50 |

How to read it:

* **Five players is comfortable**: the mesh is up in under 3 s, each engine uses about a tenth of one core and 320 MB, and the whole mesh
  carries 30 kbit/s each way while *everyone* talks (the fake microphone is a steady tone; real speech through Opus is configured up to
  32 kbit/s per call, so the worst case for five is about 4 x 32 = 128 kbit/s up and the same down).
* **From six players up the numbers climb sharply, but this benchmark overstates it**: 7 engines and 7 clients share one PC with the
  measuring script, so CPU and set-up times include contention that one player on one PC will never see. Treat 6 to 8 as "works, costs more", and the
  default `max_peers = 8` as the ceiling it should stay at.
* **Memory is the browser, not the mesh**: 300 to 410 MB per engine regardless of how many calls it holds.
* **The engine is not free when silent**: with nobody talking, CPU still grows with the number of calls (2% at 2 players to 24% at 6). The page
  runs timers per call (statistics every 0.5 s, a 20 ms volume fade, a 100 ms level meter) and the browser keeps the microphone processed
  for echo cancellation. **Not optimised, deliberately**: if a crowd turns out to matter, the cheapest levers to try next are statistics only every 2 s per
  call and only for connected calls, and a fade timer that runs only while a fade is in progress.
* The Python client for all N players together costs 1% to 16% of one core in this benchmark (so roughly 1 to 3% per player).

## B. Big rosters (50 to 1000 opted-in players on a realm)

What the client hands the game: a roster file (25 names per line), rewritten every 4 s as a heartbeat. What the game does with it: look names up
by exact name round-robin, a budget of names per 0.5 s scan.

**Costs measured outside the game** (`python tools/scale_bench.py`; the addon column is LuaJIT on a stub, so only the growth means anything:
the game's Lua 5.0 is several times slower):

| Players | Hub: register all | Newcomer learns roster | Roster file | File write | Lookups per s | Full sweep | Client tick | Addon: new roster / heartbeat / scan |
|---|---|---|---|---|---|---|---|---|
| 50 | 13 ms | 1.8 KB, 1 message | 0.4 KB | 0.9 ms | 160 | 0.3 s | 0.05 ms | 0.10 / 0.000 / 0.005 ms |
| 100 | 22 ms | 3.6 KB, 1 | 0.8 KB | 1.1 ms | 160 | 0.6 s | 0.04 ms | 0.23 / 0.000 / 0.005 ms |
| 250 | 69 ms | 8.7 KB, 1 | 1.8 KB | 1.9 ms | 160 | 1.6 s | 0.05 ms | 0.50 / 0.005 / 0.015 ms |
| 500 | 165 ms | 17 KB, 2 | 3.5 KB | 3.2 ms | 168 | 3.0 s | 0.07 ms | 1.0 / 0.005 / 0.025 ms |
| 1000 | 509 ms | 34 KB, 4 | 7.0 KB | 5.8 ms | 334 | 3.0 s | 0.13 ms | 1.9 / 0.000 / 0.050 ms |

The game's report frame with 200 nearby players is 1,841 bytes (encode and decode about 1.5 ms in Python). Nothing here grows
faster than the roster, and nothing is large. Two things are, or could be, felt:

1. **Parsing a new roster in the game.** In Lua 5.0 a 1000-name file may take tens of milliseconds, once per roster *change*. It used to be
   once per heartbeat (every 4 s): the addon now takes a rewrite whose body is byte-for-byte the previous one (same CRC, same character) on its
   header alone: **0.000 ms against 1.9 ms** at 1000 names in LuaJIT. Measured in the real game: about 0.11 ms per name, 112 ms for a 1000-name file (table below).
2. **The lookups themselves** (`UnitTokenFromName` + `UnitDistanceSquared` per name): the one cost only the game can tell. `/ev diag scale` (22 s,
   do not move the camera) times 50 to 1000 lookups per frame and prints, per size, microseconds per lookup, frame time and fps, and what the
   schedule in force costs per second. **Run on Ollie's client 2026-10-22**, so the budget below is measured, not a guess,
   bounded at 200 lookups per scan.

   **Measured on Ollie's client** (`/ev diag scale`, saved 2026-10-22 18:52; idle frame 9.21 ms):

   | Names | Parse of a new roster file | Frame time if EVERY name were looked up EVERY frame | Per lookup | Schedule in force (80 per 0.5 s) |
   |---|---|---|---|---|
   | 50 | 6.0 ms | 9.15 ms (109 fps) | under the timer's resolution | 0.00 ms per second |
   | 100 | 13.6 ms | 9.37 ms (107 fps) | 1.6 us (noise) | 0.25 ms per second |
   | 250 | 30.1 ms | 12.17 ms (82 fps) | 11.8 us | 1.89 ms per second |
   | 500 | 59.0 ms | 15.55 ms (64 fps) | 12.7 us | 2.03 ms per second |
   | 1000 | 111.7 ms | 23.20 ms (43 fps) | 14.0 us | 2.24 ms per second |

   Reading: **lookups are free.** A lookup is 12 to 14 us (the 0 and 1.6 at 50 and 100 names are the timer's resolution). The frame-time column
   is a stress test that no real schedule comes near; the schedule actually in force costs 2 ms per second at 1000 names, and the automatic
   one at its worst (200 per scan, 400 lookups a second) about 5.6 ms per second, 0.6 % of play time. (That run had a 0-name roster, so the
   floor of 80 per scan was in force; with 1000 names the client would send 167.) **The parse is the one cost you can feel:** about 0.11 ms
   per name, paid once per roster *change* (never per heartbeat). At 60 fps that is a hitch smaller than a frame at 100 names, two frames at
   250, four at 500 and seven at 1000. How often it is felt is the roster's churn: a quiet roster of 1000 is fine, one that changes every few
   seconds would stutter.

**Scheduling strategy** (`Lookup.lua`, header comment; budgets sized by `auto_checks` in the client):

1. Players loaded in the last 10 s are looked up on **every** scan (their distance matters).
2. Names **never looked up yet** come next, inside the budget: a player who just came online is checked within the next scan, however big
   the sweep (measured: found in 0.25 s in a settled roster of 1000).
3. Everyone else is checked round-robin, `checks` per scan, sized automatically from the roster: `ceil(N x 0.5 / 3)` per 0.5 s scan, never below 80 or
   above 200, so any roster is swept in about 3 s (80 per scan up to 480 names, 167 at 1000, 200 beyond 1200).
4. A name that keeps coming back "not loaded" is checked every scan twice, then every 1 s, then every 2 s (before: up to 4 s, which only delayed
   finding strangers, because the per-scan budget already bounds the cost). A roster small enough to fit one scan's budget is never slower than 1 s.

**Stranger discovery** (a stranger becomes loaded among N unresolved names; simulated with the real addon code; the three tested positions are the first, the
middle and the last name): found within 0.75 s at 50, 1.75 s at 100, 250, 500 and 1000. The worst case is bounded by one sweep plus one scan interval
(3.5 s at 1000). Before this change a 1000-name sweep took 6.3 s (80 per scan), and unresolved names also backed off for up to 4 s.

**Not done, on purpose.**
* *Zone scoping* (only look up names in my zone): it would need every client to tell the hub its zone (a new protocol field and a new thing the hub learns
  about each player). The real-game numbers agree: a lookup costs 14 us, so there is nothing to save.
* *Incremental roster updates* instead of rewriting the whole file: a protocol change; worth it only when the parse is felt: in the game it is 59 ms at 500 names and
  112 ms at 1000, so from about 500 names with a busy roster. Cheapest fixes if that day comes, neither built: the client rewrites the roster file
  at most once every few seconds (it then coalesces churn), and/or the addon parses the file in slices over several frames. Below about 250 names
  (30 ms) it is not noticeable.
* *Batching roster broadcasts in the hub*: registering a player costs O(roster) sends (0.5 ms at 1000), so a login storm of 1000 is half a second of hub CPU. Fine.

## Reproducing

```
python tools\mesh_bench.py --sizes 2,3,4,5,6,8 --window 20     real engines (several minutes; close other heavy programs while it runs)
python tools\scale_bench.py                                    hub, file, client, addon (LuaJIT)
luajit eqvoice-addon\tests\run.lua                           the scheduling simulation prints its discovery times
/ev diag scale                                                 in the game
```
