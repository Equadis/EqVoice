# 11 — One-click install and the in-game panel

This is the player-facing layer: **install is one double-click, and everything you need while playing is inside the game.** It sits on top of
the pipeline in `06` and changes none of it.

## What a player does

1. Unzip, double-click **EqVoice.exe**.
2. Start WoW and log in (type `/reload` once if you were already in).
3. Click the small microphone button beside the micro bar (the row of little menu buttons), or type `/eqvc`. Talk: the microphone is open by default.

That is all. The exe finds the game, copies the addon in, puts its icon in the **system tray** (by the clock) and starts the voice engine out of sight: **there
is no taskbar button**. The first time only it also opens the window and shows a few lines about what to do in the game. Afterwards the window is opened from the
tray (double-click the icon) and can be closed without ending anything; to quit, right-click the icon and choose **Quit EqVoice**.

## The in-game panel

`/eqvc` or `/eqvoice` opens it; the same words are the text commands. The old `/ev` stays for diagnostics.

| Command | What it does |
|---|---|
| `/eqvc` | open or close the panel |
| `/eqvc open` · `/eqvc ptt` | open microphone (default) · push to talk (key shown in the panel, F9 unless changed) |
| `/eqvc mute` · `unmute` · `deafen` · `undeafen` | mute yourself · deafen = hear nobody, nobody hears you |
| `/eqvc vol 0-100` | output volume (steps of 5) on top of distance and per-player volume |
| `/eqvc input 0-200` | how loud your microphone is before the noise gate and everything else (100 = as it comes; the setup may move it) |
| `/eqvc setup` | the microphone setup: stay silent, talk normally, shout (see below) |
| `/eqvc test` | play the speaker test (two rising notes) |
| `/eqvc blocklist` | open the window with everyone you have blocked |
| `/eqvc status` | what the app says, in chat |
| `/eqvc show` · `hide` · `reset` | the indicator on or off · put the panel and the indicator back where they started (the indicator beside the micro bar) |

**The indicator** is a microphone button drawn like the client's own micro-bar buttons (the same 28 x 58 size, bronze frame, pressed art
and highlight; the art is `Micro*.tga` and `Mic*.tga` in the addon folder, made by `tools/make_micro_art.py`). It sits to the right of
the micro bar and follows it: the bar's size, and wherever a UI addon (EquadisClassicOverhaul's micro menu bar, say) moves it. It looks for
the bar once a second, so a hidden micro bar makes the button float at the top of the screen and it docks again when the bar is back.
The **colour of the microphone** is the state: green = live (open mic, or the push-to-talk key is ready), cyan = talking, yellow = a
warning, red = an error, grey = the desktop program is not running; a red slash over it means muted or deafened. Hover it for the exact
words (`MIC ON`, `TALKING`, `PTT F9`, `MUTED`, `DEAF`, `CHECK`, `ERROR`, `NO APP`). Click it for the panel, **right-click to mute**,
**Shift-drag** to put it somewhere else for good (the place and size are remembered; `/eqvc reset` docks it again). Dragging needs Shift
so a stray drag in a row of buttons does nothing.

