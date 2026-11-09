# 10 — Step by step: what to do now

**Which test?** Both, in this order. Phase A is **solo, two WoW windows on this PC** (it needs two of your characters): it measures `/say` range and
the in-game lookup cost. Phase B is the real goal, **two different PCs on two different networks**: you on this PC with one character, a friend on
theirs with another. Two windows on one PC can never test internet NAT traversal (both sides share one network, so the call always connects
`host>host`); that part passed already. Only Phase B answers "does it work over the internet".

You will need: both characters (Dgawdawdad, Testststsets) on N'Zoth, headphones, ProtonVPN **off**, this PC awake for the whole of Phase B
(sleep switched off), and a friend with the same OctoWoW client (it has ClassicAPI.dll), Windows (Edge is built in), headphones and a microphone.

---

## Phase A: solo, two windows (about 25 minutes)

**A1. Free the ports.** The tunnel run in terminal tab `c2` is waiting for a character. Stop it (click in the tab, Ctrl+C) so Phase A can use
both characters. (Phase B restarts it; its address changes every time, which is why the friend's zip is rebuilt then.)

**A2. Load the new addon.** Log in both characters. In each, type `/reload`, then `/ev say`. If it prints a line starting `/ev say with <Name>...`, the new addon is
loaded. (If it prints the general help, the old addon is still loaded: close the game, make sure `python tools\realtest.py doctor` says the addon is
"identical to the repository", log in again.)

**A3. Start your clients.** In a terminal:
```
python tools\realtest.py run
```
It opens one EqVoice window per character. Leave it running through A4 and A5. (Ctrl+C at the end of A5 prints the checklist.)

**A4. Lookup cost of big rosters (22 seconds).** Stand still in one game window, don't move the camera, type:
```
/ev diag scale
```
After about 22 s it prints five rows (50, 100, 250, 500, 1000 names): frame time, fps, microseconds per lookup, sweep time, roster-file parse time.
**Copy those five lines and send them to me.** (They are also saved: after `/reload` or logout, `python tools\read_savedvars.py "D:\Octowow"` prints them.)

**A5. How far does `/say` carry (about 8 minutes).** Dgawdawdad has no `/say` permission, so it only **listens** and **Testststsets speaks** (if Testststsets cannot speak
either, use Equadis as the speaker). Never `/ev say start` on a character that cannot speak: its lines would count as "not heard" at every distance.
1. In **Dgawdawdad**: `/ev say with Testststsets`, then `/ev say listen`.
2. In **Testststsets**: `/ev say with Dgawdawdad`, then `/ev say start`. (Chat shows its `ovcal` lines: that is the test.)
3. Keep Dgawdawdad standing still. Walk Testststsets **slowly** in a straight line away from it, to about 80 yards, and back. Do it twice.
4. `/ev say stop` in both. `/ev say report` in either shows that side's view.
5. Ctrl+C the `realtest run` terminal, then:
   ```
   python tools\say_calibrate.py
   ```
   It prints a table and "/say carried to about NN yards" with a recommendation. **Send me that output.** If it says "nothing was missed", walk further next time.
   Don't apply it yourself: send me the output and I'll set the default (a few test expectations need updating with it).

---

## Phase B: two PCs over the internet

### B1. Your side: start the test endpoint
1. Make sure ProtonVPN is **off** (quit it, not just paused) and Windows will not sleep.
2. Log in **one** character: Dgawdawdad (the other can stay logged out; two of your characters near the friend would just confuse the audio).
3. In a terminal:
   ```
   python tools\realtest.py run --tunnel --only Dgawdawdad
   ```
   It prints `TUNNEL UP: ... wss://<words>.trycloudflare.com/ws` and `Friend's pack: D:\EqVoice\dist\EqVoice-test-pack.zip`, opens the EqVoice window, and
   starts printing live lines. **Leave this window open for the whole test: closing it ends the address.**
4. Windows may ask whether `msedge.exe` / `python.exe` may use the network: choose **Allow**.
5. Check the EqVoice window's top line says *Ready: Dgawdawdad on N'Zoth*. Press **Test speakers** (two rising notes), and make sure the mic bar moves when you talk.

### B2. Send your friend the zip and this message
Send `D:\EqVoice\dist\EqVoice-test-pack.zip` (one file) with:

> EqVoice voice test, about 10 minutes. You need: Windows, headphones + a mic, and the OctoWoW game you normally play. Turn any VPN off.
> Your character must be on **N'Zoth**.
> 1. Unzip the file (right-click, Extract All).
> 2. Double-click **EqVoice.exe**. If Windows says "protected your PC", click More info, then Run anyway. It finds your game, adds its addon and
>    opens a small window: leave it open. If Windows asks about network access, click Allow.
> 3. Start WoW and log in (type **/reload** once if you were already in). A small microphone button appears beside the little menu buttons at the bottom of the screen (at the top of the screen
>    if you have hidden those) and should be green.
>    Click it (or type /eqvc) for the voice panel.
> 4. Walk up to **<my character>** (I'll tell you where; stay within 10 yards). Your microphone is open: just talk, and I will too. We should hear each other, both ways.
> 5. Walk away from me in a straight line: my voice should stay full to about 30 yards, be quiet by 70 and gone by 85 (a shout stays full to about 45). Close up we should sound louder (twice as loud right next to each other), easing smoothly back to normal by 10 yards, with no sudden jump. Walk back: it returns.
> 6. When done (or if anything failed): in the EqVoice window scroll down to Diagnostics, press **Save report**, and send me the zip it puts on your Desktop.

Coordinate by text or voice chat (Discord). Agree on a meeting spot before they log in.

### B3. The meeting (about 10 minutes)
| You | Your friend | What you should see |
|---|---|---|
| Stand still at the spot. Watch the terminal. | Logs in, `/reload`, walks to you. | within ~45 yd: `hub_link ... UP`, then `webrtc_candidate`, `webrtc_connected`, and **`webrtc_result ... result=DIRECT_STUN`** (or `DIRECT_HOST`) |
| Hold **F9**, say "one two three". | Listens. Then they hold F9 and answer. | the other side says they hear you clearly; yours says `audio_rx ... RECEIVING` |
| Both talk at once, then each alone. | | no echo (headphones); `webrtc_stats` every 5 s with rtt/jitter/loss |
| Walk away from them slowly. | Tells you when the voice fades. | `audio_gain` falls; at about 43 yd `OUT_OF_RANGE_GRACE`, 8 s later the call closes (`webrtc_closed`) |
| Walk back in range. | | the call comes back by itself |

### B4. Read the verdict (it's one line on each side)
`webrtc_result` in the live window, and `logs\eqvoice.log` on both PCs:

| `result=` | Meaning | What to do |
|---|---|---|
| `DIRECT_STUN` | **Success: direct peer-to-peer through both NATs, found by STUN.** | nothing; this is the goal |
| `DIRECT_HOST` | direct, no NAT crossed (you are on the same network or have public addresses) | fine, but it did not prove NAT traversal: the friend must be on a different network |
| `RELAY_TURN` | carried by a relay | should not happen (no TURN is configured); tell me |
| `FAILED` | no path formed; the line says why | see below |

Failure reasons: `no_public_address` (STUN blocked on that PC: firewall/VPN), `no_remote_candidates` (the other side's addresses never arrived),
`remote_no_public_address` (STUN blocked on the other PC), `nat_traversal_failed` (both found public addresses, no direct path: a strict NAT such as
a symmetric or carrier-grade NAT; needs a TURN relay, which I can add), `dropped` (was up, then lost). Also press **Check my connection** in the EqVoice window on both sides again:
"cone" is good, "symmetric" is why direct fails, "udp_blocked" means no UDP at all.

### B5. End and collect
1. Friend: in the EqVoice window press **Save report** (under Diagnostics) and send you the zip it puts on their Desktop.
2. You: Ctrl+C in the `realtest` terminal (this ends the tunnel, the hub and your client, and prints your checklist and your verdict). Don't close it before the friend has finished.
3. Put the friend's zip somewhere on this PC, then:
   ```
   python tools\realtest.py report --friend "<path to their EqVoice-logs-....zip>"
   ```
   It prints the 20-step checklist with both sides and **WebRTC result per call** (path, ICE pair, rtt, jitter, loss, set-up time for both).
4. Send me that output (and the friend's NETCHECK screenshot if anything was off).

### B6. If it fails
* Nothing connects at all: friend presses **Check my connection** in the window; `hub FAIL` means they cannot reach the address (typo in the pack, or your terminal window was closed).
* Registered but never linked (`proximity` lines but no `link_up`): the two games are not seeing each other. Both must be on N'Zoth, loaded near each other (same area, within ~40 yd), addon loaded.
* Linked, but `FAILED`: use the reason table above. Then try with the roles swapped (friend hosts) only if I say so.
* Connected, no audio: look at `audio_rx`/`audio_tx` and the mic pill; `Test speakers` on each side; `mic ... fake=yes` or a red line in the window means a wrong voice engine (close all Edge windows and restart).

## Phase C: three or more people

Two players prove the pipeline; three or more prove the *mesh*: every pair of people near each other has its own call, each listener hears each
speaker at that pair's distance, and one person muting, blocking or leaving changes only what that person hears. (The mesh benchmarks in `09` say what a
PC can carry: five players is about 10-13% of a core and 30 kbit/s each way per call; it climbs from six.)

You need: you plus **two friends** (more is better; the plan below works for any number), each on their own PC, the same OctoWoW game with `nampower.dll`
and `ClassicAPI.dll` loaded (each friend can check with `/ev diag api`: it must say a file api, not `nil`), all characters on N'Zoth, headphones.

### C1. Set up
1. Your side exactly as B1: ProtonVPN off, one character logged in, then
   ```
   python tools\realtest.py run --tunnel --only <your character>
   ```
2. Send **every** friend the **same** `EqVoice-test-pack.zip` with the B2 message. One pack serves any number of friends: it carries the address and join code of
   this run. Ask each to say their character's name when they are in.
3. Agree a spot. Everyone meets there and stays within 10 yards (stand close: with three people everyone is near everyone).

### C2. What to do, and what you should see
| Step | Who | What you should see |
|---|---|---|
| 1. Everyone is at the spot | all | each person's panel says "2 players in range, 2 connected" (with three of you); the hub shows three `link_up` lines, one per pair |
| 2. Each person says their name in turn | one at a time | the other two hear them; the speaker's icon (speaker with waves) shows on their name on the others' screens while they talk, the plain speaker when they are quiet |
| 3. Two talk at once, then all three | all | everyone hears everyone; no echo (headphones); `audio_rx` is `RECEIVING` for each pair |
| 4. **Friend A mutes themselves** (the key) | A | B and C hear nothing from A; A's icon changes to the slashed microphone on B's and C's screens within a second; B and C still hear each other |
| 5. **You block friend B** (Blocklist, type the name, Block) | you | only *you* stop hearing B; C still hears B, B still hears you. Unblock in the window |
| 6. **Friend C walks away** to 60 yards, then 90 | C | C's voice fades for you and A (0.13 at 70 yd, silent at 85); A and you still hear each other at full volume; at 90 the call to C closes 8 s after the last reading, and C hears nobody |
| 7. C walks back | C | the calls come back by themselves |
| 8. Someone **deafens** | any | they hear nobody and nobody hears them; their icon shows the headphones |
| 9. Someone **leaves the game** | any | their calls close; the others' calls are untouched |
| 10. **Set up mic** (each person, once) | all | the overlay asks for silence, talking, a shout; the result says the noise gate's new level |

### C3. Collect
1. Each friend presses **Save report** in the EqVoice window and sends you the zip on their Desktop.
2. You Ctrl+C in the `realtest` terminal, then give the report **one `--friend` per friend**:
   ```
   python tools\realtest.py report --friend "<friend A's zip>" --friend "<friend B's zip>"
   ```
   It lists the checklist with every client (yours and "friend", "friend2", ...) and the WebRTC result for every call.
3. Send me that output, and tell me anything that sounded wrong (who, when, how far apart).

Things to watch for: a call that never forms between two friends (they are both behind strict NATs: look for `FAILED` in `webrtc_result`); a friend whose panel says
"0 players in range" while the others see them (their game is missing `nampower.dll` or `ClassicAPI.dll`, see above); more than about five calls on one PC
getting choppy (a PC problem, not a hub one: `09` has the numbers).

## Afterwards
Tell me the verdict lines and the `say_calibrate` and `/ev diag scale` output. From those: the default radius, the lookup budget, and whether a TURN relay is needed.
