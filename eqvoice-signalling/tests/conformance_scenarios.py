"""The conformance scenarios. Run tools/gen_conformance.py after changing any of them or the Python hub."""
from __future__ import annotations

import json

from conformance_lib import Recorder, sid_for

R = "Testrealm"


def scenarios() -> list:
    out = []

    # ------------------------------------------------------------------ registration
    s = Recorder("hello and registration", ice=[{"urls": ["stun:x.example"]}])
    s.connect("a"); s.register("a", "Alice")
    s.connect("b"); s.register("b", "Bobby")
    s.connect("c"); s.register("c", "Carol")
    s.send("a", {"t": "ping", "ts": 123.5})
    out.append(s)

    s = Recorder("unicode names")
    for i, name in enumerate(["Zoë", "Émile", "Жанна", "Al1ce", "Alice_", "A", "Thirteenchars", "Bob Smith", "x;DROP", "ab"]):
        s.connect("u%d" % i, ip="10.0.1.%d" % i)
        s.register("u%d" % i, name)
    out.append(s)

    s = Recorder("malformed registrations")
    cases = [("sid", "short"), ("sid", "A" * 21 + "!"), ("sid", 12345), ("sid", None), ("realm", ""), ("realm", "x" * 40),
             ("realm", "bad/realm"), ("realm", 5), ("character", ""), ("character", 7), ("v", 1), ("v", None), ("pub", "!!"),
             ("pub", "AAAA"), ("sig", "AAAA"), ("sig", 5)]
    for i, (field, value) in enumerate(cases):
        c = "m%d" % i
        s.connect(c, ip="10.0.2.%d" % i)
        s.register(c, "Alice", **{field: value})
    out.append(s)

    s = Recorder("signature must match the nonce, names and realm")
    s.connect("a"); s.connect("b")
    s.register("a", "Alice", sign_character="Bobby")
    s.register("a", "Alice", sign_realm="Otherrealm")
    s.register("a", "Alice", sign_nonce=b"\x00" * 16)
    s.register("a", "Alice", sign_sid=sid_for("zzz"))
    s.register("a", "Alice", key="someone-else-signs-but-pub-is-theirs", pub="AAAA")
    s.register("a", "Alice")
    out.append(s)

    s = Recorder("join code", join_code="s3cret")
    s.connect("a"); s.register("a", "Alice")
    s.connect("b"); s.register("b", "Bobby", code="nope")
    s.connect("c"); s.register("c", "Carol", code=5)
    s.connect("d"); s.register("d", "Dina", code="s3cret")
    out.append(s)

    s = Recorder("order of messages: not registered, double register, unknown")
    s.connect("a")
    s.send("a", {"t": "proximity", "peers": []})
    s.send("a", {"t": "offer", "to": "x", "ep": 1, "data": {}})
    s.send("a", {"t": "unregister"})
    s.register("a", "Alice", id=7)
    s.register("a", "Alice")
    s.send("a", {"t": "zzz", "id": "q1"})
    s.send("a", {"t": "ping"})
    s.send("a", {"t": "ping", "ts": True})
    s.send("a", {"t": "ping", "ts": "x"})
    out.append(s)

    # ---------------------------------------------------------------- realms and names
    s = Recorder("realm isolation")
    s.connect("a"); s.register("a", "Alice", realm="Turtle WoW")
    s.connect("b"); s.register("b", "Bobby", realm="turtle  wow")      # same realm, case and spacing folded
    s.connect("c"); s.register("c", "Alicia", realm="Elsewhere")
    s.connect("d"); s.register("d", "Alice", realm="Elsewhere")        # same name, other realm: fine
    s.send("a", {"t": "proximity", "peers": [["Alice", 5, 1], ["Bobby", 6, 1]]})
    s.send("b", {"t": "proximity", "peers": [["Alice", 7, 1]]})
    s.send("c", {"t": "proximity", "peers": [["Alice", 5, 1]]})
    s.send("d", {"t": "proximity", "peers": [["Alicia", 5, 1]]})
    out.append(s)

    s = Recorder("duplicate login, takeover, sid collision")
    s.connect("a"); s.register("a", "Alice")
    s.connect("b"); s.register("b", "Bobby")
    s.connect("x"); s.register("x", "Alice", key="impostor")                       # live name, different key
    s.connect("y"); s.register("y", "Alice", sid=sid_for("a:Alice"), key="impostor2")   # live name wins
    s.connect("v"); s.register("v", "Mallory", sid=sid_for("a:Alice"))                  # sid in use by another name
    s.connect("z"); s.register("z", "Alice", sid=sid_for("z:new"))                  # same key: takes over
    s.send("b", {"t": "proximity", "peers": [["Alice", 5, 1]]})
    out.append(s)

    s = Recorder("character change and reservation")
    s.connect("a")
    ev = s.register("a", "Alice")
    tok = s.token_of(ev)
    s.connect("b"); s.register("b", "Bobby")
    s.disconnect("a")                                                              # dropped: name reserved
    s.connect("t"); s.register("t", "Alice", key="thief")                          # refused
    s.connect("w"); s.register("w", "Alice", token="wrong-token-xxxxxxxxxxxx")     # right key, wrong token
    s.connect("r"); s.register("r", "Alice", key="Alice", token=tok)               # right key, right token
    s.disconnect("r")
    s.advance(31)
    s.connect("e"); s.register("e", "Alice", key="late-thief")                     # reservation expired
    s.send("e", {"t": "unregister"})                                               # clean leave frees the name at once
    s.connect("f"); s.register("f", "Alice", key="newcomer")
    out.append(s)

    s = Recorder("roster chunks", limits={"roster_chunk": 2})
    for i, n in enumerate(["Aaa", "Bbb", "Ccc", "Ddd", "Eee"]):
        s.connect("c%d" % i, ip="10.0.3.%d" % i); s.register("c%d" % i, n)
    s.disconnect("c1")
    out.append(s)

    s = Recorder("realm cap", limits={"realm_cap": 2})
    for i, n in enumerate(["Aaa", "Bbb", "Ccc"]):
        s.connect("c%d" % i, ip="10.0.4.%d" % i); s.register("c%d" % i, n)
    out.append(s)

    # ------------------------------------------------------------ mutual proximity
    def pair(s):
        s.connect("a"); s.register("a", "Alice")
        s.connect("b"); s.register("b", "Bobby")

    s = Recorder("mutual proximity links and unlinks")
    pair(s)
    s.send("a", {"t": "proximity", "peers": [["Bobby", 18.0, 1]]})
    s.send("b", {"t": "proximity", "peers": [["Alice", 19.0, 1]]})              # link: 18.5
    s.advance(2)
    s.send("a", {"t": "proximity", "peers": [["Bobby", 18.0, 1]]})
    s.advance(5)                                                                 # b's report ages out; grace passes
    s.send("a", {"t": "proximity", "peers": [["Bobby", 18.0, 1]]})
    s.advance(4)
    out.append(s)

    s = Recorder("one-sided and disagreeing reports")
    pair(s)
    s.send("a", {"t": "proximity", "peers": [["Bobby", 5, 1]]})                  # one-sided: nothing
    s.send("b", {"t": "proximity", "peers": [["Alice", 40, 1]]})                 # |5-40| > tolerance: nothing
    s.send("b", {"t": "proximity", "peers": [["Alice", 16.9, 1]]})               # within 12 of 5: link (rounded average)
    s.send("a", {"t": "proximity", "peers": [["Bobby", 60, 1]]})                 # now 60 vs 16.9: disagreement
    s.advance(3.5)
    s.send("b", {"t": "proximity", "peers": [["Alice", 60, 1]]})                 # both far: beyond link radius
    s.advance(1)
    out.append(s)

    s = Recorder("rounding of the shared distance")
    pair(s)
    s.send("a", {"t": "proximity", "peers": [["Bobby", 18.1, 1]]})
    s.send("b", {"t": "proximity", "peers": [["Alice", 18.4, 1]]})              # average 18.25 -> 18.3
    out.append(s)

    s = Recorder("register after report completes the pair")
    s.connect("a"); s.register("a", "Alice")
    s.send("a", {"t": "proximity", "peers": [["Bobby", 10, 1]]})
    s.connect("b"); s.register("b", "Bobby")
    s.send("b", {"t": "proximity", "peers": [["Alice", 11, 1]]})
    s.disconnect("b")
    out.append(s)

    s = Recorder("replacement set and three way")
    s.connect("a"); s.register("a", "Alice")
    s.connect("b"); s.register("b", "Bobby")
    s.connect("c"); s.register("c", "Carol")
    s.send("a", {"t": "proximity", "peers": [["Bobby", 10, 1], ["Carol", 12, 1]]})
    s.send("b", {"t": "proximity", "peers": [["Alice", 10, 1], ["Carol", 5, 1]]})
    s.send("c", {"t": "proximity", "peers": [["Alice", 12, 1], ["Bobby", 5, 1], ["Carol", 0, 1]]})
    s.send("a", {"t": "proximity", "peers": [["Carol", 12, 1]]})                   # Alice stops reporting Bobby
    s.advance(4)
    s.send("a", {"t": "proximity", "peers": [["Carol", 12, 1]]})
    s.advance(1)
    out.append(s)

    s = Recorder("disconnect drops links and notifies")
    pair(s)
    s.send("a", {"t": "proximity", "peers": [["Bobby", 10, 1]]})
    s.send("b", {"t": "proximity", "peers": [["Alice", 10, 1]]})
    s.disconnect("b")
    out.append(s)

    s = Recorder("malformed proximity")
    pair(s)
    for i, peers in enumerate(["nope", [["Bobby"]], [["Bobby", "far"]], [["Bobby", -1]], [["Bobby", 600]], [["Bobby", True]],
                               [[5, 5]], [["B", 5]], [["Bob by", 5]], ["Bobby"], [None], [["Bobby", 5]] * 65, None, {}]):
        s.send("a", {"t": "proximity", "peers": peers})
        s.advance(0.3)
    s.raw("a", '{"t":"proximity","peers":[["Bobby",NaN,1]]}')
    s.raw("a", '{"t":"proximity"}')
    out.append(s)

    # ---------------------------------------------------------------------- relay
    def linked(s):
        pair(s)
        s.send("a", {"t": "proximity", "peers": [["Bobby", 10, 1]]})
        s.send("b", {"t": "proximity", "peers": [["Alice", 10, 1]]})

    s = Recorder("signalling relay rules")
    linked(s)
    sa, sb = sid_for("a:Alice"), sid_for("b:Bobby")
    s.send("a", {"t": "offer", "to": sb, "ep": 5, "data": {"sdp": "x"}})
    s.send("b", {"t": "answer", "to": sa, "ep": 5, "data": {"sdp": "y"}})
    s.send("a", {"t": "ice", "to": sb, "ep": 5, "data": {"candidate": {"candidate": "c1"}}})
    s.send("a", {"t": "bye", "to": sb, "ep": 5})
    s.send("a", {"t": "offer", "to": sid_for("nobody"), "ep": 1, "data": {}})
    s.send("a", {"t": "offer", "to": 5, "ep": 1, "data": {}})
    for ep in (-1, 2 ** 31, "x", True, None):
        s.send("a", {"t": "offer", "to": sb, "ep": ep, "data": {}})
    s.send("a", {"t": "offer", "to": sb, "ep": 1, "data": {"sdp": "x" * 9000}})
    s.send("a", {"t": "offer", "to": sb})
    out.append(s)

    s = Recorder("per link signal budget", limits={"link_signals_per_minute": 5, "bucket_capacity": 1000, "bucket_rate": 1000,
                                                    "idle_timeout": 1000})
    linked(s)
    sb = sid_for("b:Bobby")
    for i in range(7):
        s.send("a", {"t": "ice", "to": sb, "ep": 1, "data": {"i": i}})
    s.advance(61)
    s.send("a", {"t": "proximity", "peers": [["Bobby", 10, 1]]})
    s.send("b", {"t": "proximity", "peers": [["Alice", 10, 1]]})
    s.send("a", {"t": "ice", "to": sb, "ep": 1, "data": {"i": 99}})
    out.append(s)

    # ------------------------------------------------------------- abuse limits
    s = Recorder("rate limits", limits={"bucket_capacity": 4, "bucket_rate": 0.5, "prox_capacity": 3, "prox_rate": 0.5})
    pair(s)
    for i in range(3):
        s.send("a", {"t": "proximity", "peers": [["Bobby", 10 + i, 1]]})
    s.send("a", {"t": "proximity", "peers": [["Bobby", 20, 1]]})
    s.advance(2)
    for i in range(8):
        s.send("b", {"t": "ping"})
    out.append(s)

    s = Recorder("garbage never raises")
    pair(s)
    for raw in ["", "not json", "[]", "null", "5", '{"t":5}', '{"t":"zzz"}', "{" * 10, '{"t":"proximity"}', '"str"', "true",
                '{"t":"register"}', '{"t":"offer"}', '{"t":"ping","ts":null}', '{"t":"ping","ts":1e300}']:
        s.raw("a", raw)
        s.advance(0.1, 0.1)
    s.rawbytes("a", b"\x00\x01\x02")
    s.raw("b", "x" * 17000)
    out.append(s)

    s = Recorder("unregister closes politely")
    pair(s)
    s.send("a", {"t": "unregister"})
    s.send("b", {"t": "ping"})
    out.append(s)

    s = Recorder("timeouts")
    s.connect("a")                                   # never registers
    s.connect("b"); s.register("b", "Bobby")         # registers, then goes silent
    s.connect("c"); s.register("c", "Carol")
    s.advance(11)
    s.send("c", {"t": "ping"})
    s.advance(20)
    s.send("c", {"t": "ping"})
    s.advance(25)
    out.append(s)

    s = Recorder("address limits", limits={"max_conns_per_ip": 2, "registers_per_ip_per_minute": 3})
    s.connect("a", ip="9.9.9.9"); s.connect("b", ip="9.9.9.9"); s.connect("c", ip="9.9.9.9"); s.connect("d", ip="8.8.8.8")
    s.register("a", "Aaa"); s.register("b", "Bbb")
    s.disconnect("a")
    s.connect("e", ip="9.9.9.9")
    s.register("e", "Ccc")
    s.register("b", "Ddd")                           # b is registered already: already_registered, counts nothing
    s.disconnect("b")
    s.connect("f", ip="9.9.9.9")
    s.register("f", "Eee")                           # fourth registration this minute from one address
    s.advance(61)
    s.register("f", "Eee")
    s.register("d", "Fff")
    out.append(s)

    s = Recorder("global connection cap", limits={"max_connections": 3})
    s.connect("a", ip="1.1.1.1"); s.connect("b", ip="1.1.1.2"); s.connect("c", ip="1.1.1.3"); s.connect("d", ip="1.1.1.4")
    s.register("a", "Aaa"); s.register("b", "Bbb"); s.register("c", "Ccc")
    s.disconnect("a")
    s.connect("e", ip="1.1.1.5")                     # room again
    s.register("e", "Eee")
    s.connect("f", ip="1.1.1.6")                     # full again
    out.append(s)

    return out


def documents() -> list:
    return [r.doc() for r in scenarios()]


if __name__ == "__main__":
    print(json.dumps({"scenarios": len(documents())}))
