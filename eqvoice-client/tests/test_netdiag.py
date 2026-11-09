"""Reading ICE and the network check: classification, plain-language diagnosis, the STUN client, and the hub probe."""
import socket
import struct
import threading

import pytest

from eqvoice_client import netcheck, netdiag

SRFLX = "candidate:842163049 1 udp 1677729535 93.184.216.34 54400 typ srflx raddr 192.168.1.5 rport 54400 generation 0 ufrag abcd network-cost 999"
HOST = "candidate:1 1 udp 2113937151 192.168.1.5 54400 typ host generation 0 ufrag abcd network-cost 999"
MDNS = "candidate:2 1 udp 2113937151 8a1b2c3d-1111-2222-3333-444455556666.local 54401 typ host generation 0"
RELAY = "candidate:3 1 udp 41885439 198.51.100.7 61000 typ relay raddr 93.184.216.34 rport 54400 generation 0"
TCP = "candidate:4 1 tcp 1518280447 192.168.1.5 9 typ host tcptype active generation 0"


def test_candidate_lines_are_read_without_keeping_addresses_in_the_kind():
    c = netdiag.parse_candidate(SRFLX)
    assert (c["type"], c["proto"], c["net"], c["address"], c["port"], c["related"]) == ("srflx", "udp", "public", "93.184.216.34", 54400, "192.168.1.5")
    assert netdiag.parse_candidate({"candidate": HOST, "sdpMid": "0"})["net"] == "private", "an RTCIceCandidateInit works too"
    assert netdiag.parse_candidate(MDNS)["net"] == "mdns"
    assert netdiag.parse_candidate(RELAY)["type"] == "relay"
    assert netdiag.parse_candidate(TCP)["proto"] == "tcp"
    assert netdiag.parse_candidate("nonsense") is None and netdiag.parse_candidate(None) is None and netdiag.parse_candidate({}) is None


@pytest.mark.parametrize("addr,kind", [
    ("192.168.0.4", "private"), ("10.1.2.3", "private"), ("172.20.0.1", "private"), ("172.32.0.1", "public"),
    ("100.64.1.1", "cgnat"), ("100.127.255.255", "cgnat"), ("100.128.0.1", "public"), ("169.254.3.4", "link-local"),
    ("127.0.0.1", "loopback"), ("8.8.8.8", "public"), ("fe80::1", "link-local"), ("fd12:3456::1", "ula"),
    ("2606:4700::1111", "public6"), ("abc.local", "mdns"), ("", "unknown"), (None, "unknown"), ("not an ip", "hostname"),
])
def test_addresses_are_described_by_kind(addr, kind):
    assert netdiag.ip_class(addr) == kind


def test_paths():
    assert netdiag.classify_pair("host", "host") == "direct-host"
    assert netdiag.classify_pair("srflx", "srflx") == "direct-nat"
    assert netdiag.classify_pair("host", "prflx") == "direct-nat"
    assert netdiag.classify_pair("srflx", "relay") == "relayed" and netdiag.classify_pair("relay", "host") == "relayed"
    assert netdiag.fmt_counts({"srflx": 1, "host": 2}) == "host:2,srflx:1" and netdiag.fmt_counts({}) == "-"


def test_the_first_missing_thing_is_what_the_diagnosis_names():
    d = netdiag.diagnose
    assert d({}, {"host": 1}, [])[0] == "no_local_candidates"
    code, text = d({"host": 2}, {"srflx": 1, "host": 1}, [{"code": 701, "url": "stun:stun.example:3478"}])
    assert code == "no_public_address" and "1 STUN server(s) did not answer" in text and "UDP" in text
    assert d({"host": 1, "srflx": 1}, {}, [])[0] == "no_remote_candidates"
    assert d({"host": 1, "srflx": 1}, {"host": 1}, [])[0] == "remote_no_public_address"
    code, text = d({"host": 1, "srflx": 1}, {"host": 1, "srflx": 1}, [], "failed")
    assert code == "nat_traversal_failed" and "symmetric" in text and "TURN" in text
    assert d({"host": 1, "srflx": 1, "relay": 1}, {"host": 1, "srflx": 1}, [], turn_configured=True)[0] == "nat_traversal_failed_with_turn"


# ------------------------------------------------------------------ STUN

