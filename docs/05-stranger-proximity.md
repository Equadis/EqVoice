# 05 — Proximity between strangers

Requirement (from the brief, restated after the SAY test failed): two EqVoice users who are strangers
walk within about `/say` distance, voice connects; they walk apart, voice fades and the connection closes.
No group, no guild, no friend list, no manual room. Group mode survives only as a test harness.

> **Status, after the roster pipeline was built (`06-roster-pipeline.md`).** The design below went through the
> "Option A, phase 2" route of section 6: the hub keeps a roster of online characters, the client hands the names
> to the addon through a file, and the addon looks them up by name (`UnitTokenFromName`, `UnitDistanceSquared`),
> because nameplate enumeration returned nothing. Differences from the sketch in sections 3 and 10: the
> directory holds plain character names and random session ids, not hashes (a decision to accept a realm-wide
> roster of opted-in names, see `06` section 2); the link needs both sides' reports to agree within 12 yd; the
> data-channel identity proof is **not built** (`01` section 16); volume follows distance with the curve in `06`
> section 5. Everything marked "not built" in the sections below that `06` describes is now built, apart from the
> proof. In-game verification of the pipeline is still outstanding (`03`, B-short).

## Verdict

1. **A helper DLL is not needed, but the discovery route changed.** (See "In-game results" below: nameplate
   enumeration returned nothing on this client; lookup by name with exact distance works.) The client mods
   already installed here expose, without any grouping, the names, GUIDs, distances and world positions of
   units that have a nameplate, and the distance to any loaded player asked about by name:
   **ClassicAPI** (`C_NamePlate.GetNamePlateGUIDs`, `UnitDistanceSquared`, `UnitPosition`,
   `UnitTokenFromName`), and partly **SuperWoW** (`frame:GetName(1)` GUID, friendly `UnitPosition`).
   EqUpdater classifies ClassicAPI, SuperWoW, Nampower and UnitXP_SP3 as *essential* mods, so users of the
   OctoWoW ecosystem have them. Confirmed in the running client for the by-name lookup; **refuted for
   nameplate enumeration.**
2. **The bare 1.12.1 client alone gives names only**, through nameplate frames, and only while nameplates
   are on. No distance, no GUID, no way to tell an NPC from a player. Binary proximity at best.
3. **Recommended design: "see, then introduce".** Discovery happens locally from what the game client
   already knows; only the names of players who are *registered EqVoice users* ever reach the signalling
   server; distance comes from the game, so volume can follow distance.
4. **The per-zone chat channel is the worst of the three** on privacy and reliability, and it would still
   need the same extensions for a position source. Keep it as a last resort. Not built.

## In-game results (2026-10-15) — they change the plan

| Test | Result |
|---|---|
| Sources present | ClassicAPI nameplates, `UnitDistanceSquared`, `UnitTokenFromName`, SuperWoW plates, UnitXP, `UnitPosition`: all yes. `UnitPosition(player)` returns **4 values** (y, x, z, map id): ClassicAPI's shape is live |
| Nameplate reach | `NameplateRange = 41`, `NP_NameplateDistance = 41` |
| Scan by nameplates (`C_NamePlate.GetNamePlateGUIDs`) in Orgrimmar, many players in view | **0 units.** `GetNamePlates` 0 frames, `nameplateN` tokens 0. The floating names on screen are not nameplates, or this client build does not feed ClassicAPI's plate list. Either way **enumeration by nameplate does not work here** (the user had "nameplates on") |
| **Look a player up by name** (`/ev diag name Wipemon`) | **Works.** token `target`, `player=true`, **7.7 yd**; then by GUID **18.3 yd, 47.8 yd, 86.1 yd** as the distance grew. No nameplate, no group, and far beyond the 41-yard plate reach |
| File bridge, addon to client | **Works.** `CustomData\eqvoice.txt` is rewritten about once a second; the client's decoder reads it, CRC valid, counter advancing, flags correct (enabled, file, optical) |
| Reverse direction (client to addon through a file) | test file in place; `/ev diag inbox` reads it. Pending |

