# EqVoice

Opt-in proximity voice chat for OctoWoW (WoW 1.12.1): walk near another player who runs EqVoice and you can
talk; walk away and the call fades out. Players need share nothing but a realm: no group, guild, friend list,
room or nameplates. Peer-to-peer WebRTC audio, a tiny hub that never touches voice, no OctoWoW server access, no
DLL, no coordinates sent anywhere.

**How strangers find each other.** The hub keeps a roster of the characters online on each realm. Your desktop
client hands the roster's names to the addon (a file in the game folder); the addon looks each name up in the game
and measures the exact distance; the client reports those distances to the hub; the hub connects two players only
when **both** report each other at distances that agree; then WebRTC carries the voice directly between them and
the distance sets the volume. If anything goes quiet (the game, the bridge, the hub), voice fades out.
Details: `docs/06-roster-pipeline.md`.

**Status.** Verified in the real game (2026-10-16/17, two unrelated characters on one PC): identity from the game, registration, the roster
reaching the addon through the file bridge, exact-name distances, mutual proximity, WebRTC, push-to-talk voice **in both directions**, volume
falling with distance, leaving range, teardown. Everything else is covered by tests (`python tools/run_tests.py --all`, including real
Chromium engines exchanging audio). **Next, in this order:** a two-PC test over the internet (`docs/07`), five players in one place and
big rosters (`docs/09`, measured), the real `/say` range (`docs/08`: a calibration mode is built, the measurement is yours to run), and
the Cloudflare deployment (`eqvoice-signalling-cf/DEPLOY.md`: prepared, **not deployed**).

## Install (players)

Unzip and double-click **`EqVoice.exe`**: it finds the game, adds the addon and opens its window. In the game, click the small EqVoice
button (or type `/eqvc`) for the voice panel: open mic by default, mute, push to talk, volume, who is near. Nothing else to install (no
Python); `docs/11-install-and-ingame-ui.md` has the details, the commands and how the exe is built (`python tools/build_exe.py`).

## Try it without WoW (5 minutes)

Needs Python 3.12 with `aiohttp` and `cryptography`, and Edge or Chrome.

```
python tools/run_sim.py --fake-media --demo
```

A hub and two clients, each with a **simulated game** that speaks the real file formats. Bobby walks towards Alice,
talks, and leaves; watch the *Nearby* table and the *Pipeline* panel in either window. Edit the pretend world
yourself with `python tools/sim_world.py <home>/world.json pair Alice Bobby 18 19` (Alice's game sees Bobby at 18
yards, Bobby's sees Alice at 19: voice connects) or `see Alice Bobby 60` (the two disagree: the link ends).

## Try it in the game

```
python tools\realtest.py doctor        pre-flight, no game needed
python tools\realtest.py run           local Cloudflare hub + one desktop client per logged-in character + a live 20-step checklist
python tools\realtest.py stop          end a run that is still going (one run at a time)
```

Install the addon first (`powershell tools\install-addon.ps1 -AddOns "D:\Octowow\Interface\AddOns"`, then `/reload` in
game). Then log two unrelated characters in (same realm; two game instances from one folder are fine). `realtest run`
finds them from the addon's files, starts a client for each, and prints each boundary as it passes: identity from WoW,
registration, roster, the name reaching the addon, the addon's exact-name lookup, the distance coming back, mutual
proximity, WebRTC, push-to-talk audio both ways, then the walk away: volume falls, audio stops, the connection closes.
When a step does not pass, the checklist names the boundary. Stages (inbound file check, one-player check, the full
test) and the table of what each failure means: `docs/03-onwow-test-plan.md`.

Use headphones and click **Test speakers** in each client window before walking: it plays two rising notes. A red line in a
window (or `!!` in the launcher) means the voice engine is a hidden test browser or Chromium's fake microphone (a beep, not
your voice); `realtest run` stops leftover test browsers itself.

## Layout

| Folder | What |
|---|---|
| `eqvoice-addon/EqVoice` | the addon (Lua 5.0, no libraries) |
| `eqvoice-client` | desktop client: Python logic + a Chromium page for WebRTC |
| `eqvoice-signalling` | the hub (Python, aiohttp) |
| `eqvoice-signalling-cf` | the same hub for Cloudflare Workers + one Durable Object (replays the same recorded conversations; runs on the real Workers runtime locally via `npx wrangler dev`; not deployed) |
| `docs` | feasibility, architecture, protocol, test plan, deployment and cost, the roster pipeline, **07 the two-PC internet test, 08 calibrating the `/say` radius, 09 crowds and big rosters, 11 one-click install and the in-game panel** |
| `tools` | `build_exe.py` / `smoke_exe.py` (EqVoice.exe), test runner, Lua 5.0 lint, simulator, installer, load test, vector and conformance generators, SavedVariables reader; `realtest.py` (the real-game run), `make_test_pack.py` (the zip for a friend), `mesh_bench.py` / `scale_bench.py` (crowd and roster costs), `say_calibrate.py` (the `/say` radius), `cost_estimate.py` (Cloudflare free-tier estimate) |
| `protocol` | generated test vectors (`bridge_vectors.json`) and recorded hub conversations (`conformance.json`) shared by implementations |

## Tests

```
python tools/run_tests.py         lint + addon + vectors + conformance + signalling + client   (~2.5 min)
python tools/run_tests.py --all   plus two and four real browsers exchanging audio              (~6 min)
```

## Limits worth knowing

* The roster is realm-wide: everyone running EqVoice on your realm can see your character name while you are
  online (names and random session ids only; no accounts, no addresses). That is the price of finding strangers.
* Nothing can prove a client is the character it names without the OctoWoW server's help: `docs/06`, section 8, says
  what is and is not defended.
* Peers see each other's IP addresses (WebRTC). `relay_only` hides them but needs a TURN server (not included).
* At most 8 simultaneous callers (a mesh does not scale). Measured: five players cost each PC about a tenth of a core and 320 MB for the voice engine and
  30 kbit/s each way while everyone talks; from six up it climbs (`docs/09`).
* Global push-to-talk is Windows-only; elsewhere there is the on-screen button and open mic.
* The game needs ClassicAPI (`UnitTokenFromName`, `UnitDistanceSquared`) and the CustomData file functions.

## License

Not chosen yet. Add one before sharing.
