# Handoff: the overhead (nameplate) voice icon, and where EqVoice stands (2026-11-16)

Written so a fresh session can pick up without the chat. Current build: **0.7.1** (pre-release line before 1.0), commit `84a7ea9` on `main` locally.

## The one open problem

**"Overhead icons: On" makes the ally's nameplate disappear, and the voice icon does not show above the player.** The player wants:

- the icon (speaker / mic / deaf, drawn on a small steel-blue tile) **above the player, 4 px above the nameplate's name**;
- **no change to the nameplate at all**: not its font, colour, text, art, bar, range or visibility. The nameplate is only an *anchor* to measure from;
- it must work **whether the plate is the vanilla client's (Friz Quadrata) or the Equadis overhaul's (ECO)**.

### Evidence (two screenshots from the player, v0.7.0 label on the panel)

| Overhead icons | What the screen showed |
|---|---|
| **Off** | The ally ("Gnomedboi", 6 yd) has a normal blue stock nameplate name floating above. No icon (expected). |
| **On** | The ally's name/plate is **gone**, and **no icon** either. |

So with icons On, something removes a plate that was already showing. Nothing in 0.7.0 changes a plate's regions; the only nameplate-affecting call EqVoice made was the client's **`ShowFriendNameplates()`** (made whenever a voice player was near, re-asserted every 20 s).

### Hypotheses (none verified; no live client was available)

1. **`ShowFriendNameplates()` behaves as a toggle on this client** (SuperWoW / ClassicAPI / ECO all hook nameplates). The player's friendly plates were already on, so the call switched them off. *Most likely.* 0.7.1 is built on this: it only calls the switch when a voice player has been within 25 yd for 3 s and **no plate of any voice player is on screen**, only once, and never when a voice player's plate is showing.
2. ECO reacts to the switch (`modules/nameplates.lua` `ApplyGameVisibility` calls Show/HideFriendNameplates from its config, `friendlyNpc`/`friendlyPlayer` = false by default; it only re-applies on bind/style, not every tick).
3. The player was running an **older copy** (the screenshot with no tile and a name-only plate was 0.5.3-0.6.1 "quiet plates"; the README link still serves 0.5.3). The v0.7.0 label in the second pair of screenshots rules this out for that pair.

### What 0.7.1 does (untested in game)