What this means:

* **The capability exists and is exact**: the game client will tell the addon the true distance to any
  loaded player it is *asked about by name*, to at least 86 yards. What it will not do on this client is
  *list* the players around you.
* So discovery has to be **directory-driven**: the addon is given names to ask about. This is section 6
  ("phase 2") below, promoted to the main design.
* The directory has to carry **plain character names**, not hashes, because the lookup needs the name. That
  is a real privacy trade: every EqVoice client receives the roster of other EqVoice users' character
  names on the realm. (Names are public in game, and these are opted-in users, but it is a list.) Hashing was
  only possible while discovery was local and could intersect hashes.
* The addon needs a way to receive that roster: **the inbound file**, `CustomData\eqvoice-in.txt`, polled
  with `ReadCustomFile`. This makes the file bridge two-way, so the pixel bridge alone is no longer enough;
  stranger proximity needs Nampower 3.2+ or SuperWoW's file API (both present, both "essential" in
  EqUpdater).
* Cost: the addon asks `UnitTokenFromName` once per roster name each tick. That is a native call; hundreds of
  names are cheap, and the roster can be narrowed (see below).
* The nameplate scan stays as an optional extra on clients where it works.

Revised flow (supersedes section 3 where they differ):

1. The client registers the player's character name (from the bridge frame) and a session id with the server.
2. The server publishes the realm's roster of registered names (and updates it as people come and go).
3. The client writes the roster to `eqvoice-in.txt`; the addon polls it once a second.
4. Every 0.5 s the addon looks each roster name up with `UnitTokenFromName(name, true)` and reads
   `UnitDistanceSquared`; names that are not loaded nearby simply return nothing.
5. The bridge frame (BF2) carries the `(name, distance)` pairs back to the client.
6. The client declares those names and distances to the server; the server links mutual pairs whose two
   distances agree (section 4); WebRTC, proof, gain by distance and the fade/grace/close are as designed.

Narrowing the roster, if it gets big: the server can partition it by the coarse zone each client reports
(continent and zone index, no coordinates), so a client only probes names registered in its own zone.

## 1. What the installed extensions expose

Evidence labels: **[installed]** string found inside your DLL (`tools/dll_apis.py "D:\Octowow"`),
**[doc]** the mod's published documentation, **[unknown]** untested here.

| Capability | Source | Evidence |
|---|---|---|
| List every unit that has a nameplate, as GUIDs, no group needed | ClassicAPI `C_NamePlate.GetNamePlateGUIDs()`; walks the client's object hash table for units | [installed] [doc] |
| `UnitIsPlayer`, `UnitName`, `UnitIsEnemy`, ... on a GUID string or on `nameplateN` tokens | ClassicAPI unit tokens | [doc] |
| Squared world distance to any such unit, center to center, in yards squared | ClassicAPI `UnitDistanceSquared(unit)` returns `(d2, checked)` | [installed] [doc] |
| World position of **any visible unit, not restricted to your group**: `y, x, z, instanceId` | ClassicAPI `UnitPosition(unit)` ("strictly more permissive" than the standard one) | [installed] [doc] |
| Find a loaded player **by name**, no nameplate needed, "every unit loaded around you" | ClassicAPI `UnitTokenFromName(name, exact)` | [installed] [doc] |
| Line of sight, 40-yard reach-aware range | ClassicAPI `UnitInLineOfSight`, `UnitInRange` | [installed] [doc] |
| Nameplate frame GUID: `frame:GetName(1)`; GUID accepted by every unit function | SuperWoW | [installed] [doc] |
| `UnitPosition` for **friendly** units only; a different return shape from ClassicAPI's | SuperWoW | [installed] [doc] |
| Nameplate reach 10-80 yd (`NameplateRange`); `ChatBubbleRange` 10-200 yd | SuperWoW | [installed] [doc] |
| Edge-aware distance between two units: `UnitXP("distanceBetween", a, b)` | UnitXP_SP3 | used 21 times by the SuperCleveRoidMacros addon in this install |
| Raw unit descriptor fields by GUID (`GetUnitData`, `GetUnitField`), `NP_NameplateDistance` | Nampower | [installed] [doc] |
| **Enumerate all nearby units without nameplates** | nobody | none of `GetNearbyUnits`, `EnumerateUnits`, `GetVisibleUnits`, `GetNearbyPlayers` appears in any DLL |

