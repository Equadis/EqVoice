# 07 — The two-PC internet test

Goal: two people on two different PCs and networks install EqVoice, walk unrelated characters near each other, and talk over a
**direct** WebRTC connection (STUN for NAT traversal, no TURN relay).

## The verdict, in one line

After a call connects, and again 5 seconds later with real statistics, each side writes **one line** to `logs\eqvoice.log`:

```
TRACE desktop webrtc_result name=Bobby result=DIRECT_STUN phase=settled pair=srflx>srflx proto=udp local_network=public remote_network=public
      turn_used=no rtt_ms=38 jitter_ms=2.5 loss_in_pct=0.4 loss_out_pct=0.0 setup_ms=1234
```

| `result=` | Meaning |
|---|---|
| `DIRECT_HOST` | direct peer to peer on host addresses (same network, or both public): no NAT had to be crossed |
| `DIRECT_STUN` | direct peer to peer **through NAT**, using the public addresses STUN discovered (`srflx`, or `prflx` learned while checking): the case that proves normal NAT traversal works |
| `RELAY_TURN` | carried by a TURN relay (not direct; only possible if a TURN server is configured, which it is not by default) |
| `FAILED` | no path formed; `reason=` and `explanation=` say why (STUN blocked here, the other side's addresses never arrived, symmetric NAT or firewall...). The call is retried with back-off |

`pair=` is the selected ICE candidate pair (local type > remote type); `rtt_ms`, `jitter_ms`, `loss_in_pct` (what we lost of their audio) and
`loss_out_pct` (what they lost of ours) are the numbers at that moment; `setup_ms` is the time from the call being started to it being up.
`python tools\realtest.py report --friend <their zip>` prints both sides' verdicts together under **WebRTC result per call**.

## What the client now tells you

Every attempt writes these `TRACE desktop` lines (log: `logs\eqvoice.log`; no real IP addresses unless `--show-ips`):

| Line | Meaning |
|---|---|
| `webrtc_candidate side=local\|remote type=host\|srflx\|relay proto network=` | one ICE candidate; `srflx` = STUN found a public address; `network` = private/public/cgnat/mdns |
| `webrtc_gathered gather_ms= local_candidates= public_address_found=` | address gathering finished; `public_address_found=no` = STUN did not work from this PC |
| `webrtc_ice_error code= server=` | the browser could not use a STUN/TURN server |
| `webrtc ice= connection= gathering= ms=` | state transitions, with milliseconds since the attempt began |
| `webrtc_connected ice_ms= total_ms= role=` | connected; `total_ms` includes the signalling round trips |
| `webrtc_path path= pair= proto= turn_used=` | **the verdict**: `direct-nat` (STUN traversal worked), `direct-host` (same network), `relayed` (TURN) |
| `webrtc_stats rtt_ms= jitter_ms= loss_in_pct= loss_out_pct= in_kbps= out_kbps=` | every 5 s while connected |
| `webrtc_failed reason= explanation= local_candidates= remote_candidates=` | why it did not form (also shown in the window next to the player) |

Failure reasons: `no_public_address` (STUN blocked here), `no_remote_candidates`, `remote_no_public_address` (STUN blocked
there), `nat_traversal_failed` (both found addresses, no path: a symmetric NAT or firewall; needs TURN), `dropped` (was up, then lost).
A failed attempt retries with back-off (3, 6, 12, 24, 48 s) and never disturbs other peers.

`python -m eqvoice_client --netcheck` (the window's **Check my connection** button) answers before anyone starts the game: hub reachable, STUN
reachable over UDP, and whether the NAT gives the same public address and port to every destination (`cone`: good) or a different
one (`symmetric`: direct connections usually fail), and whether a VPN adapter is active.

## Result of the first check on the development PC (2026-10-17)

`NETCHECK` reported a **symmetric NAT** and an active **ProtonVPN (WireGuard)** adapter: the public port seen by Cloudflare's STUN
differed from the one seen by Google's, and all traffic leaves through the VPN (a probe bound to the physical Ethernet address got
no answer). Direct P2P from this PC to a friend behind an ordinary home router will therefore often fail while the VPN is on.
Run the friend test **with the VPN off** first (and again with it on); that comparison is the answer to "does normal NAT
traversal work".

## Running the test from this PC (cloudflared is installed)

`cloudflared` 2026.10.1 (official release, SHA-256 checked) is at `D:\Octowow\.eco-work\cloudflared\cloudflared.exe`. It is used only for the
temporary test address; **nothing is deployed to Cloudflare**. With ProtonVPN **off**, after the `/reload`, `/ev diag scale` and `/ev say` steps:

```
python tools\realtest.py run --tunnel --only <the character that will meet your friend>
```

It starts the hub with a random join code, opens the tunnel, builds `dist\EqVoice-test-pack.zip` with that address and code inside, starts your client,
and prints your connection lines live (`webrtc_result` among them). Send your friend the zip and the message below. The address works only while that window
stays open; Ctrl+C ends the tunnel, the hub and your client, and prints the final checklist with your side's verdict. When the friend's **Save report** zip
arrives: `python tools\realtest.py report --friend <zip>`.

## Reaching the test hub

The friend's PC must reach a hub. Nothing is deployed to Cloudflare. Options, cheapest first:

1. **Cloudflare quick tunnel** (recommended): `cloudflared tunnel --url http://127.0.0.1:8787` prints a temporary
   `https://<words>.trycloudflare.com`. No account, nothing deployed; it forwards the internet to the hub running on your PC for as long as
   the process lives. `python tools\realtest.py run --tunnel` starts the hub with a random join code, starts the tunnel, builds the
   friend's zip with the address and code inside, and prints live connection lines. It needs `cloudflared.exe` (not bundled and
   never downloaded by the tools): put it at `D:\Octowow\.eco-work\cloudflared\cloudflared.exe` or set `%CLOUDFLARED%`.
