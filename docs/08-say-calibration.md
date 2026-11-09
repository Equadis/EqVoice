# 08 — How far does `/say` carry? (calibrating the voice radius)

EqVoice's default radius started as an assumption (**40 yards**, MaNGOS's default listen range for `/say`). **Measured on this server (2026-10-17, three passes): `/say` is heard out to 40.8 yd and not at 41.0 to 41.2 yd, so the default is now 41.** That is 40 plus about a yard for the two characters' bodies. Use this document to re-measure on another server.
The addon can measure it with two of your characters and the desktop client's logs. The radius stays configurable; this only tells
you what to configure.

## What it measures

While calibration runs, each character `/say`s `ovcal <n> <interval>` every 2 s and notes the **exact distance** to the other one
(the same name lookup the voice pipeline uses: `UnitTokenFromName` + `UnitDistanceSquared`; it works far beyond `/say` range, so the
distance is known even when the text is not delivered). When the other character's line arrives (`CHAT_MSG_SAY`) the distance at that
moment is noted too. A gap in the numbers is a line that did not arrive. `tools/say_calibrate.py` joins both sides and fits the
cutoff: the distance below which `/say` was delivered and above which it was not.

## Procedure (about 5 minutes, two characters, both in the world)

1. Install the addon (`powershell tools\install-addon.ps1 -AddOns "D:\Octowow\Interface\AddOns"`) and `/reload` in both games.
2. Start the desktop clients so the addons' lines are logged: `python tools\realtest.py run`
3. On **each** character (exact names, capital letters matter):
   ```
   /ev say with <the other character>
   /ev say start
   ```
   **A character that cannot `/say` (muted, no permission) must use `/ev say listen` instead of `start`**: it then only listens and the other character speaks.
   Never `start` on a character that cannot speak: the server drops its lines, so they would count as "not heard" at every distance and ruin the fit.
   (Then the walk is the same: the speaker stands still or walks, the listener the other way round; only one direction is measured, which is all the range needs.)
4. Leave one character standing still. Walk the other **slowly** in a straight line away to about 80 yards, then back; do it twice.
   (Slowly: the distance is taken when the line is sent, and a fast walker moves several yards in a second.)
5. `/ev say stop` on both (or `/ev say start 150` to stop by itself after 150 s). `/ev say report` prints this side's view:
   farthest heard, nearest skipped, and a 5-yard table.
6. Ctrl+C in the realtest terminal, then:
   ```
   python tools\say_calibrate.py
   ```
   It prints the table, the fitted range, and a recommendation. Add `--apply-default` to write the fitted radius into the client's
   default configuration (`config.py`), or set it for one player with `--say-radius NN`, or `proximity.say_radius` in `config.json`.

If the two characters are on different factions, the other side's `/say` arrives in their language (garbled): the analyzer matches it
to the sent line by time instead of by number. If they are on different PCs, send the logs (the window's **Save report** button, or the `logs` folder of each client
home) and pass them with `--log A=path --log B=path`.

## Reading the result

* A sharp edge (everything heard up to X, nothing beyond) gives a confident range. A soft edge or few samples prints a warning.
* "Nothing was missed": you never went far enough; the number is only a lower bound.
* If the range is wider than about 42 yards, the hub's link radius (default 45) must be raised too (`LINK_RADIUS` for the Worker,
  `--link-radius` for the Python hub); the client warns when its radius does not fit the hub's.
* What to set: `say_radius` = the fitted range; `leave_hysteresis` 3 yards is fine. (Since the volume curve was redone, how loud a voice
  is at each distance no longer follows the `/say` range: it is full to 30 yd, quiet at 70 and gone at 85, see doc 11. `say_radius` is what
  the game is asked to scan for, and the least a voice can reach.)

## Files

`eqvoice-addon/EqVoice/Calibrate.lua` (the mode), `tools/say_calibrate.py` (the fit), `eqvoice-addon/tests/say_sample.lua` and
`eqvoice-client/tests/test_say_calibrate.py` (the addon's real words are parsed by the analyzer in the test suite).

## Fine pass: pinning the edge

The first real run (2026-10-17, Testststsets speaking, Dgawdawdad listening, a walk out to 128 yd and back) found: every line heard up to 36.2 yd, every line
missed from 41.0 yd, nothing measured in between. A second pass at one line a second then lost eight lines in a row inside the range (the server's chat flood limit is the likely cause; two seconds between lines is safe and the addon refuses less), but heard a line at 40.8 yd. All three passes together pin the edge to 40.8 to 41.2 yd. To repeat or narrow it:

| Character | Commands |
|---|---|
| both | `/reload` (loads the newest addon) |
| **Dgawdawdad** (listener, stands still) | `/ev say with Testststsets`, `/ev say listen` |
| **Testststsets** (speaker, walks) | `/ev say with Dgawdawdad`, `/ev say show`, `/ev say start` |
| Testststsets | walk (do not run) from about 34 yd away to about 46 yd and back, twice, as slowly as you can; the chat shows `saycal sent N to Dgawdawdad at XX.X yd` for every line, so you can see where you are |
| both | `/ev say stop`, then `/reload` (this is what writes the data to disk) |
| terminal | `python tools\say_calibrate.py --savedvars` |
