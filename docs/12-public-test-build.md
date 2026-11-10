# EqVoice Test Build 0.3.2: what it is and what it is not

Bottom line: the prototype is now a one-file installer, a tray-only background program and an in-game panel. Everything was tested in simulation and
in the test suite; **none of the new in-game UI has been looked at in the real game yet**, and **no voice server is deployed**, so no stranger can
connect until one is.

## Architecture (unchanged where it worked)

- **Addon** (`eqvoice-addon/EqVoice`, Lua 5.0 on the 1.12 client): draws the icons and the panel, reads the position of nearby players, talks to the
  program through two small text files in the game's `CustomData` folder (status: program to addon, with a CRC; control: addon to program, last 8
  commands per write).
- **Program** (`EqVoice.exe`, Python): lives in the system tray. Watches the files, keeps the connection to the voice server, runs the voice
  engine on demand (starts when a character is in game, stops 45 s after the last one leaves).
- **Voice engine**: a hidden Edge page doing WebRTC and Web Audio. Hidden by `ShowWindow(SW_HIDE)`; its 20 ms timer was measured at 50 Hz while hidden.
- **Voice server (hub)**: finds peers and relays signalling only; audio is peer to peer (Opus). Python version and a Cloudflare Worker version exist.
  **Neither is deployed.** `eqvoice-signalling-cf/DEPLOY.md` does it and needs the project owner's Cloudflare account.

## What exists, what is partial, what is missing

| Wish | State |
|---|---|
| Proximity voice with a distance curve | exists (proven in game, 2 characters and an internet friend) |
| One-click installer, upgrade, uninstall | **new**: `EqVoice-Test-Setup-0.3.2.exe` (built; tested in a scratch folder and registry key, not on a real PC) |
| All settings in game, nothing on the desktop | **new**: no window at all; settings live in the addon (`EqVoiceDB.settings`) and are replayed to the program when it appears |
| `/eqvoice`, `/eqvc`, click on the mic icon: one panel | exists, same code path |
| First-run wizard | **new** (6 pages, never repeats, dismissible); unit-tested only |
| Tray only, no taskbar button, no throttling | **new**; tray menu: status, restart, open logs, exit |
| No chat spam; rotating logs | **new**: chat is silent unless `/ev trace on`; logs rotate; the build id is on the first line |
| Own mic icon states (ready, talking, muted, deafened, disconnected, unavailable) | exists, extended; click opens the panel |
| Other players' icon right of the nameplate: `vol_low`, `vol_mid`, `vol_high` | **new** mapping, see below |
| Loud speech extends range | exists (raised voice), see below |
| Device pickers with live level and test sound | **new** (panel and wizard) |
| Group, guild, raid channels | **missing**: proximity only. Not built. |
| Code signing | **missing**: SmartScreen shows "unknown publisher" once |

## Icon mapping (other players)

Received voice level, smoothed, with hysteresis (`voicemodel.js`, `VoiceActivity`):

| Icon | Meaning | Rule |
|---|---|---|
| `vol_low` | known voice user, silent | no speech |
| `vol_mid` | speaking normally | level above speak threshold |
| `vol_high` | loud | level above loud threshold for 1.0 s minimum hold; drops only after 0.5 s below |

Without the hold times the icon flickered in the real browser mid-shout; the end-to-end test now expects the order idle, speaking, loud, speaking.

## Loud speech and range

Normal speech: full volume to 30 yd, then falls as (1-t)^1.6 to zero at 85 yd. Raised voice: full volume to 45 yd. The boost is at most x2 and
eases down to nothing inside 10 yd of the speaker, and the range never exceeds the clamp, so shouting cannot carry across a zone.

## Installer

`python tools/build_installer.py --hub-url wss://.../ws [--join-code CODE]` writes `dist/EqVoice-Test-Setup-0.3.2.exe` (about 25 MB). It needs
the hub address; `--allow-no-hub` builds one that cannot connect, for trying the installer.

The player double-clicks, picks the game folder only if it cannot be found, and is done. It copies the program to `%LOCALAPPDATA%\Programs\EqVoice`,
the addon into the game, writes a manifest, registers Settings > Apps and start-with-Windows, and makes Start-menu shortcuts. An upgrade stops the old
program first and keeps settings. A game folder without `nampower.dll` and `ClassicAPI.dll` (loaded by `dlls.txt`) gets a plain-words warning.
Uninstall (`EqVoice.exe --uninstall`) removes the addon, its bridge files, shortcuts and registry entries, asks whether to keep settings and logs,
and deletes its own folder after exiting.

Scratch-test switches: `EQVOICE_SETUP_SILENT`, `_ROOT`, `_WOW`, `_REGBASE`, `_NO_LAUNCH`, `_NO_SHORTCUTS`, `_DATA`.

## The in-game panel (redesigned 2026-11-01)

Opaque charcoal window, flat compact controls, one purple accent, four groups: a header (status, Mute, Deafen), **Microphone**, **Speaker**,
**Nearby** (with Blocklist). No gold, no red templates; the window drags anywhere and keeps its saved position.

- **Microphone**: input device, input volume, Open mic / Push to talk, Keybind, Set up mic, the sensitivity bar, Boost. Only what EqVoice really
  supports is shown. Noise suppression and echo cancellation are always on in the engine and have no switch, so they are a one-line note, not a toggle.
- **One sensitivity bar** (`Panel.NewGateBar`), as Discord's: the live microphone level moves along a dB track (-90 to -10); the handle is the
  noise gate's bar; below it the track is amber, above it green (dim), and the live fill is the bright version of each. The edge turns green and the line
  under it says "Gate open: your voice is being sent." when the gate opens. Auto / Manual / Off pick the mode: in Auto the handle shows the room-following
  bar and cannot be dragged; Manual lets you drag the handle (or click the track), -80 to -20 dB in 2 dB steps; Off draws the level with no handle.
- **Same signal as the gate.** The level is `gl`, taken from the keyboard filter's own measure (200 Hz to 3.5 kHz, after the input volume, before the gate), so
  it keeps moving while the gate is shut. `gt` is the bar in force, `go` is 1 while the gate is open; all three are sent only while a meter is on screen.
  Without a gate the broadband level is drawn on the same track. Push to talk is respected: gate open with the key up says "Voice heard. Hold F9 to talk."
  A real-browser test (`test_the_panels_one_bar_shows_the_signal_the_gate_judges_even_while_the_gate_blocks_it`) measured -24 dB behind a shut gate.
- **Speaker**: output device, output volume, Test speakers. **Nearby**: players in range with their voice icons (click a name to mute), an empty state, Blocklist.
- The setup wizard, device picker, blocklist and mic-setup windows share the same look.

Not seen in the real game yet: the layout was checked in the test stub (no control overlaps or leaves the window), not on screen.

## Hardening results

- Full suite: 559 pass, 4 skipped (after fixing two stale tests that expected the old mod check and an instant game ident).
- `scale_bench.py`: 1000 players in the roster cost 0.36 ms per client tick and 7 KB per roster file; the addon frame stays under 1 ms.
- `mesh_bench.py` (real hidden browsers): 3 and 4 engines on one PC run at 11 to 30% CPU and about 250 to 470 MB in total.
- Idle: with no character in game the engine is stopped and the program only watches a file.

## Not yet verified

1. Any of the new UI in the real game (panel layout, wizard, device lists, icon art positions).
2. The setup exe on a PC other than this one, and with SmartScreen.
3. 3 or more people at once (`10-step-by-step-test-guide.md`, Phase C).
4. `tools/smoke_tray.py` is stale for 0.3.2 and needs rewriting against a simulated game.
