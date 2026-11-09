# 00 — Feasibility and assumption audit

Status: written 2026-10-14, before any code. Every claim carries a confidence
label: **[verified]** I read it in a file or source on this machine or in
upstream source, **[doc]** a published page says it, **[unknown]** nobody on
this project has seen it work, test it.

> **Update, 2026-10-15 (later).** This audit is kept as written: it records how the design got here. The brief's
> SAY idea is dead, and the plan that replaced it is built: a roster of online characters from the hub, looked up by
> name in the game with exact distances, mutual-proximity confirmation, then peer-to-peer voice. Read
> `06-roster-pipeline.md` for what exists now. Several statements below ("a group/guild fallback already exists",
> "section 9 of `01-architecture.md`") describe the earlier shape.

## Bottom line

1. **The core assumption — "addon messages sent over SAY reach nearby players" —
   is false on this client. Confirmed in game on 2026-10-15: the client answers
   `Unknown addon chat type` for both SAY and YELL.** SAY and YELL as addon-message
   types were added in Classic Era *1.13.3*, not in 1.12, and at least one real
   1.12 server core (VMaNGOS) drops them too. (The rest of this document was
   written before that test; the tables below keep the original reasoning, and the
   two "in-game run" sections record what the game said.)
2. **Everything downstream of that assumption can be built and tested without
   WoW**, so it is built behind two swappable seams: a *proximity transport* in
   the addon and a *bridge source* in the client. If SAY works, you flip one
   setting. If it does not, a group/guild-scoped fallback already exists.
3. **A no-DLL addon → desktop bridge exists**: the addon paints a small block of
   colour cells and the client reads the pixels. It is real time, needs no mods,
   and has real limits (WoW must be visible on screen). If the client already
   runs Nampower ≥ 3.2 or SuperWoW — this install does — a file bridge is better
   and is auto-selected.
4. **No DLL is required by this design.** A DLL is only the last-resort fallback
   (section 9 of `01-architecture.md`).

## First in-game run (2026-10-15)

`/ev status` and `/ev diag api` ran on the real client. What they showed, and what they broke:

| Observation | Meaning |
|---|---|
| `GetBuildInfo()` says **1.18.1 build 7272** (Mar 20 2026), `_VERSION` is `nil` | the exe's file version (1.12.1.5875) is the base; the running client is OctoWoW's own build. Still Lua 5.0-style (no `_VERSION`, no `C_ChatInfo`) |
| `SendAddonMessage` present; `RegisterAddonMessagePrefix` and `C_ChatInfo` absent | as predicted: a 1.12-style addon API |
| Present: `WriteCustomFile`, `ReadCustomFile`, `CustomFileExists`, `ExportFile`, `ImportFile`, `UnitXP`, `UnitPosition`, `UnitNameplate`, `LoggingChat`, `GetPlayerMapPosition`, `SetMapToCurrentZone` | the file bridge's APIs exist; `UnitXP("nop","nop")` runs |
| `gxWindow=1`, `gxResolution=3440x1440`, `useUiScale=1` | windowed, ultrawide: the optical bridge's capture case |
| Addon loaded, session id issued, bridge painted 56 frames and made 56 file-API calls | the bridge side runs; **whether the file appears on disk and whether the client reads the pixels is still untested** |
| Every send failed: `Invalid escape code in chat message` | **bug in EqVoice, not a verdict on SAY.** `|` is WoW's chat escape character and the client rejects a message containing a `|` that is not a valid escape. The first wire format used `|` as its separator, so **every** message, on every channel, was refused. The SAY probe's `ERROR` was this, not SAY. Fixed: the separator is now `:`, and the test stub now rejects bad escapes the way the client does |
| `Comm.lua:39: attempt to perform arithmetic on field '?' (a nil value)` | the rate limiter's `table.remove` ring misbehaved on the real Lua 5.0. Replaced by a fixed ring written by plain index |

## Second in-game run (2026-10-15, after the fixes): SAY and YELL are dead