Three things to know:

* **Nameplates are the gate.** ClassicAPI's enumeration lists units that have a nameplate. Friendly
  players only get one while friendly nameplates are on (the "Show Friendly Name Plates" key binding), and
  only within the nameplate reach. Whether that reach covers `/say` range on OctoWoW is **[unknown]**.
  The by-name route (`UnitTokenFromName`) does not need nameplates but needs names to look up (section 6).
* **`UnitPosition` has two shapes.** SuperWoW and ClassicAPI both define it; the last to register wins.
  `dlls.txt` loads SuperWoW before ClassicAPI, so ClassicAPI's four-value shape should be live.
  `/ev diag nearby` prints how many values it returns. Distance does not depend on this
  (`UnitDistanceSquared` is used).
* **Facing range.** SuperWoW's chat-bubble range and Nampower's `NP_ChatBubbleDistance` (default 60)
  are about rendering bubbles, not the server's `/say` range. The real `/say` range must be measured
  (section 5).

## 2. What the bare client contains

The engine knows every nearby object: its object manager is exactly what ClassicAPI reads. Lua gets almost
none of it:

* nameplate frames: the unit's **name** as text; nothing else
* tokens: `target`, `mouseover`, `party1..4`, `raid1..40`, `pet`
* combat-log and chat events: names of players who act or speak in range
* `CheckInteractDistance(unit, n)`: 11, 28 yards and so on, for those tokens only

So the client alone can say "a player called X is near" if their nameplate is showing, and nothing about how
near. That is enough for a binary MVP and not for volume by distance.

## 3. Recommended architecture: see, then introduce

```
 WoW client + ClassicAPI                              Player's PC
 +-------------------------------+   pixels / file   +------------------------------------------------+
 | Scan (new)                    |  --------------> | client                                         |
 |  nameplate GUIDs -> players   |  frame BF2:      |  bridge -> local list of [name, distance]      |
 |  name, distance, enemy flag   |  self name,      |  directory of registered users <---------+     |
 | 2 Hz, local only              |  [name, dist]    |  intersect: only registered users stay    |     |
 +-------------------------------+                  |  declare to server: [hash, distance]      v     |
                                                    +--------------------------------- signalling server
                                                          WebRTC P2P once linked; gain follows distance
```

1. **Scan** (built as a diagnostic, `Scan.lua`): every 0.5 s the addon lists nearby players with distance.
   Nothing leaves the PC.
2. **Frame BF2** (built): the bridge frame carries the player's own character name and realm, and a list of
   `(name, distance)` for the roster names the game could see; the session id is made by the client.
3. **Directory** (built, with plain names instead of hashes): each client registers its character name and a
   session id. The hub publishes the realm's roster. The client gives the game only roster names, so
   **names of people who do not use EqVoice never reach the game's lookup, let alone the hub**.
4. **Link** (changed): the server links two clients when each lists the other **and their two measured
   distances agree** (see below).
5. **Connect, verify, unmute**: unchanged state machine and WebRTC. Before audio, the peers exchange a short
   proof over the data channel (section 4). Until it passes the outbound track is not attached.
6. **Volume follows distance**: `gain = 1` up to `near` yards (default 5), falling to 0 at `say_radius`
   (default 40, to be measured), updated 4 times a second with a short ramp. (This is the first design. The curve that runs now is in doc 06
   and doc 11: boosted up close, full to 30 yd, quiet at 70, gone at 85.) Binary mode (gain 1 inside the
   radius) is the same code with the curve flattened, and is what runs when only names are available.
