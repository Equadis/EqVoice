# EqVoice Test Build 0.4.1: what it is and what it is not

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
| One-click installer, upgrade, uninstall | **new**: `EqVoice-Test-Setup-0.4.1.exe` (built; tested in a scratch folder and registry key, not on a real PC) |
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

`python tools/build_installer.py --hub-url wss://.../ws [--join-code CODE]` writes `dist/EqVoice-Test-Setup-0.4.1.exe` (about 25 MB). It needs
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

## 0.7.1 (2026-11, pre-release)

**Overhead icons On hid the ally's nameplate** (a screenshot pair: the plate was there with the icons Off and gone with them On, and there was no icon either). The suspect is the client's friendly-nameplate switch, which EqVoice
called whenever a voice player was near: on this client it seems to act as a toggle, so calling it while the player's own friendly plates were already on switched them off. Now: the switch is only used when a voice player has
been within 25 yd for 3 s and NO plate of any voice player is on screen, and then only once while voice players stay near (and put back after); if a voice player's plate is showing it is never touched.

**The icon appears whatever draws the plate.** The Equadis overhaul hides the plates it does not draw and its own name text is then empty, so no icon was found for them; the client's own name text (which holds the unit's name
either way) is read now, and the icon is placed above where that name is.

## 0.7.0 (2026-11, pre-release)

The overhead icon is on a small **steel-blue tile**, 4 px above the plate's name, on its own frame (the plate is only an anchor and nothing is added to it or changed on it). The client's friendly-nameplate switch (the
Shift-V one) is switched on again while a voice player is near, so there is a plate to anchor to (0.6.8 and 0.6.9 left it off and no icon showed unless the player had friendly plates on themselves); `/eqvc plates off` stops
that. **After installing, type /reload in the game**: the addon is only read at login, and an old copy still running (0.6.3 hid plates) can look like a bug in the new one.

## 0.6.9 (2026-11, pre-release)

**The icon is anchored to the nameplate, not part of it.** It now lives on a frame of its own under UIParent and is placed 4 px above the plate's name text (the plate is only a point to measure from), so nothing the plate or another
addon does to its regions or alpha reaches the icon, and EqVoice adds nothing to the plate. It is shown while the plate is, checked every frame.

## 0.6.8 (2026-11, pre-release)

**Nameplates: EqVoice no longer switches any nameplate setting.** With Overhead icons On, 0.6.7 still switched the client's friendly nameplates on while a voice player was near, and the player reported their nameplates
hiding; that switch is the only nameplate-affecting call left, so it is off by default (`/eqvc plates on` brings it back as an opt-in). The icon goes above the nameplates the player has, and if none shows for a voice
player near them the chat says how to get friendly player nameplates (Shift-V, or Show Friendly Players in the Equadis overhaul), once.

**Sliders:** a click on a slider's bar (not the knob) now sets the value; it used to be ignored and snap back, because that click sends no mouse-down on this client.

## 0.6.7 (2026-11, pre-release)

**Indicator: wider.** 26 x 30 (it was 14 x 28), glyph 18 px.

**Nameplates: the range change is gone.** 0.6.5 and 0.6.6 raised SuperWoW's `NameplateRange` to 80 yd while a voice player was near; players reported all nameplates hiding when someone with EqVoice was nearby,
and that was the newest nameplate-related change, so it is removed: EqVoice never changes the nameplate range any more (a range left at 80 by those versions is put back once from the saved value). Icons are seen as
far away as the player's own range lets plates be (SuperAPI options: Nameplate range, up to 80). The friendly-nameplate switch is re-asserted every 20 s instead of 5.

**Changing character no longer needs EqVoice restarted, and no longer waits two minutes.** The app was pinned to the first character's game file and only let it go after 120 s of silence. Now, when that
file has been quiet for 6 s and another character's file is being written to right now (and no other copy of EqVoice has that character), it switches to the new one. It does not take back the character it
just left (that file stays "fresh" for 20 s). Logging out to the character screen with nobody else playing keeps the claim, as before.

## 0.6.6 (2026-11, pre-release)

