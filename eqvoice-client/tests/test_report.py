"""tools/pipeline_report.py: it must say exactly which boundary a run stopped at, and it must understand the real
addon's words (run through LuaJIT here) as well as the desktop client's and the hub's."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from eqvoice_client.bridge.filesource import split_file

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import pipeline_report as pr  # noqa: E402

LUAJIT = shutil.which("luajit")
T = [0]


def line(who, event, **kv):
    """A desktop/hub log line as the real log formats print it."""
    T[0] += 1
    stamp = "2026-10-16 17:%02d:%02d,%03d" % (T[0] // 60, T[0] % 60, 0)
    body = " ".join('%s="%s"' % (k, v) if " " in str(v) else "%s=%s" % (k, v) for k, v in kv.items())
    return "%s INFO  eqvoice.trace: TRACE %s %s %s" % (stamp, who, event, body)


def addon(text):
    T[0] += 1
    return "2026-10-16 17:%02d:%02d,000 INFO  eqvoice.trace: TRACE addon %s" % (T[0] // 60, T[0] % 60, text)


def client(label, lines):
    return pr.Client(label, pr.parse("\n".join(lines)))


def healthy_start(name, realm="Realm", other="Bobby"):
    return [
        line("desktop", "start", version="1"),
        line("desktop", "outbound_first_frame", source="file"),
        line("desktop", "identity", character=name, realm=realm, login="00000001"),
        line("desktop", "hub_registered", character=name),
        line("desktop", "roster_received", total=1, names=other),
    ]


def test_parse_ignores_untraced_lines_and_old_runs():
    text = "\n".join([
        line("desktop", "start"), line("desktop", "identity", character="Old", realm="R"),
        "2026-10-16 17:00:00,000 INFO eqvoice.app: something else",
        line("desktop", "start"), line("desktop", "identity", character="New", realm="R"),
    ])
    events = pr.parse(text)
    assert [e.get("character") for e in events if e.event == "identity"] == ["New"], "only the last run counts"
    assert len(pr.parse(text, since_start=False)) == 4


def test_parse_reads_quoted_values_and_bare_hub_lines():
    ev = pr.parse('TRACE hub link_down a=Alice b=Bobby why="the two distances disagree (5.0 vs 40.0)"\nTRACE hub left character=Alice', since_start=False)
    assert ev[0].event == "link_down" and ev[0].get("why") == "the two distances disagree (5.0 vs 40.0)", "wrangler prints no timestamp"
    assert ev[1].get("character") == "Alice"


@pytest.mark.skipif(LUAJIT is None, reason="luajit is not installed")
def test_the_real_addons_words_satisfy_the_report():
    out = subprocess.run([LUAJIT, str(ROOT / "eqvoice-addon" / "tests" / "trace_sample.lua")], capture_output=True, encoding="utf-8",
                         errors="replace", cwd=str(ROOT / "eqvoice-addon"), timeout=60)
    assert out.returncode == 0, out.stderr
    _, traces = split_file(out.stdout)
    assert len(traces) >= 10, "the addon narrated its run"
    desk = [
        line("desktop", "start"), line("desktop", "outbound_first_frame", source="file"),
        line("desktop", "identity", character="Alice", realm="Testrealm", login="00000001"),
        line("desktop", "inbound_written", path="x", seq=5, character="Alice", total=2, names="Bobby,Carol"),
        line("desktop", "inbound_acked", seq=5, by_the_game_after_s=0.9),
        line("desktop", "distance", name="Bobby", yards=18.4, inside_radius="yes"),
    ] + [addon(t[2]) for t in traces]
    steps = {s.n: s for s in pr.evaluate([client("Alice", desk)], [], test_names=["Bobby"])}
    assert steps[7].status == pr.PASS, steps[7].evidence
    assert steps[8].status == pr.PASS and "Bobby at 18.4 yd" in steps[8].evidence, steps[8].evidence
    assert steps[9].status == pr.PASS, "the addon's `outbound: ... Bobby 18.4 yd` line is understood"
    assert steps[10].status == pr.PASS
    assert steps[11].status == pr.NA, "one client cannot cover the second-player steps"


def two_clients(extra_a=(), extra_b=(), realm_b="Realm"):
    a = client("Alice", healthy_start("Alice", other="Bobby") + list(extra_a))
    b = client("Bobby", healthy_start("Bobby", realm=realm_b, other="Alice") + list(extra_b))
    return [a, b]


def first_unmet(steps):
    return next((s for s in steps if s.status in (pr.FAIL, pr.NOT_YET)), None)


def test_a_run_that_stops_after_the_roster_is_reported_at_step_7():
    hub = pr.parse("\n".join([line("hub", "registered", character="Alice"), line("hub", "registered", character="Bobby")]), since_start=False)
    steps = pr.evaluate(two_clients(), hub)
    assert [s.status for s in steps[:6]] == [pr.PASS] * 6
    s = first_unmet(steps)
    assert s.n == 7 and "inbound" in s.title.lower()
    assert pr.render(steps).count("FIRST BOUNDARY NOT REACHED: step 7") == 1


def test_written_but_never_acknowledged_names_the_inbound_bridge():
    w = line("desktop", "inbound_written", path="x", seq=5, character="Alice", total=1, names="Bobby")
    w2 = line("desktop", "inbound_written", path="x", seq=6, character="Bobby", total=1, names="Alice")
    steps = pr.evaluate(two_clients([w], [w2]), [])
    s = steps[6]
    assert s.status != pr.PASS and "inbound bridge is the boundary that failed" in s.evidence, s.evidence


def test_different_realms_are_a_failure_at_step_4():
    steps = pr.evaluate(two_clients(realm_b="Elsewhere"), [])
    assert steps[3].status == pr.FAIL and "different realms" in steps[3].evidence


def test_one_sided_reports_and_the_reason_the_hub_gave():
    hub_lines = [line("hub", "registered", character="Alice"), line("hub", "registered", character="Bobby"),
                 line("hub", "proximity", frm="Alice", peers="Bobby:10.0"),
                 line("hub", "link_down", a="Alice", b="Bobby", why="only one side reports the other")]
    hub = pr.parse("\n".join(hub_lines), since_start=False)
    steps = pr.evaluate(two_clients(), hub)
    assert steps[10].status != pr.PASS and "1" in steps[10].evidence
    assert steps[11].status != pr.PASS and "only one side reports the other" in steps[11].evidence


def test_webrtc_failure_is_a_fail_with_the_reason():
    bad = [line("desktop", "webrtc_signal", name="Bobby", sent="offer"), line("desktop", "webrtc", name="Bobby", ice="failed", connection="failed")]
    steps = pr.evaluate(two_clients(bad, []), [])
    assert steps[12].status != pr.PASS and "failed" in steps[12].evidence


def test_a_skipped_step_is_a_fail_not_just_not_yet():
    steps = pr.evaluate(two_clients(), [])
    steps[0].status = pr.NOT_YET
    fresh = pr.evaluate(two_clients([line("desktop", "audio_rx", name="Bobby", state="RECEIVING")], [line("desktop", "audio_rx", name="Alice", state="RECEIVING")]), [])
    assert fresh[13].status == pr.PASS and fresh[6].status == pr.FAIL, "audio flowed although the inbound bridge never reported success"


def test_render_and_exit_code(tmp_path, capsys):
    log = tmp_path / "logs"
    log.mkdir()
    (log / "eqvoice.log").write_text("\n".join(healthy_start("Alice")), encoding="utf-8")
    rc = pr.main(["--client", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 1 and "FIRST BOUNDARY NOT REACHED" in out and " 1  PASS" in out


def test_a_fake_microphone_nobody_asked_for_is_not_voice():
    """The 2026-10-16 real run: audio 'flowed' both ways, but one side was a leftover test browser playing Chromium's beep."""
    rx = lambda n: line("desktop", "audio_rx", name=n, state="RECEIVING")          # noqa: E731
    fake = line("desktop", "mic", state="ok", label="Fake Default Audio Input", fake="yes")
    steps = pr.evaluate(two_clients([rx("Bobby"), fake], [rx("Alice")]), [])
    s = steps[13]
    assert s.status == pr.FAIL and "NOT REAL VOICE" in s.evidence and "Alice" in s.evidence and "FAKE" in s.evidence, s.evidence

    def asked(name, other, extra):                                                  # a rehearsal: --headless --fake-media
        lines = healthy_start(name, other=other)
        lines[0] = line("desktop", "start", fake_media="yes", headless="yes")
        return client(name, lines + extra)
    ok = pr.evaluate([asked("Alice", "Bobby", [rx("Bobby"), fake]), asked("Bobby", "Alice", [rx("Alice")])], [])
    assert ok[13].status == pr.PASS, "a fake microphone that was asked for is the rehearsal, not a fault"