7. **Leaving**: out of the list, or farther than `say_radius + 3` (hysteresis), starts the existing
   fade, grace and close.

What stays as it is: P2P WebRTC, the signalling server's shape, the per-peer state machine, the bridge
(pixels or file), the simulator, push-to-talk, everything in the client UI. What changes: the frame format,
the identity key (character-name hash instead of an addon-chosen session id), and the link rule.

## 4. Who is that, really? (anti-spoofing without addon messages)

Without an addon channel the link between "the player standing next to me" and "the peer that answered the
signalling" is no longer authenticated by the server. What replaces it:

| Attack | Defence |
|---|---|
| Remote user registers a victim's name | two registrations of one name are both marked suspect; the **distance check** below runs for every candidate |
| Remote user wants to be linked to someone | needs that person to list their name **and** a distance that matches. Both ends measure the *same* two objects, so a genuine pair reports nearly equal distances (the squared distance is symmetric); an impostor has to guess, repeatedly, while the real values drift. Rate limits and a "three consistent samples in a row" rule make guessing slow |
| Near-miss: impostor next to a real pair | they are in earshot anyway. Hearing a nearby player is exactly what `/say` allows |
| IP exposure before verification | the server does the distance check **before** it links, so a remote guesser rarely gets as far as a connection |
| Audio before verification | the outbound track stays detached until the data-channel proof matches (each side sends its own `UnitPosition`; the receiver compares it with the position it observes for the player it matched). Position shape differences are handled by checking the return count |
| Wholesale name harvesting | the server only ever receives names that are already registered, as hashes |