`eqvoice-addon/EqVoice/Icons.lua`: `plateName` / `plateAnchor` (read the client's own name text even when ECO hides its overlay and its own text is empty; anchor to what the player sees), `voicePlateShowing`, `nearestVoice`, `ensurePlates` (grace 3 s, within 25 yd, once, release when voice players leave), `plates()` (icon on its own frame under `UIParent`, tile via `Icons.FitTile`, shown with the plate via `Icons.ShowHost`). `/eqvc plates off` disables the switch entirely; `/eqvc overhead off` removes the icons.

### What to do next (in this order)

1. Ask the player to `/reload`, confirm the panel title shows **v0.7.1**, stand near the ally with Overhead **On**, and say whether the plate stays and the icon shows.
2. If the plate still vanishes: add a **probe command** (`/eqvc plates probe`) that prints `FRIENDNAMEPLATES_ON`, the number of plate frames, and per plate: name text, shown, bar colour, whether ECO has it (`np.plates[frame]`, `overlay` shown); then calls `HideFriendNameplates()` / `ShowFriendNameplates()` one at a time with a printout between, to **learn whether they set or toggle**. Do this before more guessing.
3. If the switch cannot be used safely: choose between
   - (A) icons only on plates the player already has (Shift-V, or ECO "Show Friendly Players"), plus the one-time chat hint that already exists (this is 0.6.8's behaviour);
   - (B) **ECO integration** (the player owns ECO): a small API in ECO's nameplates module so ECO draws/keeps friendly player plates in its own style and exposes the name FontString for EqVoice to anchor to;
   - (C) a world-to-screen projection from `UnitPosition` plus camera yaw/zoom (no camera yaw API exists on 1.12; would need calibrating from any visible plate).
4. Do not change nameplate font/colour/text, and never draw a name of our own on a plate (0.5.0-0.5.1 did, in gg sans, and the player objected). A test (`voice_ui_tests.lua`, "overhead icons" block) fails if any code touches a plate part.

### History of the nameplate approach (why each step was taken)

| Version | Approach | Result |
|---|---|---|
| 0.3-0.5.1 | Force friendly plates on, make them "quiet" (hide bar/border/level), draw a name label in gg sans when ECO hid its own | Player: "nameplate font changed". |
| 0.5.2-0.5.3 | Label removed; forced+quiet plates kept | Player: "why are we still using new nameplates" (NPC names everywhere, plates changed). |
| 0.6.2 | No forcing at all; icon only on existing plates | Icons "not showing" (friendly plates off; ECO's empty name text hid them from lookup). |
| 0.6.3 | Force on, hide everything except voice players' names | Player: "hiding all nameplates". |
| 0.6.4 | Force on, touch nothing | Plate hiding reported again after 0.6.5-0.6.7. |
| 0.6.5-0.6.6 | Also widened SuperWoW `NameplateRange` to 80 | Removed in 0.6.7 (suspect; one left at 80 is put back once from `db.ui.plateRangeWas`). |
| 0.6.8-0.6.9 | Switch off by default; icon on its own `UIParent` frame, 4 px above the name | "Icons still not showing". |
| 0.7.0 | Switch on again by default; icon on a steel-blue tile | Plate vanished with Overhead On (the screenshots above). |
| 0.7.1 | Presence-based use of the switch, client name text for lookup | **Awaiting the player's test.** |

## State of everything else (all pre-release, none confirmed in the real game)

- **Releases on GitHub (Equadis/EqVoice):** v0.6.0-v0.6.7 are published pre-releases; README's one-click link still serves **v0.5.3** (last full release). **Unpublished (local commits only): 0.6.8, 0.6.9, 0.7.0, 0.7.1.** The player says "publish X" to approve a release; push + `gh release create vX <EqVoice-Setup.exe> --prerelease`.
- **Voice icon levels (0.6.6):** idle speaker; quiet = one bubble and carries less far; normal = two bubbles; yelling = two red bubbles and carries further; not connected = red X. Deafened-with-mic-open shows the deaf icon plus the voice icon.
- **Direction (0.6.5):** axes are learned from running, needs a turn; first guess for ClassicAPI's four-value `UnitPosition` is west-first (right on 3 of 4 accounts).
- **Changing character (0.6.7):** the app follows it within ~6 s, no restart (`bridge/filesource.py`).
- **Sliders (0.6.8):** a click on the bar sets the value; the volume sliders are live while dragging.
- **Unverified in game:** direction angles, whisper, RNNoise with a real mic, quiet/yelling levels, player window, the indicator (26 x 30, top-centre start), unit-frame icon click with a second player.

## How to work on it

- Addon: `D:\EqVoice\eqvoice-addon\EqVoice\` (Lua 5.0: no `...`, no `#`, no `%` operator; run `python tools/lint_lua50.py`). Tests: `luajit eqvoice-addon/tests/run.lua` (2108 passing). App tests: `cd eqvoice-client && python -m pytest -q --ignore=tests/test_e2e_browser.py` (+ the e2e file separately; `test_tray::test_hide_windows...` fails only while the player's real EqVoice engine is running).
- Build the installer: `python tools/build_installer.py --hub-url wss://eqvoice-signalling-prod.eqvoice.workers.dev/ws --join-code "$(head -1 /d/Octowow/.eco-work/eqvoice-secrets/join-code.txt)"`, copy `dist/EqVoice-Test-Setup-<v>.exe` to `D:\Octowow\.eco-work\release\EqVoice-Setup.exe`.
- Install on the player's PC: stop `EqVoice*` processes, copy `build\installer-payload\EqVoice.exe` to `C:\Users\Oliver Duncan\AppData\Local\Programs\EqVoice\`, `robocopy D:\EqVoice\eqvoice-addon\EqVoice D:\Octowow\Interface\AddOns\EqVoice /E`, start it. **The game must `/reload`: addon Lua is read at login.**
- Do not publish without the player's "publish". Do not put the join code anywhere public.