def test_a_hidden_engine_is_named_even_before_any_audio():
    hidden = line("desktop", "media_engine", browser="Edg/141", headless="yes", automation="no", launched_by_this_client="no")
    steps = pr.evaluate(two_clients([hidden], []), [])
    assert steps[13].status == pr.FAIL and "hidden test browser" in steps[13].evidence


def test_with_three_clients_the_pair_that_talked_is_reported():
    up = lambda other: line("desktop", "webrtc", name=other, ice="connected", connection="connected")    # noqa: E731
    a = client("Alice", healthy_start("Alice", other="Bobby") + [up("Bobby")])
    b = client("Bobby", healthy_start("Bobby", other="Alice") + [up("Alice")])
    old = client("Wipemon", healthy_start("Wipemon", other="Zed"))
    hub = pr.parse(line("hub", "link_up", a="Alice", b="Bobby", distance=10), since_start=False)
    for order in ([old, a, b], [a, old, b], [a, b, old]):
        steps = pr.evaluate(order, hub)
        assert "Wipemon" not in steps[3].evidence and "Alice" in steps[3].evidence and "Bobby" in steps[3].evidence, steps[3].evidence
        assert steps[11].status == pr.PASS and steps[12].status == pr.PASS


def test_step_14_names_the_client_whose_microphone_never_opened():
    rx = line("desktop", "audio_rx", name="Alice", state="RECEIVING")
    open_mic = line("desktop", "audio_mic", transmit="yes", mode="open", muted="no", deafened="no", ptt_key_down="no")
    steps = pr.evaluate(two_clients([line("desktop", "audio_tx", name="Bobby", state="SENDING"), open_mic], [rx]), [])
    s = steps[13]
    assert s.status != pr.PASS
    assert "microphone opened: Alice=1 Bobby=0" in s.evidence, s.evidence
    assert "Bobby never opened its microphone" in s.evidence and "Alice never opened" not in s.evidence