The strong version, a hidden whisper challenge ("prove you are this character by answering a nonce sent in a
whisper"), would need a way for the client to tell the addon to whisper (the bridge is one-way), and it
puts visible chat on the wire. **Not proposed for the MVP.** If abuse appears it is the next layer.

**Honest limits.** The hash of a name is not secret (names are guessable); it keeps casual harvesting out,
not a determined one. The server learns who is near whom among registered users, and distances between
them. Cross-faction pairs work the same way as same-faction pairs (no whisper involved); enemy nameplates
must be on for the scanner to see the other faction.

## 5. What has to be measured in the game

1. `/ev diag nearby` with friendly nameplates on and a player close by: which method is chosen, how many
   players it finds, whether distances appear, what `UnitPosition` returns, the nameplate reach CVars.
2. `/ev diag nearby watch` while you walk away from someone: the distance should rise smoothly; note the
   distance at which they disappear from the list (the nameplate reach).
3. `/ev diag name <player>` with nameplates **off**: does the by-name lookup still return a distance?
4. **The real `/say` range.** Have the other player `/say` a numbered line every few seconds while you walk
   straight away with `diag nearby watch` on; the last number you can read, against the last distance you
   saw, is the range. Repeat for `/yell` if you want a second radius.
5. Same with a crowd (a capital): `diag nearby` count and the framerate.

## 6. Option A, phase 2: look up registered names instead of scanning

If nameplate reach or the nameplate toggle turns out to be the weak spot, invert the flow: the client
sends the addon the names of *registered* users, and the addon asks `UnitTokenFromName(name, true)` and
`UnitDistanceSquared` for each. No nameplates needed, nothing about non-users is ever looked at. It needs a
channel **into** the game. The file API provides one: the client writes `CustomData\eqvoice-in.txt` and the
addon polls `ReadCustomFile` (then `ImportFile`). That makes the bridge two-way for the first time, and only for
users who have the file mods. **Built** (`02-protocol.md` section 1); not yet run in the game.

## 7. Option B: the smallest helper DLL (design only, not recommended yet)

Only if ClassicAPI proves unusable (not installed, nameplate dependence unacceptable, wrong results).

* **Scope**: exactly one Lua function, `EqVoiceNearby(maxYards)`, returning a Lua table of
  `{name, distance}` for player objects within the radius. No microphone, no audio, no WebRTC, no network,
  no files, no hooks on gameplay code, no writes to game memory.
* **How**: loaded through the loader already in use (`dlls.txt` / VanillaFixes); at load it registers the
  function with the client's Lua; the function walks the client's object list, keeps player objects, reads
  position through the object's own position virtual, subtracts the local player's, and builds the table.
  About 150-250 lines of C++.
* **Cost**: build-specific addresses for the client in use (the running client reports build 7272, the exe
  says 5875: both would need confirming), a closed-source-DLL trust question for players, and exposure to
  whatever the server does about memory readers. ClassicAPI and SuperWoW already do the same reading, so the
  ecosystem accepts it.
* **Better than writing our own**: ClassicAPI is open source and already contains the object-hash walker
  that `GetNamePlateGUIDs` uses. A small contribution there ("expose the walker as
  `GetNearbyUnits(typeMask, maxYards)` without requiring nameplates") would give every addon the capability
  and spare EqVoice a DLL. That is a request to its maintainer, not something I can do from here.

## 8. Option C: per-zone chat channel plus coordinate broadcasts (analysis only)

Mechanics: every client joins a hidden custom channel per zone, sends heartbeats with name, session id and
position as ordinary chat (`SendChatMessage(..., "CHANNEL", nil, n)`), receives them as `CHAT_MSG_CHANNEL`,
hides the lines from the chat frames, and compares positions. This is the earlier proposal; addon-message
`CHANNEL` was proved dead on this client, so it would be real chat.

* It still needs a position source: `UnitPosition` from the same extensions, or the coarse map position
  (group mode's 1.5%-of-a-zone resolution, too coarse for `/say` range). So it does **not** avoid the
  extensions; it adds broadcasting on top of them.
* Everyone in the zone channel, including anyone who types `/join` and reads it, receives every player's name
  and position every few seconds. Privacy is the worst of the three.
* It is real chat to the server: flood control, anti-spam, mute rules and the per-character channel limit all
  apply. Heartbeats from 100 players in a capital are about 25 messages a second into one channel.
* The hidden-channel trick needs chat-frame hooks that other addons also hook.

## 9. Comparison

| | **A. Existing extensions** | **B. Helper DLL** | **C. Zone channel + broadcasts** |
|---|---|---|---|
| Feasibility | High: documented, present in your DLLs. In-game confirmation pending | Medium: new build-specific native code | Medium: works only if the server tolerates it |
| Strangers, exact range | Yes, in yards, up to the nameplate reach (by-name lookup: no reach limit) | Yes, in yards, independent of nameplates | Only as exact as the position broadcast; needs A's APIs anyway |
| Privacy | Best: discovery is local; only registered users' hashed names reach the server | Same as A | Worst: names and positions to the whole zone and to anyone who joins |
| Reliability | Depends on friendly nameplates being on and their reach (fixable by phase 2) | High | Medium: flood limits, mutes, channel join limits, hooks |
| Performance | Native calls, a 2 Hz Lua loop over tens of units | Native, negligible | n-squared chat traffic |
| Works on this 1.12.1 client | Yes, with ClassicAPI (SuperWoW partial, bare client names only) | Yes, per build | Yes |
| Implementation complexity | Low-medium: new frame, directory, link rule, gain curve | High | Medium, plus moderation of a public channel |
| Acceptable as a fan mod | High: users already run these mods; no new binary | Low-medium: one more DLL to trust | Low: visible spam and a privacy regression |

**Recommendation: A.** Confirm it with `/ev diag nearby`, then build the pieces in section 3. B stays a
contingency and is better pursued as a contribution to ClassicAPI than as our own DLL. C only if both fail.

## 10. What was built once the test passed (all done except the proof; see the status note at the top)

In this order: BF2 frame (Lua and Python, vectors extended), directory and hashed registration in the
signalling server, the distance-consistent link rule, the data-channel proof and detached outbound track,
the gain curve with hysteresis, then the simulator's "walk toward each other" scenario to exercise the
curve, and finally the real-game checks in `03-onwow-test-plan.md`.