2. **Port forward** TCP 8787 on your router to this PC and give the friend `ws://<your public IP>:8787/ws` (unencrypted;
   exposes your home address; allow `node.exe` through the Windows firewall). Build the zip with
   `python tools\make_test_pack.py --hub-url ws://IP:8787/ws --join-code CODE`.
3. A hub on any machine with a public address: `python -m eqvoice_signalling --host 0.0.0.0 --port 8787 --join-code CODE`.

## The friend's side (what to send them)

Send `dist\EqVoice-test-pack.zip` (one file) and this message:

> EqVoice voice test, about 10 minutes. You need: Windows, headphones + a mic, and the OctoWoW game you normally play. Turn any VPN off.
> Your character must be on **N'Zoth**.
> 1. Unzip the file (right-click, Extract All).
> 2. Double-click **EqVoice.exe**. If Windows says "protected your PC", click More info, then Run anyway. It finds your game, adds its addon and
>    opens a small window: leave it open. If Windows asks about network access, click Allow.
> 3. Start WoW and log in (type **/reload** once if you were already in). A small microphone button appears beside the little menu buttons at the bottom of the screen (at the top of the screen
>    if you have hidden those) and should be green.
>    Click it (or type /eqvc) for the voice panel.
> 4. Walk up to **my character** (I'll tell you where; stay within 10 yards). Your microphone is open: just talk, and I will too. We should hear each other, both ways.
> 5. Walk away from me in a straight line: my voice should stay full to about 30 yards, be quiet by 70 and gone by 85 (a shout stays full to about 45). Close up we should sound louder (twice as loud right next to each other), easing smoothly back to normal by 10 yards, with no sudden jump. Walk back: it returns.
> 6. When done (or if anything failed): in the EqVoice window scroll down to Diagnostics, press **Save report**, and send me the zip it puts on your Desktop.

## Reading the result

`python tools\realtest.py report --friend <their zip>` merges their log with yours and prints the 20-step checklist; the lines to
read first are `webrtc_path` and `webrtc_failed` in each side's log. Success is `path=direct-nat turn_used=no` on both sides with
`audio_rx RECEIVING` both ways.
