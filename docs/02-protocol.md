# 02 — Protocol reference

Four wire formats, all versioned and small. The narrative (why, flow, state machine, fail-closed rules) is in
`06-roster-pipeline.md`; this file is the exact reference.

| Format | Direction | Where it lives | Pinned by |
|---|---|---|---|
| roster file `OVI1` | client → addon | `CustomData\eqvoice-in.txt` | `eqvoice-client/tests/test_inbox.py` (runs the real Lua parser) |
| bridge frame `BF2` | addon → client | `CustomData\eqvoice.txt` (`OVB2 <hex>`), plus a truncated optical copy | `protocol/bridge_vectors.json`, generated from the Lua encoder |
| signalling `v2` | client ↔ hub | JSON over WebSocket `/ws` | `protocol/conformance.json`, recorded from the Python hub |
| group heartbeat `OV1` | addon ↔ addon | `SendAddonMessage`, **off by default** | `eqvoice-addon/tests/run.lua` |

Constants that must agree live in one place each: `EqVoice/Frame.lua` and `eqvoice_client/bridge/frame.py`
(bridge), `EqVoice/Inbox.lua` and `eqvoice_client/bridge/inbox.py` (roster file), `eqvoice_signalling/hub.py`
and `eqvoice-signalling-cf/src/hub.js` (signalling).

## 1. Roster file `OVI1` (client → addon)

```
OVI1 <seq> <unix-ts> <crc> <character>\n
C radius=40 interval=0.5 report=60 checks=80\n          optional, space-separated key=value numbers
R name,name,name,…\n                                      25 names per line, any number of lines
T free text\n                                             optional; the addon logs it
```

| Field | Rule |
|---|---|
| `seq` | 1-65535, changes on every write (wraps to 1). Unchanged for 15 s = the client is gone |
| `unix-ts` | seconds, informational |
| `crc` | CRC-16/CCITT-FALSE over every byte after the first newline (CR removed), 4 upper-case hex digits |
| `character` | the character the client believes is playing; the addon ignores the file unless it equals the logged-in character |
| `C` keys the addon reads | `interval` 0.2-5 s between scans, `checks` 1-2000 names per scan, `report` 5-200 yd (`radius` is informational) |
| `R` names | 2-24 bytes, no control characters or spaces, duplicates dropped, at most 2000 names |

File name: the addon reads `eqvoice-in-<Character>.txt` first (when its name is plain letters) and `eqvoice-in.txt` otherwise;
a client started with `--character NAME` writes the former. Written to a temp file then renamed over the target (retried briefly if the game holds it). The addon rereads
the file about once a second (`ReadCustomFile`, then `ImportFile`), ignores bad CRCs (keeps the previous roster), and
treats a roster as fresh for 15 s after `seq` last changed.

## 2. Bridge frame `BF2` (addon → client)

```
byte 0      version = 2
bytes 1-2   counter (u16, big endian), +1 per publish, wraps
bytes 3-4   payload length N (u16)
payload     N bytes, below
last 2      CRC-16/CCITT-FALSE over everything before it
```

Payload:

| Offset | Size | Field |
|---|---|---|
| 0 | 1 | flags: 1 enabled, 2 file bridge on, 4 optical bridge on, 8 roster fresh, 16 logging out, 32 lookups available, 64 roster file was for another character, 128 results truncated |
| 1 | 4 | login id (random per game session) |
| 5 | 4 | wall clock, unix seconds |
| 9 | 2 | roster ack: the `seq` of the roster file the addon is using |
| 11 | 2 | roster size |
| 13, 14, 15 | 1 each | names checked, found loaded, not loaded, in the last scan (saturating at 255) |
| 16 | 2 | scan interval, ms (measured) |
| 18 | 2 | scan cost, tenths of a millisecond |
| 20 | 1 + ≤24 | character name (length, bytes) |
| … | 1 + ≤32 | realm name |
| … | 1 | result count (≤200 in the file copy) |
| … | per result | name (length, bytes ≤24), distance (1 byte, half yards, 254 = 127 yd or more), flags (1 player, 2 enemy faction) |

File form: the first line is `OVB2 <upper-case hex of the bytes>`. It may be followed by up to 16 trace lines,
`OVT <seq> <unix-seconds> <text>`: the addon's own narration of what it just did (the desktop client logs each `seq` once, per
login id, as `TRACE addon <text>`). The file is written twice: `eqvoice.txt`, and `eqvoice-<Character>.txt` when the name is
2-24 plain ASCII letters, so two game instances sharing one folder do not collide; the desktop client started with
`--character NAME` reads only the latter. Names in frames never contain bytes below 33, 127 or a comma.

Optical form: the same bytes (cut to 234, keeping the nearest results and setting the *truncated* flag) as a
grid of 32 × 10 solid colour cells in a screen corner, 6 bits per cell (two bits per channel at 0, 85, 170, 255),
packed most significant bit first from the 9th cell. Cells 1-8 are an anchor: the eight corners of the RGB cube.
The reader finds the anchor, measures the cell size, samples centres, and tolerates fractional UI scale, gamma
and brightness drift of about ±40 per channel.

**Liveness.** The addon publishes whenever something changed and at least once a second; the counter moves every
time. The client treats an unchanged `(login id, counter)` as no news: *live* for 4 s, *stale* up to 8 s (last
readings held), then *lost* (nobody audible).

## 3. Signalling `v2` (client ↔ hub)

JSON text frames, one WebSocket (`/ws`). Binary frames, malformed JSON (including `NaN`) and messages over 16 KB
are refused. Any client message may carry `id` (integer or string); errors it causes echo it as `ref`.

