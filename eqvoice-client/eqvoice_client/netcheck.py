"""Pre-flight network check for a voice test between two PCs on different connections.

    python -m eqvoice_client --netcheck [--signalling wss://host/ws] [--show-ips]

It answers, before anybody starts the game: can this PC reach the signalling hub, can it reach a STUN server over UDP
(the thing WebRTC needs to find its public address), and does its NAT give the same public address to every
destination (good) or a different port per destination (a "symmetric" NAT, which direct connections usually cannot
cross). No TURN relay is involved; this only reports what plain NAT traversal can expect.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import struct
import time
from pathlib import Path
from typing import Optional

from .netdiag import ip_class

STUN_SERVERS = [("stun.cloudflare.com", 3478), ("stun.l.google.com", 19302)]
MAGIC = 0x2112A442


def binding_request() -> tuple:
    tid = os.urandom(12)
    return struct.pack("!HHI12s", 0x0001, 0, MAGIC, tid), tid


def parse_binding_response(data: bytes, tid: bytes) -> Optional[tuple]:
    """(ip, port) the STUN server saw, from a Binding success response; None if it is not one for ``tid``."""
    if len(data) < 20:
        return None
    mtype, mlen, _cookie, rtid = struct.unpack("!HHI12s", data[:20])
    if mtype != 0x0101 or rtid != tid:
        return None
    pos, end, plain = 20, min(20 + mlen, len(data)), None
    while pos + 4 <= end:
        atype, alen = struct.unpack("!HH", data[pos:pos + 4])
        val = data[pos + 4:pos + 4 + alen]
        if atype == 0x0020 and len(val) >= 8 and val[1] == 1:           # XOR-MAPPED-ADDRESS, IPv4
            port = struct.unpack("!H", val[2:4])[0] ^ (MAGIC >> 16)
            ip = struct.unpack("!I", val[4:8])[0] ^ MAGIC
            return socket.inet_ntoa(struct.pack("!I", ip)), port
        if atype == 0x0001 and len(val) >= 8 and val[1] == 1:           # MAPPED-ADDRESS (old servers)
            plain = (socket.inet_ntoa(val[4:8]), struct.unpack("!H", val[2:4])[0])
        pos += 4 + ((alen + 3) & ~3)
    return plain


def probe(servers=STUN_SERVERS, timeout: float = 1.0, tries: int = 3) -> list:
    """Ask each STUN server, from ONE local UDP socket (so mappings are comparable), what address it sees."""
    out = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.bind(("0.0.0.0", 0))
        for host, port in servers:
            res = {"server": "%s:%d" % (host, port), "ok": False, "mapped": None, "rtt_ms": None, "error": ""}
            try:
                addr = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)[0][4]
            except OSError as e:
                res["error"] = "cannot resolve the name (%s)" % e.__class__.__name__
                out.append(res)
                continue
            for _ in range(tries):
                req, tid = binding_request()
                t0 = time.perf_counter()
                try:
                    sock.sendto(req, addr)
                    data, _ = sock.recvfrom(2048)
                except socket.timeout:
                    res["error"] = "no answer"
                    continue
                except OSError as e:
                    res["error"] = e.__class__.__name__
                    break
                m = parse_binding_response(data, tid)
                if m:
                    res.update(ok=True, mapped=m, rtt_ms=round((time.perf_counter() - t0) * 1000), error="")
                    break
            out.append(res)
    finally:
        sock.close()
    return out


def local_address() -> Optional[str]:
    """The address of the network card that would be used to reach the internet (nothing is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def verdict(results: list, local_ip: Optional[str] = None) -> tuple:
    """(code, ok, plain-language reading) of the STUN results."""
    good = [r for r in results if r["ok"]]
    if not good:
        return ("udp_blocked", False, "no STUN server answered over UDP: a firewall, VPN or network blocks it. Direct "
                                      "connections will fail and a relay (TURN) would be needed")
    maps = {r["mapped"] for r in good}
    if local_ip and any(r["mapped"][0] == local_ip for r in good):
        return ("public_host", True, "this PC has a public address of its own (no NAT in the way): direct connections work")
    if len(good) == 1:
        return ("one_stun", True, "only one STUN server answered, so the NAT type cannot be compared; STUN itself works")
    if len(maps) == 1:
        return ("cone", True, "both servers saw the same public address and port: the NAT keeps one mapping for every "
                              "destination (easy to traverse). Direct connections usually work")
    if len({m[0] for m in maps}) > 1:
        return ("multi_wan", False, "the servers saw different public IPs: the connection is load-balanced over several "
                                    "addresses, which breaks direct connections unpredictably")
    return ("symmetric", False, "the servers saw the same public IP but a different port each: a symmetric NAT. It cannot "
                                "be crossed when the other side is also strict; expect direct connections to fail often")


VPN_WORDS = ("wireguard", "openvpn", "wintun", "tap-windows", "vpn", "proton", "nord", "mullvad", "tailscale", "zerotier",
             "anyconnect", "fortinet", "pulse secure", "globalprotect", "hamachi")


def vpn_adapters(adapters) -> list:
    """The entries of ``adapters`` ('Name | Description' strings) that look like a VPN."""
    return [a for a in adapters if any(w in a.lower() for w in VPN_WORDS)]


def active_adapters() -> list:
    """Windows: 'Name | Description' of every network adapter that is up (empty elsewhere or on error)."""
    if os.name != "nt":
        return []
    import subprocess
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                              "Get-NetAdapter | Where-Object Status -eq 'Up' | ForEach-Object { $_.Name + ' | ' + $_.InterfaceDescription }"],
                             capture_output=True, text=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