**The panel** (440 x 462, in four sections; the microphone and speaker buttons carry the addon's own small pictures):

* **Microphone**: Open mic / Push to talk, Mute / Deafen, the **key** (click, then press the new one; hover it for what it does in the mode you
  are in), **Input volume** (a slider, 0-200%, 100 = as the microphone gives it) and **Set up mic**.
* **Speaker**: **Output volume** (a slider, 0-100% in steps of 5) and **Test speakers**.
* **Voice processing**: **Boost** (a four-stop slider: off, low x1.4, normal x2, high x3: how much louder people are right next to you) and the
  **Noise gate** (a slider for the bar, -80 to -20 dB, and a button for the mode: Auto, Manual, Off; moving the slider while it is on Auto
  switches it to Manual at the place you put it).
* **Nearby**: up to five players, each with a voice icon, distance, "speaking", "connecting", "relay" or "muted". **Click a name to mute that
  player**, again to unmute. The **Blocklist** button opens the full list (below).

A slider shows its number as the hand moves and sends the value once the hand has rested for a fifth of a second, not at every step of a drag.
If a client will not make a slider the row gets a minus and a plus button instead (a tenth of the range a click); the panel is built so that
one failing widget can never take the rest down. Everything in the panel is also a slash command: `/eqvc key`, `input`, `setup`, `boost
off|low|normal|high`, `gate auto|manual|off` or `gate -50` (the manual bar in dB), `mute <name>` / `unmute <name>` / `mute target`, `muted`
(the list in the chat), `blocklist` (the window), `icons on|off`, plus the earlier `open`, `ptt`, `mute`, `unmute`, `deafen`, `undeafen`,
`vol`, `test`, `status`, `show`, `hide`, `reset`. `/eqvc help` lists them.

When the app is not running the panel says so and its buttons and sliders are greyed. Escape closes the panel.

### The blocklist window

`Blocklist` in the panel (or `/eqvc blocklist`) opens a window with **everyone you have blocked**, however far away they are (the Nearby list shows
only who is near): ten to a page with the previous and next buttons, an **Unblock** button beside each name, a box to type a name (**Block**, or
Enter), and **Block target**. It is the same list as the window's "Muted players" and `audio.mute_list`: a blocked player is never heard, and can
still hear you. Names are kept in lower case and shown with a capital. A click shows in the window at once and the app confirms it.

### The microphone setup

**Set up mic** (panel), `/eqvc setup`, or **Set up my microphone** in the EqVoice window. Three recordings, about 17 seconds: **stay silent** (5 s),
**talk normally** (7 s), **shout** (5 s; the first second of each is let go, so there is time to react). The page listens to the microphone as it comes,
before any volume change, in the band the noise gate works in (200 Hz to 3.5 kHz, in dB, twenty readings a second; `startCalibration` in `ui/app.js`) and
hands the three lists to the app (`calibration.py`, pure and tested without a browser):

* **The bar** goes halfway between the room (the loudest 5% of the silent readings is let go as a cough) and the quiet end of ordinary speech, never
  closer than 5 dB to the room or 3 dB to the quiet end of speech. The gate stays on or comes on with it, in **manual** mode at that bar.
* **The microphone volume** stays 100% unless it has to move: a shout that came within 3 dB of full scale pulls it down until the loudest shout sits at -6 dB
  (the bar is stated after that change, since the gate comes after the volume), and a microphone so quiet that talking sits below -50 dB is lifted by
  at most 6 dB.
* **How it went**: `good`; `noisy` (the voice is under 8 dB above the room: the bar is placed as well as it can be, the gate stays on automatic, and the words
  say a quieter room or a closer microphone will help); `none` (nobody talked: **nothing is changed**, and it says so).

The game shows an overlay with the step, the seconds left and, at the end, the result and a **Done** button (the same command as Cancel: `calibrate 0`);
it also appears when the setup was started in the window, and a result from an earlier session is not shown again. The window shows the same as a card
with a live bar for how loud the microphone hears you. The status file carries `cal` (idle, quiet, normal, loud, done), `calt` (seconds left), `calq`
(good, noisy, none, or `-`) and `cald` (done at least once: the panel nudges a player who has not); the commands are `calibrate 1` and `calibrate 0`.

## How it works (two small files)

The addon cannot talk to the program directly, so the two already-existing file channels get a sibling each. Both live in `CustomData\` next
to the roster file and use its naming (`...-<Character>.txt` when the roster file does, plain otherwise).

* **Status, app → addon** (`eqvoice-st*.txt`, `OVS1`): what the panel shows. The app rewrites it when something changes and at least
  every 2 s; the addon treats a sequence number that stops moving for 6 s as "the app is not running". Fields: level (ok/warn/bad), mode,
  mute, deafen, transmitting, talking, output volume, microphone volume (`ing`), the microphone setup (`cal`, `calt`, `calq`, `cald`), players near,
  connected, key, boost, noise gate and its bar, game/hub/mic state; the app's own
  one-line headline (the same sentence as the top of its window); up to four more problems; up to forty nearby players (each with the state
  of their voice, `s` speaking, `o` open, `m` muted, `d` deafened, `-` not known, and whether you have muted them); and your mute list.
* **Commands, addon → app** (`eqvoice-ctl*.txt`, `OVC1`): what you clicked: `mode open|ptt`, `mute 0|1`, `deafen 0|1`, `volume 0..100`,
  `test 1`, `input 0..200`, `calibrate 0|1`, `boost off|low|normal|high`, `key <the game's name for a key>`, `gate off|auto|manual`, `gatedb <dB>`,
  `pmute <player> 0|1`.
  The newest eight are in every write, each with an id that starts from the wall clock, so a second click inside one poll is not
  lost, nothing is applied twice, and a file left over from yesterday is ignored when the app starts.

What the panel shows is always the app's own answer, never the addon's guess. For three seconds after a click the panel shows the wanted
value at once so it feels instant, then needs the app to confirm.

Both files carry a CRC like the roster file, so a half-written file is read again, not obeyed. The formats are specified in the module
docstrings (`eqvoice-client/eqvoice_client/bridge/uifiles.py`, `eqvoice-addon/EqVoice/Status.lua`, `Control.lua`) and checked
against each other in the tests (the addon's real Lua parses what Python writes and the reverse).

## How loud a voice is at each distance

Seven numbers decide it (config `proximity.*`, `audio.boost`), and they are the same in the page (`ui/voicemodel.js`, `curveGain`) and the app
(`peers.voice_gain`; a test checks them against each other over a grid):

| distance | what you hear |
|---|---|
| **0 to 10 yd** (`boost_fade_radius`) | the **boost**, which **scales with the distance**: normal is **twice** the voice's own loudness (+6 dB) right next to each other, 173% at 3 yd, 141% (+3 dB) at 5 yd, 107% at 8 yd, gone at 10 yd. It is a half cosine in decibels: no flat stretch, no step, easing in at both ends. Low is 1.4 times, high is 3 times, off is none (`audio.boost`). `boost_radius`, 0 by default, would hold the full boost for that many yards first |
| 10 to **30 yd** (`full_radius`) | **100%**: the voice as its owner's microphone made it. 30 yd is still full |
| **30 to 85 yd** (`peak_radius`) | falls as `(1 - t) ^ 1.6` (`curve_power`), `t` being how far along the stretch you are: 73% at 40 yd, 49% at 50, 28% at 60, **13% at 70 (about -18 dB: quiet)**, 2% at 80, gone at 85 |
| a **raised voice** | stays at 100% out to **45 yd** (`loud_full_radius`) and ends in the same place: 81% at 50 yd, 47% at 60, 21% at 70 |

"Right next to each other" is the boost, and "loud and clear at 3 yd" is the boost on top of the levelling below. Changing the boost (window, panel
or `/eqvc boost`) changes only the first row. The page does not jump to each new distance it is given (a reading comes a couple of times a second, and a
player walks several yards in that time): it follows the distance over 0.3 s and the volume over 0.08 s (`VoiceModel.FOLLOW`), so the volume, the boost most of all,
rises and falls continuously as you walk. A test samples the page's gain every 20 ms through a walk of 6 yd a second up to someone: the biggest change is 0.02,
where a step to each reading would be 0.46. The numbers are `DEFAULT_CURVE` in `voicemodel.js` and the config; the app sends the real ones to the page.

"Loud" is **how loud this speaker is being compared with how they usually sound**, measured by each listener on the voice as it arrives
(`VoiceModel.Loudness`). It tracks the level of the speaker's usual loud syllables (the 90th percentile of their voiced levels, drifting about
4 dB a second up and half a dB a second down), and how far the voice is above that, past a 2 dB margin, scales 0 to 1 over the next 8 dB: 0 = talking,
1 = shouting (10 dB above their usual). It rises at once, drains over 1.5 s so a raised voice is not chopped up between syllables, and learns a new
speaker for three seconds of speech before judging them. A quiet microphone and a hot one are judged alike; it is the change that counts.
This needs the browser's automatic microphone level **off** (`audio.auto_gain`, the default), because that feature exists to flatten exactly this.

**Levelling (`audio.auto_level`, on by default).** Microphones differ by 20 dB or more, so "a voice at 100%" would be loud from one player and a
whisper from the next. Each listener also uses that measured "usual level" of a speaker: after three seconds of their speech, a quiet one is lifted
and a hot one eased towards a common target (-20 dBFS), by at most +12 dB and -6 dB, moving over about two seconds, and not while the speaker is
raising their voice (so a shout is still heard as louder). Everything is then on the same footing before the distance curve is applied. It is a
checkbox in the window ("level out different microphones"); the page reports the lift it is applying as `lev` in its stats. Measured in a real
engine: within a few seconds of speech a microphone at -48 dB was being lifted by 9 dB and one at -10 dB eased by 5 dB, on the way to the 12 and 6 dB limits.

Because a shout can be heard 85 yd away, calls are made and kept to 85 yd (`peak_radius`, +3 to leave) and we keep transmitting to everyone in
call range; the voice is silent at the listener until it is loud enough to reach them. The distance reaches the page about four times a second
(`gain` messages carry `dist` and `gate`), the hub's link radius is 90 yd, and the game is asked to report lookups out to 100 yd
(`roster.report`; `proximity.report_radius` 95). Old config files that still hold the earlier defaults (10, 50, 60, and 15 for `full_radius`) move to
the new ones once (config versions 3 and 4); a value the player changed is left alone. A wider radius means more simultaneous calls in a crowd
(`max_peers` still caps them at 8): that is the price, and docs/09's costs apply.

## The noise gate and the keyboard filter (what you send)

An open microphone sends everything, and a keyboard is loud. The gate (`audio.keyboard_filter`, on by default) is an
audio worklet (`ui/gate-worklet.js`) between the microphone and the call. A voice is a sound that lasts and holds its level, so the gate opens only
when the 200 Hz to 3.5 kHz band has been above its bar, and not dying away, for 50 ms in a row; a keystroke (a burst and a
thump that ring down in a few tens of milliseconds) never counts, whatever the bar. The audio is delayed by the same time (a look-ahead), so a word is open from its
first sound; the gate stays open 220 ms after the voice ends and closes smoothly. Only 15 ms or more of steady voice holds an open gate open, so
typing straight after talking does not. The price is about 60 ms of delay. The browser's own noise suppression and echo cancellation stay on as before (`audio.processing`).

**Where the bar is** is the one choice (window: Noise gate; game: the panel's **Noise gate** slider and button, `/eqvc gate`, or the microphone setup, which
measures the room and your voice and puts the bar between them):

* **Automatic** (`audio.gate_mode = "auto"`, the default): the bar is 9 dB over the room's noise floor (never below -68 dB in that band; a quiet
  voice sits around -60). The floor is followed continuously (down at once, up half a decibel a second), so a fan raises the bar by itself, and a
  steady hiss is treated as the room. The window shows "opens at -58 dB".