**Four voice levels on the icon.** Nothing: the plain speaker. A **quiet** voice: one bubble, and it carries less far (heard as if up to 1.6 times as far away). An ordinary voice: **two bubbles**, white.
A raised voice (**yelling**): the same two bubbles in **red**, and it carries further (as before). Quiet and yelling are relative to the speaker's own usual voice (quiet: 7 to 15 dB under their
average over the last half minute; the average moves slowly so ordinary speech after a shout is not taken for a quiet voice). Not connected is a red **X** (it was the red speaker). Deafened with
the microphone open shows the deaf icon beside the same four. The status letters are `o w s l` (and `p n q r` beside the deaf icon).

## 0.6.5 (2026-11, pre-release)

**Direction.** The addon learns the position axes from your running, but a run in one direction cannot tell mirrored axes from plain ones, and it used to pick one at random: a wrong
pick sounded wrong until a later run with turns put it right ("bugged at first, then fixed itself"). It now only settles after a run in which you turned, snaps the angle to a quarter
turn, and ignores values saved by the old code. The first guess (four-value `UnitPosition`: ClassicAPI's order, west first) was right on the three accounts that had learned it.

**Icons from further away.** A plate, and the icon on it, only exists within the nameplate range (41 yd on this install). While icons are wanted and a voice player is near, EqVoice raises
SuperWoW's `NameplateRange` to 80 yd (a voice carries to 85) and puts the player's own value back afterwards: when icons go off, at logout, and (from the saved variables) after a crash.
A side effect to know: other nameplates (enemies) also show out to 80 yd while it is widened.

## 0.6.4 (2026-11, pre-release)

The nameplates are the client's default ones, one to one: EqVoice only switches friendly nameplates on and draws the icon above voice players; it changes nothing on a plate (0.6.3 hid bars and the NPCs' plates). The panel shows the version, small and grey, beside the title.

## 0.6.3 (2026-11, pre-release)