```
/ev status        sent 12  heard 12 (malformed 0)  errors 0  throttled 0     (heard = my own GUILD heartbeats)
/ev diag say      ERROR ... Unknown addon chat type
/ev diag yell     ERROR ... Unknown addon chat type
/ev diag guild    LOOPBACK 3/3  avg 271 ms  sender=<my character>  dist=GUILD
```

* **The client itself refuses `"SAY"` and `"YELL"`** as addon chat types, before any packet is built. The
  server is never reached, so its configuration is irrelevant. The primary design of the brief, addon
  heartbeats over SAY, **cannot work on this client** without a client-side change.
* GUILD works: the server returns the message, with the sender name filled in. The new wire format,
  rate limiter and receive path ran 12 times with no errors. Round trip 271 ms.
* The error text comes through `aux-addon`'s `ChatThrottleLib`, which wraps `SendAddonMessage`; it is the
  client's message, relayed.

### `/ev diag all` (same day)

| Type | Result |
|---|---|
| SAY, YELL | `Unknown addon chat type` |
| OFFICER, WHISPER, CHANNEL | `Unknown addon chat type` |
| BATTLEGROUND | accepted by the client (no echo: not in a battleground) |
| GUILD (earlier) | LOOPBACK 3/3 |

**This client's addon chat types are exactly the original 1.12 set: PARTY, RAID, GUILD, BATTLEGROUND.**
No addon message can reach a stranger: not by SAY, not by whisper, not by a shared channel. Any route to
strangers has to leave the addon-message API (see "Fallbacks" in `01-architecture.md`).

## 1. What the client actually is

| Fact | Evidence | Label |
|---|---|---|
| `WoW.exe` file version 1.12.1.5875 | `D:\Octowow\WoW.exe` version resource | [verified] |
| Lua is 5.0 (no `#`, `%`, `string.match`, `select`, `...` expressions) | existing project notes + client behaviour | [verified] |
| Mods loaded: VanillaFixes, SuperWoWhook, nampower, UnitXP_SP3, ClassicAPI, VanillaHelpers, others | `D:\Octowow\dlls.txt` | [verified] |
| This is **not** a Classic Era 1.13+/1.14/1.15 client, so `C_ChatInfo.*`, `RegisterAddonMessagePrefix`, and addon-message SAY/YELL are not part of its base API | `warcraft.wiki.gg/wiki/API_SendAddonMessage` history | [doc] |
| "Classic 1.18.x" therefore describes server/content patch numbering, not a client API level | inference | [unknown] |

The addon must be written for Lua 5.0 and must probe for every API instead of
assuming it. `tools/lint_lua50.py` enforces the syntax half of that.

## 2. SAY / YELL addon messages

| Claim | Evidence | Label |
|---|---|---|
| `SendAddonMessage` was added in 1.12.0 | wiki patch history | [doc] |
| Prefix registration (`RegisterAddonMessagePrefix`) arrived in 4.1.0; 1.12 has none, any addon sees any prefix | wiki patch history | [doc] |
| SAY and YELL were added as addon-message types in Classic Era **1.13.3 (2019-12-10)** | wiki patch history | [doc] |
| Classic then "heavily throttles" SAY/YELL addon traffic and mangles some characters | wiki page | [doc] |
| Every addon in this install that sends addon messages uses only RAID, PARTY or GUILD (DoiteAuras, BigWigs, ECO, Turtle LFT and Titles in the extracted FrameXML) | grep over `Interface\AddOns` and `.eco-work\extracted` | [verified] |
| VMaNGOS `WorldSession::IsLanguageAllowedForChatType` permits `LANG_ADDON` only on PARTY, GUILD, OFFICER, RAID (+leader, warning), BATTLEGROUND (+leader) and CHANNEL; any other type is logged and dropped | `vmangos/core` `ChatHandler.cpp` lines 78-104 | [verified for VMaNGOS] |
| Whether OctoWoW's core is VMaNGOS-like, and whether its 1.12.1 client accepts the string `"SAY"` in `SendAddonMessage` | nothing | [unknown] |