def stun_response(tid: bytes, ip: str, port: int) -> bytes:
    xport = port ^ (netcheck.MAGIC >> 16)
    xip = struct.unpack("!I", socket.inet_aton(ip))[0] ^ netcheck.MAGIC
    attr = struct.pack("!HHBBH", 0x0020, 8, 0, 1, xport) + struct.pack("!I", xip)
    return struct.pack("!HHI12s", 0x0101, len(attr), netcheck.MAGIC, tid) + attr


def test_a_binding_response_is_decoded():
    req, tid = netcheck.binding_request()
    assert len(req) == 20 and req[:2] == b"\x00\x01"
    assert netcheck.parse_binding_response(stun_response(tid, "203.0.113.9", 40000), tid) == ("203.0.113.9", 40000)
    assert netcheck.parse_binding_response(stun_response(b"x" * 12, "203.0.113.9", 40000), tid) is None, "another transaction"
    assert netcheck.parse_binding_response(b"short", tid) is None


class FakeStun(threading.Thread):
    """A UDP server that answers every Binding request with a fixed 'you are ip:port'."""

    def __init__(self, ip="203.0.113.9", port=40000, silent=False):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.2)
        self.port, self.ip, self.mapped_port, self.silent, self.stop = self.sock.getsockname()[1], ip, port, silent, False

    def run(self):
        while not self.stop:
            try:
                data, addr = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                return
            if not self.silent and len(data) == 20:
                self.sock.sendto(stun_response(data[8:20], self.ip, self.mapped_port), addr)

    def close(self):
        self.stop = True
        self.join(1)
        self.sock.close()


def probe_with(*servers):
    for s in servers:
        s.start()
    try:
        return netcheck.probe([("127.0.0.1", s.port) for s in servers], timeout=0.3, tries=2)
    finally:
        for s in servers:
            s.close()


def test_the_nat_reading_follows_what_the_servers_saw():
    same = probe_with(FakeStun(port=40000), FakeStun(port=40000))
    assert all(r["ok"] for r in same) and netcheck.verdict(same, "192.168.1.5")[:2] == ("cone", True)
    sym = probe_with(FakeStun(port=40000), FakeStun(port=40001))
    assert netcheck.verdict(sym, "192.168.1.5")[:2] == ("symmetric", False)
    multi = probe_with(FakeStun(ip="203.0.113.9"), FakeStun(ip="203.0.113.10"))
    assert netcheck.verdict(multi, "192.168.1.5")[:2] == ("multi_wan", False)
    one = probe_with(FakeStun(), FakeStun(silent=True))
    assert [r["ok"] for r in one] == [True, False] and one[1]["error"] == "no answer"
    assert netcheck.verdict(one, "192.168.1.5")[:2] == ("one_stun", True)
    none = probe_with(FakeStun(silent=True))
    assert netcheck.verdict(none)[:2] == ("udp_blocked", False)
    assert netcheck.verdict(same, "203.0.113.9")[0] == "public_host", "a PC whose own address is the public one has no NAT"


def test_the_hub_probe_reads_hello_and_reports_a_dead_address(tmp_path):
    import asyncio
    from aiohttp.test_utils import TestServer
    from eqvoice_signalling.hub import Hub
    from eqvoice_signalling.server import make_app

    async def go():
        server = TestServer(make_app(Hub(ice_servers=[{"urls": ["stun:x.example:3478"]}], join_code="")))
        await server.start_server()
        try:
            ok = await netcheck.hub_check("ws://127.0.0.1:%d/ws" % server.port)
            dead = await netcheck.hub_check("ws://127.0.0.1:1/ws", timeout=2)
            notahub = await netcheck.hub_check("ws://127.0.0.1:%d/health" % server.port, timeout=2)
        finally:
            await server.close()
        return ok, dead, notahub
    ok, dead, notahub = asyncio.run(go())
    assert ok["ok"] and ok["version"] == 2 and ok["ice"] == ["stun:x.example:3478"] and ok["ms"] >= 0
    assert not dead["ok"] and dead["error"]
    assert not notahub["ok"]


def test_vpn_adapters_are_recognised_by_name():
    adapters = ["ProtonVPN | WireGuard Tunnel", "Wi-Fi | Intel(R) Wi-Fi 6 AX200 160MHz", "Ethernet | Intel(R) Ethernet Controller (3) I225-V",
                "Local Area Connection | TAP-Windows Adapter V9", "vEthernet (WSL) | Hyper-V Virtual Ethernet Adapter"]
    assert netcheck.vpn_adapters(adapters) == [adapters[0], adapters[3]]
    assert netcheck.vpn_adapters([]) == []