### Hub → client

| `t` | Fields | When |
|---|---|---|
| `hello` | `v: 2`, `nonce` (16 bytes, base64url), `now`, `join_code` (bool), `ice` (RTCIceServer list), `limits {idle, report_ttl, link_radius, max_report}` | on connect |
| `registered` | `sid`, `token`, `lease`, `now`, `roster: [[name, sid]]` (≤300), `more` (entries still to come as `roster_update`), `id?` | registration accepted |
| `roster_update` | `add: [[name, sid]]` or `remove: [sid]` | someone of the same realm came or went (and the rest of a large roster) |
| `peer_available` | `sid`, `character`, `distance` | mutual proximity confirmed (0.1 yd) |
| `peer_out_of_range` | `sid`, `character` | it ended (after the grace), or the peer disconnected |
| `offer` `answer` `ice` `bye` | `from`, `ep`, `data` | relayed from a linked peer; `from` is stamped by the hub |
| `pong` | `now`, `ts?` | reply to `ping` |
| `error` | `code`, `msg`, `ref?` | |

### Client → hub

| `t` | Fields | Notes |
|---|---|---|
| `register` | `v: 2`, `sid`, `realm`, `character`, `pub`, `sig`, `code?`, `token?` | `sid` is 22 base64url characters (16 random bytes); `realm` 1-32 chars of `A-Za-z0-9 '.-`; `character` 2-12 Unicode letters; `pub` an Ed25519 public key (32 bytes); `sig` signs `"OV2-REG\0" nonce "\0" sid "\0" realm "\0" character` (UTF-8); `token` is the one the hub issued, to resume after a drop; one registration per connection |
| `proximity` | `peers: [[name, yards, ts], …]` | the complete current set (≤64); an empty list withdraws everything; yards 0-500; the sender's own name is ignored; replaces the previous set |
| `offer` `answer` `ice` `bye` | `to` (sid), `ep` (int 0 to 2^31-1), `data` (≤8 KB JSON) | delivered only while linked |
| `ping` | `ts?` | every 10 s |
| `unregister` | | leaves now; frees the name immediately (a plain disconnect reserves it for 30 s) |

### Rules

* **Mutual proximity.** Link A–B ⇔ A reports B and B reports A, both reports at most 6 s old, `|dA − dB| ≤ 12`,
  `min(dA, dB) ≤ 90`. Evaluated whenever a report arrives, a session registers, and on `tick()`. It survives 3 s
  without holding. The shared distance is `round1((dA + dB) / 2)` where `round1(x) = floor(x · 10 + 0.5) / 10`.
* **Relay.** `offer/answer/ice/bye` to anyone not linked answer `not_linked` (same text for offline and
  not-near); at most 120 per minute per link.
* **Registration.** Checks in order: version, per-address rate, field formats, join code, signature, then the
  state rules: a live holder with another key → `name_in_use`; the same key → takes over (old socket closed `4409`);
  a session id held by another name → `sid_taken`; a reservation (30 s after a drop) needs the same key and token.
* **Order of events** is deterministic (insertion order everywhere); that is what lets two implementations
  produce identical transcripts.

### Error codes

`bad_json`, `bad_msg`, `too_big`, `too_many`, `rate_limited`, `unsupported_version`, `not_registered`,
`already_registered`, `bad_sid`, `bad_realm`, `bad_name`, `bad_sig`, `bad_code`, `name_in_use`, `sid_taken`,
`realm_full`, `ip_rate`, `not_linked`, `register_timeout`.

### Limits (defaults; `Limits` in `hub.py`)

| | |
|---|---|
| message size | 16 KB; signal `data` 8 KB |
| rate | 100-message burst, 50/s; proximity 8 burst, 4/s; 5 rate-limit strikes in a minute → close `1008` |
| timeouts | 10 s to register (`4408`), 30 s idle (`4408`) |
| addresses | 20 connections, 12 registrations a minute |
| realms | 5000 sessions each; roster chunks of 300 |
| link | 6 s report TTL, 3 s leave grace, 12 yd tolerance, 90 yd radius |

### Signal payloads (`data`)

| kind | data |
|---|---|
| `offer`, `answer` | `{"sdp": "<session description>"}` |
| `ice` | `{"candidate": {<RTCIceCandidateInit>}}` |
| `bye` | `{}` |

`ep` is the connection attempt number chosen by the initiator (the lower session id). Anything older than the
current attempt is dropped.

## 4. Group heartbeat `OV1` (test harness only; `/ev transport group`)

`SendAddonMessage("OV1", text, "GUILD"|"PARTY"|"RAID")`; SAY, YELL, WHISPER, OFFICER and CHANNEL are refused by
this client (`Unknown addon chat type`). Text is `[A-Za-z0-9:.]`, ≤80 bytes, **never `|`** (the chat escape
character). Fields: `H:<sid>:<seq>:<iv>:<flags>:<zone>:<x>:<y>` and `B:<sid>:<seq>`. The harness is off by
default and does not feed the roster pipeline. Details are in `Proto.lua` and `Nearby.lua`; it is kept to test the
client when no ClassicAPI is installed.

## 5. Versioning

Each format carries its version in its first field. A change that old parsers cannot read bumps it
(`OVI1` → `OVI2`, `BF2` → `BF3`, `v: 2` → `v: 3`): old and new then ignore each other instead of misparsing. The
hub answers a wrong `v` with `unsupported_version`, and the client refuses a hub that says another `v` in `hello`.