Reading: there are two independent places SAY can die — the client may refuse
the type (so nothing is sent) and the server may drop the packet. Both have to
pass. The diagnostic tests each separately:

* **Client accepts it?** `pcall(SendAddonMessage, ...)` returns without error.
* **Server relays it?** the sender receives its own `CHAT_MSG_ADDON` back with
  distribution `SAY` (self-loopback, single client, no second player needed).
* **Reaches others?** a second client in range receives it (two-client test).

## 3. Server-side behaviour to expect (VMaNGOS source, one candidate core)

These are what the *reference* 1.12 core does. OctoWoW may differ. [verified for
VMaNGOS, unknown for OctoWoW]

* Addon-language chat bypasses flood control, language checks and content
  sanitising. The whole addon channel can be disabled by the operator
  (`AddonChannel`, default on).
* `/say` is delivered only to **living** players; ghosts do not reach the living.
* `/say` range is `min(ListenRange.Say, level-scaled yell range)`. Shipped conf:
  Say 40 yd, Yell 300 yd. Low-level characters can have a shorter say range.
* `Player::Say` does not apply the "own team only" flag, so say reaches both
  factions; `TextEmote` does apply it. If SAY addon messages were allowed, they
  would likely cross factions. [unknown for OctoWoW]
* Operators can enforce a minimum level to use `/say`.
* No layering or phasing exists in 1.12. Instances are separate maps and SAY
  stays inside the map. [unknown whether OctoWoW adds custom phasing]

Things that need an empirical answer on OctoWoW (all in `03-onwow-test-plan.md`):
cross-faction, instances, battlegrounds, ghosts, druid forms, polymorph/mind
control, crowded cities, effective range in yards, throttle thresholds.

## 4. Throttling

* Classic Era (1.13.4+) lets each prefix send 10 messages then refills 1 per
  second [doc]. That is a modern-client figure; **1.12 has no documented
  client-side throttle** [doc]. Limits are server-side and unknown.
* `aux-addon` ships `ChatThrottleLib` for 1.12 because bursts can get a player
  disconnected for flooding [verified: file present].
* Design response: one heartbeat per ~4 s, a hard token bucket of 3 messages /
  6 s, a 30/minute ceiling, one pending message at most, interval *grows* with
  crowd size, and payload restricted to `[A-Za-z0-9:.]` (safe against
  character mangling). That is ≈ 0.25 msg/s, far below any figure above.
* `/ev diag rate` measures the real limit instead of guessing.

## 5. Proximity transports considered

| # | Transport | Reaches strangers | Works on 1.12.1 today | Verdict |
|---|---|---|---|---|
| T1 | SAY addon heartbeat | yes (the goal) | **no: the client refuses the type** | dead on this client |
| T2 | YELL addon heartbeat | yes, 300 yd | same as T1 | useful for the range test only |
| T3 | GUILD / PARTY / RAID addon heartbeat + coarse map position | **no**, members only | yes (documented channels, server allows `LANG_ADDON`) | built as the fallback ("group mode") |
| T4 | Custom channel addon messages | worldwide, not local | disabled on Classic, allowed by VMaNGOS | rejected: not proximity, abuse magnet |
| T5 | Hidden whispers | one target | works, but visible to server/anti-spam | rejected for heartbeats; kept as an idea for name-ownership proof |
| T6 | Nameplate scan (`WorldFrame:GetChildren()`) → nearby names | yes | yes, if friendly nameplates are on | future "presence source"; gives names, not voice identities |
| T7 | SuperWoW `UnitNameplate` / `GetName(1)` GUID + `UnitPosition` + UnitXP distance | yes, with yards | only with those mods | future enhancement: real distance falloff |

T6/T7 answer "who is near me" but not "which voice peer is that player", which
is the identity-binding problem. A heartbeat from an authenticated sender name
solves that for free; without one you need a proof of character ownership.
That is why T1/T3 are built first.