Nameplates, again: 0.6.2 switched nothing on, so no icons showed. Now the client's default friendly nameplates are on again, but only a voice player's stays visible (the client's own name text, untouched, with the icon above); NPCs and players without EqVoice are hidden.

## 0.6.2 (2026-11, pre-release)

Nameplates: EqVoice no longer forces friendly nameplates on (that is what put every friendly NPC's name on screen in the default font). It only adds the icon above the plates the player already has.

## 0.6.0 (2026-11, pre-release): the test build before 1.0

Everything from 0.5.x (whisper, noise suppression, the natural volume curve, direction that learns its axes, red muted and deafened icons, the player window and
round slider knobs) plus a new **indicator**: a small rounded button in the panel's own theme (a ring and microphone in the colour of the state) that starts in the middle of
the screen in the top quarter, draggable anywhere (`/eqvc reset` puts it back). EqVoice never changes the player's nameplate font, colour or text (a test checks it).
If this build holds up in play it becomes 1.0.

## 0.5.0 (2026-11): whisper, noise suppression, a natural volume curve, direction that learns

Borrowed from Simple Voice Chat's proximity voice (not its groups): **whispering** (hold a key you choose, Whisper in the panel; you are heard as if twice as far
away, so the range halves), **RNNoise noise suppression** (a neural network in an audio worklet, `ui/rnnoise-worklet.js`, in front of the keyboard filter;
on by default, "Noise Suppression" in the panel; if it cannot load the microphone is sent without it and the app logs why), and a **natural volume curve**
(100% to 5 yd, 50% at 20, 15% at 40, a shout 10% at 70; see doc 11). OpenAL's 3D sound is what the engine's HRTF panner already does.

**Direction now learns the axes.** `UnitPosition` answers in a different order on different clients (SuperWoW north, west; ClassicAPI y, x), so the addon learns the
axes from 20 yards of running forward and keeps them (`EqVoiceDB.dirCal`); `/ev dir reset` forgets them. The direction follows the way the character faces, not the camera.

Also: the nameplate name EqVoice draws for plates ECO hides uses the player's own nameplate font; the overhead icon is 16 px; the setup box lost its Skip button
and the Sound row reads Headphones, Speakers, Mono; the Nearby list scrolls with the mouse wheel and only glows for the selected player. Not seen in the real game yet.

## 0.4.1 (2026-11): what changed and how it works

**Where each part lives.** Settings you click are in the addon (`Panel.lua`, `Wizard.lua`); the app (`eqvoice-client`) applies them and the hidden engine page
(`ui/app.js`) does the audio. The hub (`eqvoice-signalling*`) is unchanged: nothing in this release needs a new hub or a new protocol version.

| Feature | Addon | App | Engine |
|---|---|---|---|
| Click a speaker icon: that player's settings | `Icons.lua` click -> `Panel.OpenPlayer` | `pvol`, `pblock`, `pmute` commands; kept by name in `config.json` | `volume` (0-2, multiplies that one stream) |
| **Block** | `pblock` | `Peers.set_blocked`: `gain` carries `send: false`, so the page stops sending the microphone to that player at once | `setSend(p, false)` (the track is replaced by nothing) |
| Directional audio | `Direction.lua` writes `eqvoice-dir*.txt` (degrees from straight ahead, clockwise, ten times a second) | reads it, tells the page `dir` when it moves 2 degrees | a gain, a stereo pan and an HRTF panner per voice, `VM.spatialFor` |
| Mono / Speakers / Headphones | panel + wizard page | `spatial` setting, replayed to a new engine | mono: no direction; speakers: pan up to 80% and quieter behind; headphones: HRTF front/back plus a pan |
| Microphone test | `mictest` | `mic_test` closes `tx_enabled` first, is re-sent to a new engine, never touches your mute | the send chain's output is played to the chosen device; the outgoing track is off |
| Two deafens | `deafen 1` (microphone closes too) / `deafen 2` (microphone stays) | `deaf_keep_mic`; your own mute is never changed | `deafen` mutes the voices |
| Boost slider | Input volume, 0-200%, continuous | `input_gain` | (unchanged) |
| Plain device names | list from the status file | `uifiles.clean_device_label`, aliases removed, `E` lines say what the default is | |

**Directional audio, honestly.** Voice direction needs two functions the client mod DLLs provide: `UnitPosition` and `GetPlayerFacing`. Direction is the angle from where
your character faces to the other player (so turning the camera does not matter, turning the character does). `/ev dir` prints the angle to your target, and `/ev dir flip` swaps
the sides if a client turns out to answer mirrored. Measured in real engines (headphones, 90 degrees right): the right ear is about 3 times louder than the left; mono is equal.

**Icons over heads, honestly.** The names drawn above characters are part of the 3D world: no addon can draw on them. A nameplate is the one thing that sits over a head and can be
drawn on, so the icon rides on it. From 0.6.4: while a voice player is near, EqVoice switches the client's own friendly nameplates on (the Shift-V switch) and changes nothing on any plate: the default nameplate one to one, with the icon above voice players' names. A test fails if any code touches a plate. Seen in the test stub only; **not seen in the real game**.

**Not done.** The two-ears effect is verified in a real browser engine, not on your ears. Group voice does not exist. Per-player settings are kept by character name, per Windows user.

## Hardening results

- Full suite: 559 pass, 4 skipped (after fixing two stale tests that expected the old mod check and an instant game ident).
- `scale_bench.py`: 1000 players in the roster cost 0.36 ms per client tick and 7 KB per roster file; the addon frame stays under 1 ms.
- `mesh_bench.py` (real hidden browsers): 3 and 4 engines on one PC run at 11 to 30% CPU and about 250 to 470 MB in total.
- Idle: with no character in game the engine is stopped and the program only watches a file.

## Not yet verified

1. Any of the new UI in the real game (panel layout, wizard, device lists, icon art positions).
2. The setup exe on a PC other than this one, and with SmartScreen.
3. 3 or more people at once (`10-step-by-step-test-guide.md`, Phase C).
4. `tools/smoke_tray.py` is stale for 0.4.1 and needs rewriting against a simulated game.