* **Manual** (`"manual"`, `audio.gate_db`, -80 sensitive to -20 insensitive): the bar is where you put it (the slider in the window, - and + in the
  game, `/eqvc gate -50`), 3 dB lower once the gate is open so a voice that sits on the bar does not flutter. It is only a level: a steady noise
  louder than the bar goes through (that is what a manual gate does; the automatic one is the one that learns the room). The window's **Gate level**
  bar shows your voice against the line (the worklet reports both ten times a second), so setting it is a matter of watching it.
* **Off**: the microphone as it is, nothing delayed.

On synthetic stand-ins (room hiss, a voice of syllables with consonants and pauses, keystrokes with a thump and a release click, mouse clicks) the
filter removes typing and clicks completely and leaves the voice at the same loudness within 0.1 dB and its first 120 ms intact, in both modes. On the same
recordings played through the browser's fake microphone, typing opens the gate 0.0% of the time and a voice 91%. Real keyboards differ: if one still
gets through, the settings are the options at the top of `gate-worklet.js` (`sustainMs`, `hangMs`, `openDb`, `fallDb`). If the filter ever fails
to start or stops answering, the raw microphone is sent instead and the window says so ("The keyboard filter is switched off"); being heard comes
before being clean.

## The key, mute, deafen and the mute list