async def hub_check(url: str, join_code: str = "", timeout: float = 8.0) -> dict:
    """Connect to the signalling hub and read its hello. Never registers anything."""
    import aiohttp
    res = {"url": url, "ok": False, "ms": None, "error": "", "version": None, "ice": [], "join_code": False}
    t0 = time.perf_counter()
    try:
        async with aiohttp.ClientSession() as s:
            try:
                wst = aiohttp.ClientWSTimeout(ws_close=timeout)
            except AttributeError:          # older aiohttp
                wst = timeout
            async with s.ws_connect(url, timeout=wst, heartbeat=None) as ws:
                msg = await asyncio.wait_for(ws.receive_json(), timeout)
                res.update(ok=msg.get("t") == "hello", ms=round((time.perf_counter() - t0) * 1000), version=msg.get("v"),
                           ice=[u for srv in msg.get("ice", []) for u in srv.get("urls", [])], join_code=bool(msg.get("join_code")))
                if not res["ok"]:
                    res["error"] = "the server answered, but not like an EqVoice hub"
    except asyncio.TimeoutError:
        res["error"] = "timed out: no answer (wrong address, hub not running, or a firewall)"
    except Exception as e:                                    # noqa: BLE001
        res["error"] = "%s: %s" % (e.__class__.__name__, str(e)[:120])
    return res


def mask(addr: tuple, show: bool) -> str:
    ip, port = addr
    return "%s:%d" % (ip, port) if show else "%s address, port %d" % (ip_class(ip), port)


def run(signalling_url: str, join_code: str = "", show_ips: bool = False, report: Optional[Path] = None) -> int:
    lines = []

    def say(s=""):
        lines.append(s)
        print(s, flush=True)

    say("EqVoice network check")
    say("")
    hub = asyncio.run(hub_check(signalling_url, join_code))
    if hub["ok"]:
        say("  hub          OK    %s answered in %d ms (protocol v%s%s)" % (signalling_url, hub["ms"], hub["version"],
                                                                           ", needs a join code" if hub["join_code"] else ""))
        say("               it offers STUN/TURN: %s" % (", ".join(hub["ice"]) or "nothing"))
    else:
        say("  hub          FAIL  %s: %s" % (signalling_url, hub["error"]))
    stun = probe()
    for r in stun:
        if r["ok"]:
            say("  stun         OK    %-28s saw %s (%d ms)" % (r["server"], mask(r["mapped"], show_ips), r["rtt_ms"]))
        else:
            say("  stun         FAIL  %-28s %s" % (r["server"], r["error"]))
    code, ok, text = verdict(stun, local_address())
    say("  nat          %s  %s" % ("OK  " if ok else "WARN", text))
    vpns = vpn_adapters(active_adapters())
    if vpns:
        say("  vpn          WARN  a VPN-like adapter is active: %s" % "; ".join(vpns))
        say("               A VPN does the NAT for you on its servers and often breaks direct connections. If the voice test fails,")
        say("               run this check and the test again with the VPN switched off.")
    say("")
    allok = hub["ok"] and ok
    say("RESULT: " + ("ready for a voice test." if allok else "fix the FAIL/WARN lines above first, or send this text to whoever is running the test."))
    if report is not None:
        try:
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(time.strftime("%Y-%m-%d %H:%M:%S") + "\n" + "\n".join(lines) + "\n" + json.dumps(
                {"hub": hub, "nat": code, "stun": [{k: (v if k != "mapped" else ip_class(v[0]) if v else None) for k, v in r.items()}
                                                   for r in stun]}) + "\n", encoding="utf-8")
        except OSError:
            pass
    return 0 if allok else 1
