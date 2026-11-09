"""Reading ICE: what kind of path a WebRTC connection took, and, when none formed, the likeliest reason.

Pure functions, no I/O. The page reports candidates (as SDP lines), the selected pair, ICE errors and timings;
``peers.py`` collects them per attempt and uses this module to put words on them for the log and the window.

Candidate types, as WebRTC names them:

    host    an address on this PC's own network card (LAN address, or a public one)
    srflx   "server reflexive": the public address a STUN server saw. Having one means STUN worked
    prflx   "peer reflexive": learned from the other side while checking connectivity (also NAT traversal)
    relay   an address on a TURN server. Having one means traffic can go through a relay

A selected pair is *direct* unless one end is a relay. Direct through NAT (srflx/prflx on either end) is the case that
matters for strangers: it is what "normal NAT traversal succeeded" looks like.
"""
from __future__ import annotations

import ipaddress
import re
from typing import Optional

TYPES = ("host", "srflx", "prflx", "relay")

# candidate:<foundation> <component> <protocol> <priority> <address> <port> typ <type> [raddr .. rport ..] [tcptype ..]
_CAND = re.compile(r"candidate:\S+\s+(\d+)\s+(\w+)\s+(\d+)\s+(\S+)\s+(\d+)\s+typ\s+(\w+)(?:\s+raddr\s+(\S+)\s+rport\s+(\d+))?")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def ip_class(addr: Optional[str]) -> str:
    """A coarse, privacy-preserving description of an address: private, public, cgnat, ... (never the address itself)."""
    if not addr:
        return "unknown"
    if addr.lower().endswith(".local"):
        return "mdns"                      # the browser hides host addresses behind a random .local name
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])
    except ValueError:
        return "hostname"
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link-local"
    if ip.version == 4:
        if ip in _CGNAT:
            return "cgnat"
        return "private" if ip.is_private else "public"
    return "ula" if ip.is_private else "public6"


def parse_candidate(line) -> Optional[dict]:
    """{type, proto, net, address, port, related} for an SDP candidate line (or an RTCIceCandidateInit dict), else None."""
    if isinstance(line, dict):
        line = line.get("candidate")
    if not isinstance(line, str):
        return None
    m = _CAND.search(line)
    if not m:
        return None
    _, proto, _, address, port, ctype, raddr, _ = m.groups()
    return {"type": ctype, "proto": proto.lower(), "net": ip_class(address), "address": address, "port": int(port),
            "related": raddr}


def classify_pair(local_type: str, remote_type: str) -> str:
    """relayed | direct-nat | direct-host | direct (unknown types count as direct)."""
    if "relay" in (local_type, remote_type):
        return "relayed"
    if local_type in ("srflx", "prflx") or remote_type in ("srflx", "prflx"):
        return "direct-nat"
    if local_type == remote_type == "host":
        return "direct-host"
    return "direct"


# The words used in the logs and reports for a call that connected (and FAILED for one that did not).
RESULT_LABELS = {"direct-host": "DIRECT_HOST", "direct": "DIRECT_HOST", "direct-nat": "DIRECT_STUN", "relayed": "RELAY_TURN"}

PATH_WORDS = {
    "relayed": "relayed through a TURN server (not direct)",
    "direct-nat": "direct peer-to-peer through NAT (STUN worked)",
    "direct-host": "direct peer-to-peer on host addresses (same network, or public addresses)",
    "direct": "direct peer-to-peer",
}


def fmt_counts(counts: dict) -> str:
    """host:2,srflx:1 (or '-' when nothing)."""
    return ",".join("%s:%d" % (t, counts[t]) for t in TYPES if counts.get(t)) or "-"


def diagnose(local: dict, remote: dict, errors: list, ice: str = "", turn_configured: bool = False) -> tuple:
    """(code, plain-language reason) a WebRTC attempt did not connect, from what was learned about it.

    ``local`` / ``remote``: candidate type -> count gathered here / received from the other side. ``errors``: ICE
    candidate errors the browser reported (dicts with ``code``, ``url``). Nothing here is certain: it names the
    first thing that was missing, which is where to look.
    """
    if not local:
        return ("no_local_candidates", "this PC's browser engine produced no network addresses at all")
    if not local.get("srflx") and not local.get("relay"):
        stun = [e for e in errors if str(e.get("url", "")).startswith(("stun:", "turn:"))]
        why = " (%d STUN server(s) did not answer)" % len(stun) if stun else ""
        return ("no_public_address", "this PC could not learn its public address%s: UDP to the STUN servers is blocked, "
                                     "or there is no internet" % why)
    if not remote:
        return ("no_remote_candidates", "the other side's network addresses never arrived (their client, or the signalling "
                                        "link between you, dropped them)")
    if not remote.get("srflx") and not remote.get("relay"):
        return ("remote_no_public_address", "the other PC could not learn its public address (its STUN is blocked), so only "
                                            "a local address was offered")
    if turn_configured:
        return ("nat_traversal_failed_with_turn", "both sides found public addresses, no direct path formed, and the relay "
                                                  "(TURN) did not connect either")
    return ("nat_traversal_failed", "both sides found their public addresses but no direct path formed: at least one NAT is "
                                    "probably symmetric, or a firewall blocks UDP between you. A relay (TURN) would be needed; "
                                    "none is configured")