**One key** (`audio.ptt_code`; F9 by default) with two jobs, by the transmit mode. With **push to talk** you hold it to speak (a 200 ms tail
keeps word ends). With an **open microphone** each press **mutes or unmutes** you (no tail, so two quick taps are two toggles); it never undeafens.
Every mute, unmute, deafen and undeafen plays a short blip through your speakers (falling = off, rising = on; `audio.cues`), because in a full-screen
game the indicator is not always in sight. While deafened only the deafen and undeafen blips play.

**Change it** in the window (Key button, then press a key or mouse button 3, 4 or 5) or **in the game**: the panel's key button (or `/eqvc key`)
listens for the next key press and hands the game's own name for it (`F10`, `A`, `NUMPAD1`, `LSHIFT`, `BUTTON4`...) to the app, which turns it into the
key it watches (`ptt.vk_from_wow`; Escape, Enter and left and right click are refused and the panel says why). The button reads "Mute key: F9" with an open
microphone and "Push-to-talk key: F9" in push to talk. Escape, a left or right click, or ten seconds of nothing cancels.

**Deafen** stops all incoming sound: every voice's output is muted at once, voices that connect while you are deafened start muted, and you stop
transmitting too. (The page mutes each player's output element; the real-engine test checks it from the page's own report.)

**The mute list** (`audio.mute_list`): players whose voice you never hear, wherever they are and whenever they come back. A name is added from the
Mute button beside a player in the window, by clicking a name in the game's panel, with `/eqvc mute <name>` or `/eqvc mute target`, or by typing a
name under "Muted players" in the window. The connection stays up and they can still hear you: only their playback is switched off here (a
`peer_mute` to the page, or a `mute` flag on the connection when it opens). The list is saved, shows in the panel ("2 players on your mute list:
/eqvc muted") and in the window with an x beside each name to take them off.

## The voice icons beside names

A small icon to the **right of a player's name** shows what their EqVoice is doing. The five pictures are Ollie's (`tools/art/voice-icons/*.png`, 24 x 24;
made into the game's 16 x 16 textures `Voice*.tga` by `tools/make_micro_art.py`, and used as they are in the window):

| picture | meaning |
|---|---|
| speaker with sound waves (`VoiceSpeaking`) | **speaking** |
| plain speaker (`VoiceKnown`, Ollie's `vol_low`) | **on the voice channel and not talking**: the microphone is open (or the push-to-talk key is held) and nothing is being transmitted |
| microphone with a slash (`VoiceMuted`) | **muted**, or push to talk with the key not held (from outside it is the same) |
| headphones with a slash (`VoiceDeafened`) | **deafened** (which also means muted) |
| the same plain speaker, dimmed | has EqVoice, nothing more is known (not connected to them yet, or their app is not running) |

(Ollie's `vol_mid`, the speaker with one small wave, is kept in `tools/art/voice-icons/open.png` but not drawn.) They appear in the panel's list, on nameplates, on the target, player and party frames
(`Icons.lua`), and in the window's table. `/eqvc icons off` removes the ones in the game. **On the unit frames the icon is a button: a click opens the
panel** (nameplates are only drawn on, so a click there still targets the player).

**Where the icon goes** is decided by where the words are, not by where the box is: just after the last letter, whether the name is centred, left- or
right-aligned, and no further than the box allows. The name that counts is the one the player *sees*. The client's own unit-frame names are
`PlayerName`, `TargetName` and `PartyMemberFrameNName` (the 1.12 name of the target's is `TargetName`; the `TargetFrameTextureFrameName` of later clients does not
exist, and the first version of this module looked for it, so there was never an icon on the target frame). The Equadis overhaul (ECO) hides the client's
nameplate texts and draws its own, and draws its own left-aligned names on its party frames; the icon follows ECO's (`EquadisClassicOverhaul.modules.nameplates.plates[frame].name`,
`...modules.partyframes.frames[i].nameText`) when ECO has one, and the client's otherwise.

Two sources, best first. **Each player's own EqVoice tells yours** over a small data channel that rides along with their voice connection (negotiated,
id 1: `{"t":"state","mute":..,"deaf":..}`): exact, and about as quick as a click; "speaking" is what the listener hears arriving. For **anyone with the
addon** the presence heartbeat the addon already sends now carries three more flag bits (muted, deafened, app running; the format is unchanged and an
older addon simply sends 1), and a change sends one straight away instead of waiting out the interval; that works without a voice connection but takes a
few seconds and knows nothing about speaking. Nameplates are found the way `Scan.lua` finds them (children of the world frame whose first region is the
border picture and whose third is the name); a surface that fails three times is switched off for the session and the rest keeps working.

## How voices sound: the voice chain and the bitrate cap

There used to be a "voice effect" (a telephone-style band-limit on what you hear, with a Voice button in the panel and `/eqvc fx`). Nobody could hear
what it did, so it was **taken out** (config version 5 drops the setting from a saved file). What is left is the chain every voice goes through in the page:
a meter (how loud the speaker is being, for the raised-voice rule), the boost (the volume above full that a voice doubled up close needs) and a limiter
(so that boost cannot clip), then the output element, which keeps the speaker choice, the volume and deafen exactly as before.

**The chain can never cost a voice:** if the audio context is not running the voice plays plain until it is; if building the chain throws, or it stays silent for two
seconds while a voice is arriving (the page compares the decoder's energy with a meter at the end of the chain), every voice goes back to the plain path and the
window shows "The voice booster is switched off (...)... Restart EqVoice to try it again". The real-engine test
(`test_the_voice_chain_runs_in_a_real_engine...`) proves the chain passes a real call's audio and that the bitrate reaches the sender.

**Voice quality (a rate cap).** `audio.max_bitrate_kbps`, default **24** (Light 16, Normal 24, High 32, or 0 for the browser's default of
about 32). It is applied three ways: `maxBitrate` on each call's sender (live, reported back as `maxbr`), `maxaveragebitrate` in the Opus
parameters, and `maxplaybackrate=16000` (wideband, nothing above 8 kHz is encoded). The cap
holds in both directions, since a listener's Opus parameters tell the other end how much to send. It is also a bandwidth saving: each
call has its own encoder, so five players around you cost five streams.

## Open mic by default

New installs transmit whenever you are not muted (`audio.mode = "open"`), with Opus DTX on so silence costs next to nothing. A settings file
from before this release that still said push to talk **with the old default key (backtick)** is moved to open mic and F9 once; a file
that chose any other key is left alone. Push to talk is one click away (`/eqvc ptt` or the panel) and the key can be changed in the
EqVoice window.

## EqVoice.exe

* **Finds the game** (the folder with `WoW.exe`): the one saved last time, a running WoW.exe, the registry (Blizzard and uninstall entries,
  which is how OctoWoW is found on this PC), the usual places (a drive's top level, `Games`, `Program Files`, folders named like the game,
  skipping updater backups), then asks once with Windows' own folder chooser.
* **Installs the addon**: copies only files that differ, removes files of older versions from its own folder, and if Windows refuses (a game
  in Program Files) offers to restart itself as administrator. It warns, but carries on, when `ClassicAPI.dll` is missing.
* **Switches the addon on for every character.** Found on this PC on 2026-10-25: the game keeps a list per character
  (`WTF\Account\<account>\<realm>\<character>\AddOns.txt`) and a character made before EqVoice existed listed it as
  `EqVoice: disabled`, so the addon never loaded and `/eqvc` was an unknown command (no error, no panel: the worst way to fail).
  The exe now sets that one line to `enabled` in every list (adds the line if a list lacks it), keeping the file's line endings, but
  **only while the game is closed**: the game rewrites these lists when a character is left, so a change made under it would be undone.
  With the game open it shows a message naming the characters and the click path (Log Out, AddOns, tick EqVoice, Okay), and the
  EqVoice window's "waiting for the addon" line names the same characters. `/reload` cannot fix this: the list is read at login.
* **The voice server's address** is `hub.txt` beside the exe (one line, `URL CODE`) or built into it (`build_exe.py --hub-url`). The file wins,
  so one exe serves any test server; the join code is never shown anywhere.
* **One copy per game window** (see "Two game windows on one PC"); **closing the window does not quit** (the tray's Quit does; without a tray icon, see "Tray only", closing the window
  quits after 8 s as it used to, so that a reload is not a quit); a busy port can never stop it (random port).
* **Help is in the window**: Diagnostics has **Check my connection** (hub reachable? STUN? what kind of NAT?) and **Save report** (a zip of
  logs on the Desktop, join code blanked; `realtest.py report --friend` reads it).
* Errors are message boxes in words; a crash writes `%APPDATA%\EqVoice\logs\crash.txt`.

Build and check:

```
python tools/build_exe.py                  dist/EqVoice.exe (about 25 s, 17 MB), reads hub.txt beside it
python tools/build_exe.py --hub-url wss://voice.example/ws --join-code CODE     the address built in (a release)
python tools/smoke_exe.py                  starts it twice: with arguments (serves its window and files) and as a double-click on a pretend
                                           PC (finds the game, installs the whole addon byte for byte, reads hub.txt, remembers the folder)
python tools/make_test_pack.py --hub-url wss://... --join-code CODE     dist/EqVoice-test-pack.zip = exe + hub.txt + README
```

`realtest.py run --tunnel` builds the pack itself, and rebuilds `EqVoice.exe` first if any source is newer than it, so a pack can never carry
a stale addon. `--python-pack` makes the older Python-and-.bat pack.

**Limits.** The exe is **not code-signed** (that costs money), so Windows SmartScreen says "Windows protected your PC" the first time: More
info, Run anyway (the README says so). Some antivirus products distrust any new PyInstaller exe; a signed release fixes both. Edge or Chrome
must be installed (Windows has Edge). ClassicAPI comes with the OctoWoW client.

## Two game windows on one PC

A copy of EqVoice serves one game window. A second double-click does not refuse; it opens a second copy:

* **Slots.** Each running copy holds one of four named Windows mutexes (`Local\EqVoice-single-instance`, `-2` ... `-4`). The first copy keeps the usual data
  folder; copy N keeps its own, `%APPDATA%\EqVoice\copy-N` (settings, this PC's identity for the voice server, logs, the browser profile), so copies never
  share a file. A later copy reuses the game folder the first one found, so it does not search again. All four taken: a message says so.
* **Claims.** A copy started by double-click does not know which game it is for, so it takes the first logged-in character no other copy has
  (`bridge/claims.py`: one named mutex per character, `Local\EqVoice-char-<name>`, gone the instant its copy is, crash included). It reads only that
  character's file (`eqvoice-<Character>.txt`) from then on. A game that goes quiet for 120 s is let go, so another character can be taken. A character
  whose name has no per-character file (not plain ASCII letters) falls back to the shared `eqvoice.txt`, if nobody else is using it. Before this, two game windows
  and one copy made the copy flip between the two characters every second, and the voice server's 12-registrations-a-minute limit refused it.
* **Asking.** If a second double-click finds a logged-in game that no copy serves, that copy is for it and nothing is asked. If every logged-in game
  already has a copy (usually an accidental double-click) it asks "Open another copy, for a second game window?" and does nothing on No. It never guesses a
  character twice: the claim is what stops two copies serving one.
* A copy that is waiting says why: "Every logged-in game already has an EqVoice (...). This copy is waiting for another game window".

## A test setup hosts its own voice server

`hub.txt` beside the exe holds the voice server's address. A real release points at a real server and none of this applies. A test setup points it at
this PC (`ws://127.0.0.1:8787/ws`), and then nobody is listening unless a server was started by hand, which is the first thing anyone testing met.
So when the address is on this PC and nothing answers, **the first copy hosts the server** (`launcher.LocalHub`: the same Python hub the Cloudflare one is
checked against, bundled in the exe, with a looser registration limit for one address). A second copy finds it and connects. If the copy hosting it is
closed, another takes over within about three seconds (measured: the server back 3.4 s after the host was killed, the survivor registered again 0.5 s
later). A remote address is never hosted from here.

## The first launch and the microphone

A new browser profile (the first launch, and every extra copy) used to wait for someone to find and click Edge's "Allow microphone" bubble, with the window
saying only that the microphone was starting. The window is EqVoice's own page, served from this PC, so the browser is now started with
`--use-fake-ui-for-media-stream`, which answers that one prompt "allow" and uses the default microphone (the microphone is still chosen in the window).
Measured on a fresh profile with the real exe and a real microphone: ready 3.2 s after the double-click (it never became ready before).

## The program's icon

Ollie's picture (a tentacle round a microphone, `tools/art/EqVc.png`) is EqVoice's icon, and only in the places that are the program's own face:
**the exe** (in Explorer and on the taskbar), **the window's button on the taskbar**, and **the icon in the system tray**. It is not drawn in the game
(the indicator and panel keep their own art) and not on the window's page.

* `python tools/make_app_icon.py` makes `ui/eqvoice.ico` (every size Windows asks for, 16 to 256) and `ui/eqvoice-icon.png` from the picture. The clear space
  round the drawing is cropped away first, so the tentacle fills a 16-pixel icon instead of sitting small in it.
* `tools/build_exe.py` puts the `.ico` on the exe (`--icon`). The window is the browser's app window, and an app window's taskbar button is the page's
  favicon, so `index.html` names `eqvoice-icon.png` (checked in a real Edge: without the line the window holds Edge's grey default, with it the tentacle:
  `python tools/check_window_icon.py`).
* **The tray** (`tray.py`, plain ctypes, no extra package): the icon with the status as its tooltip (the window's top line, refreshed every 3 s);
  double-click, or **Open EqVoice** on the right-click menu, brings the window forward (or opens it if there is none: see "Tray only");
  **Quit EqVoice** quits. If Explorer restarts the icon is added again. Only `EqVoice.exe` has one (the exe's own tests, which run without a window,
  leave none behind), and `ui.tray` in the config switches it off. Windows 10 may put a new icon under the "^" arrow of the notification area: drag it out
  to keep it in view.
* `python tools/smoke_tray.py` runs the real exe once and reads back from Windows itself the icon inside the exe, that the shell has a tray icon for it, that the voice
  engine's window is not on the screen, that the hidden engine's timer still runs 50 times a second, that a double-click on the tray icon opens a window with the
  tentacle icon, that closing it ends nothing, and that Quit ends the program and every browser process of its profile (it opens a window twice, for about ten seconds
  in all, which is why it is not part of the suite).

## Tray only: no taskbar button

Until now the window *was* the voice engine: it had a button on the taskbar, and closing it ended voice. With a tray icon the program now runs **from the tray**
(`ui.tray_only`, on by default; off gives the old single window with its button):

* **Two pages, two roles.** The page URL carries `role=engine` or `role=ui` (`ui/app.js`: `ROLE`). The **engine** is the first window the exe starts: it holds the microphone and
  every call, it is titled "EqVoice engine", and the launcher takes it off the screen the moment it exists (`tray.hide_windows`, looked for twenty times a second until it
  does: a hidden window has no taskbar button) and again whenever it is restarted. It is hidden, not closed, so it keeps running; it does not draw the window's table
  and meters (no point). The **window** is a second `--app` window on the same browser profile, opened by the tray's Open (or by the first run): it is only a window
  (`role=ui` never becomes the voice engine, whatever order the pages connect in; a page with no role is both, as before), so **closing it never touches the call**.
  Opened twice within three seconds it opens once.
* **The engine is looked after.** If its page goes away (a crash, someone ending the process) it is started again out of sight, after a second so that a page reload,
  which is back well inside that, is not a restart; five starts in two minutes and the app gives up and opens the window instead, which shows what is wrong. Closing
  the window of the *non*-tray-only program still quits after 8 s.
* **Tray-only needs the icon.** Without one (no notification area, or `ui.tray` off) the window stays in the taskbar: a window that cannot be found again is worse than a button.
* **Not throttled.** The browser is started with background-timer throttling, renderer backgrounding and occlusion tracking turned off, and the engine page counts its own
  20 ms timer (`hz` in the `mic` report; `/api/diag` shows it): measured on the real exe with its window hidden, **50 a second**, the same as when it is showing.
* **The window's live setup meter.** The setup is measured in the engine page, so the engine tells the app the level ten times a second and the app forwards it to the window
  (`cal_progress`).
* **A bug this found.** Quitting from the tray with a window open used to wait for the window's connection to end, which aiohttp allows a minute for, and the browser was
  only stopped after that. The app now closes its connections itself before the server's clean-up; Quit takes about a second.

## Launch timings (measured on this PC, 2026-10-26)

| Step | Time |
|---|---|
| double-click to the EqVoice window being up (a fresh copy, as from a download) | 1.0 to 1.2 s (1.4 s with the Edge window) |
| ... and the microphone ready (fresh browser profile, real microphone) | 3.2 s |
| two copies, two game windows: both windows up / both characters taken / both registered / voice flowing | 2.3 s / 4.4 s / 5.5 s / 6.7 s (from the first double-click) |
| the hosting copy closed: server back / the other copy registered again / a new copy connected again | 3.4 s / 3.9 s / 4.1 s |
| a call between two clients on one PC, VPN on (direct, host to host, no relay) | 0.3 to 0.5 s |
| a local hub (`wrangler dev`) ready / the Python hub ready | 3.3 s / 1.2 s |

What is not in this table and is not EqVoice's: starting WoW, logging in, and the game loading the addon. A deployed server would remove the need to start a
local one; it would not make any of these faster.

## Checking it in the real game (the part tests cannot do)

The panel is covered by tests against a stand-in for the 1.12 client that only has the methods that client has, but only the game proves the
drawing. In order:

1. `python tools\build_exe.py`, double-click `dist\EqVoice.exe` (with `dist\hub.txt` beside it for a local hub, or run `realtest run` instead).
2. Log in. Chat says `EqVoice ... Type /eqvc for the voice panel.` and the microphone button appears right of the micro bar (top centre if the micro bar is hidden).
3. `/eqvc`: the panel opens, status line in colour, the current mode lit. Click **Push to talk**, then **Open mic**; **Mute** and **Deafen**;
   **-** and **+**; **Test speakers** (two notes). Right-click the indicator (mute), hover it (tooltip), Shift-drag it, `/eqvc reset`.
4. Escape closes the panel. Quit the EqVoice window: the indicator says `NO APP` within seconds.

If anything is drawn wrongly or a click does nothing, `/ev status` still works, `/eqvc status` prints what the panel knows, and a drawing
error is traced once in chat as `panel error: ...` (the voice pipeline keeps running).