## 6. The addon → desktop bridge

| Mechanism | Real time | Needs mods | Verdict |
|---|---|---|---|
| SavedVariables | **no** — the client writes them at logout / `ReloadUI` only [doc, standard 1.12 behaviour] | no | diagnostics dump only |
| Chat log (`LoggingChat`) | maybe | no | needs the addon's own text to appear in the chat log; only real chat events are logged, so it would need a self-echo channel. Rejected: server round trip, log growth, unknown flush timing |
| **Pixel / optical** | yes (every rendered frame) | no | **default**; WoW must be visible, windowed or borderless |
| **File via mod API** (`WriteCustomFile`, `ExportFile`) | yes | Nampower ≥ 3.2 or SuperWoW | **auto-selected when present**; works with WoW minimised |
| Screenshot API | no | no | rejected (disk churn, rate limited) |
| Packet sniffing | — | driver | rejected (header encryption, needs session key) |
| Memory reading / injected DLL | yes | yes | forbidden by the brief; fallback only |

Evidence for the file API: `NampowerAPI.lua` in the installed
SuperCleveRoidMacros documents `WriteCustomFile(filename, content, [mode])`
("CustomData directory"), `ImportFile` / `ExportFile` ("Imports directory,
.txt appended automatically", "backwards compatibility with SuperWoW") from
Nampower 3.2; the SuperWoW wiki `Features.md` documents the same two functions
for `gamedirectory\imports`. [verified as documentation, unknown as behaviour:
nothing here has been run in the client, and `nampower.dll` carries no version
resource, so the addon probes with `type(WriteCustomFile) == "function"`.]

The optical bridge is the one non-obvious part, so its limits are stated up
front:

* Reads pixels with GDI `BitBlt` of the WoW client area. Works for windowed and
  borderless windows. Exclusive fullscreen can return black.
* If WoW is minimised or covered by another window at the top-left corner, the
  bridge goes stale. The client holds the last known set for 20 s, then **fails
  closed** (fades everyone out). It never leaves a mic open on stale data.
* `Alt+Z` (hide UI) hides the bridge frame; same stale behaviour.
* It is passive: no injection, no memory access, no input synthesis.

## 7. The test that decides the design

In game, on a throwaway character, with the addon installed:

```
/ev diag api       what exists on this client
/ev diag say       does SAY send, and does it loop back to me?
/ev diag yell
/ev diag group     the same for PARTY / RAID / GUILD (known-good control)
```

* **say = LOOPBACK** → run the two-client range test; if that passes, set
  `/ev transport say` and the primary design is live.
* **say = NO ECHO or ERROR** → stay on group mode (`/ev transport group`, the
  default `AUTO` already does this) and treat stranger proximity as needing
  either OctoWoW server support or the T6/T7 presence sources.

## 8. Verdict on each assumption in the brief

| Assumption | Verdict |
|---|---|
| Addon SAY messages reach only nearby players | **false here**: the client refuses SAY and YELL (tested in game) |
| Recipients must register a prefix | not on 1.12 (no such API); the addon guards the call and works either way |
| Receiving a heartbeat is a binary proximity signal | yes **if** a local transport exists; with group transports it needs a location filter |
| Heartbeat every 2-5 s, expiry 6-12 s | kept: 4 s default, expiry 3 intervals + 1 s, interval carried in the message so the receiver adapts |
| Works across factions/instances/BGs | unknown; test matrix in `03-onwow-test-plan.md` |
| Arbitrary non-grouped players can exchange addon SAY | no (see above). GUILD works but needs membership; `/ev diag channel` tests the one remaining route |
| SavedVariables might be a live bridge | no |
| A bridge needs a DLL | no: pixel bridge, or file bridge through mods already installed |
| WebRTC P2P with a tiny signalling server | yes; verified here with two and with four real Chromium instances (`03-onwow-test-plan.md`, part A) |