def test_a_client_started_after_the_games_still_proves_steps_7_and_8():
    """Restarting only the desktop clients (the games keep running) loses the addon's early sentences from the client's log.
    The game's acknowledgement and the distances it reports are the proof; the report must not call that a failure."""
    def side(name, other, seq):
        return [line("desktop", "inbound_written", path="x", seq=seq, character=name, total=1, names=other),
                line("desktop", "inbound_acked", seq=seq, by_the_game_after_s=0.4),
                line("desktop", "distance", name=other, yards=12.0, inside_radius="yes")]
    steps = pr.evaluate(two_clients(side("Alice", "Bobby", 5), side("Bobby", "Alice", 6)), [])
    assert steps[6].status == pr.PASS and "scrolled out" in steps[6].evidence and "Client(" not in steps[6].evidence, steps[6].evidence
    assert steps[7].status == pr.PASS and "inferred" in steps[7].evidence, steps[7].evidence
    # ... but a name that was written and never acknowledged is still the inbound bridge failing
    w = line("desktop", "inbound_written", path="x", seq=5, character="Alice", total=1, names="Bobby")
    steps = pr.evaluate(two_clients([w], [w.replace("Alice", "Bobby").replace("names=Bobby", "names=Alice")]), [])
    assert steps[6].status != pr.PASS


def test_a_friends_send_logs_zip_joins_the_checklist(tmp_path):
    """Two-PC test: my side is a local client and the hub log; the friend's side arrives as the zip SEND-LOGS.bat made."""
    import zipfile
    import realtest
    up = lambda other: line("desktop", "webrtc", name=other, ice="connected", connection="connected")    # noqa: E731
    mine = client("Alice", healthy_start("Alice", other="Bobby") + [up("Bobby"), line("desktop", "webrtc_path", name="Bobby", path="direct-nat")])
    theirs = healthy_start("Bobby", other="Alice") + [up("Alice")]
    z = tmp_path / "EqVoice-logs-1.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("logs/eqvoice.log.1", "\n".join(theirs[:2]))               # rotated part: older lines
        zf.writestr("logs/eqvoice.log", "\n".join(theirs[2:]))
        zf.writestr("system.txt", "time: now")
    friend = realtest.friend_client(z)
    friend_dir = tmp_path / "unzipped" / "logs"
    friend_dir.mkdir(parents=True)
    (friend_dir / "eqvoice.log").write_text("\n".join(theirs), encoding="utf-8")
    hub = pr.parse("\n".join([line("hub", "registered", character="Alice"), line("hub", "registered", character="Bobby"),
                              line("hub", "link_up", a="Alice", b="Bobby", distance=12)]), since_start=False)
    for fr in (friend, realtest.friend_client(tmp_path / "unzipped")):
        steps = pr.evaluate([mine, fr], hub)
        assert steps[3].status == pr.PASS and "Bobby" in steps[3].evidence, steps[3].evidence
        assert steps[5].status == pr.PASS and steps[11].status == pr.PASS and steps[12].status == pr.PASS


def test_the_webrtc_block_names_how_every_call_was_carried():
    ok = [line("desktop", "webrtc_result", name="Bobby", result="DIRECT_STUN", phase="settled", pair="srflx>srflx", proto="udp",
               local_network="public", remote_network="public", turn_used="no", rtt_ms=38, jitter_ms=2.5, loss_in_pct=0.4, loss_out_pct=0.0, setup_ms=1234)]
    relay = [line("desktop", "webrtc_result", name="Alice", result="RELAY_TURN", phase="connected", pair="host>relay", proto="udp",
                  local_network="private", remote_network="public", turn_used="yes", rtt_ms=90, jitter_ms=6.0, loss_in_pct=1.2, loss_out_pct=0.8, setup_ms=4100)]
    failed = [line("desktop", "webrtc_result", name="Carol", result="FAILED", reason="nat_traversal_failed",
                   explanation="both sides found their public addresses but no direct path formed", local_candidates="host:2,srflx:1",
                   remote_candidates="host:1,srflx:1", setup_ms=20000)]
    a = client("Alice", healthy_start("Alice") + ok + failed)
    b = client("Bobby", healthy_start("Bobby", other="Alice") + relay)
    c = client("Carol", healthy_start("Carol"))
    text = pr.webrtc_block([a, b, c])
    assert "Alice          -> Bobby" in text and "DIRECT_STUN" in text and "srflx>srflx udp (public > public)" in text
    assert "rtt 38 ms  jitter 2.5 ms  loss in 0.4% out 0.0%  set-up 1234 ms" in text
    assert "Bobby          -> Alice" in text and "RELAY_TURN" in text and "host>relay" in text and "[first reading, not settled]" in text
    assert "Alice          -> Carol" in text and "FAILED  both sides found their public addresses" in text and "local host:2,srflx:1, remote host:1,srflx:1" in text
    assert "Carol          (no WebRTC attempt in this log)" in text
    assert "DIRECT_HOST = direct peer to peer" in text and "RELAY_TURN = carried by a TURN relay" in text


def test_step_13_names_the_path_each_side_got():
    up = lambda other: line("desktop", "webrtc", name=other, ice="connected", connection="connected")                    # noqa: E731
    res = lambda other, label, pair: line("desktop", "webrtc_result", name=other, result=label, pair=pair, phase="connected")    # noqa: E731
    a = client("Alice", healthy_start("Alice", other="Bobby") + [up("Bobby"), res("Bobby", "DIRECT_STUN", "srflx>srflx")])
    b = client("Bobby", healthy_start("Bobby", other="Alice") + [up("Alice"), res("Alice", "DIRECT_STUN", "prflx>srflx")])
    s = pr.evaluate([a, b], [])
    assert s[12].status == pr.PASS and "Alice: DIRECT_STUN srflx>srflx" in s[12].evidence and "Bobby: DIRECT_STUN prflx>srflx" in s[12].evidence, s[12].evidence
