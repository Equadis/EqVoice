"""The developer tools: SavedVariables reader, run against a file the Lua suite's stub client produced."""
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import savedvars  # noqa: E402


def test_parse_wow_style_tables():
    text = '''EqVoiceDB = {
	["enabled"] = true,
	["radius"] = 300,
	["ratio"] = -1.5e2,
	["name"] = "a \\"quoted\\" word\\\\ and\\ttab",
	["empty"] = {
	},
	["list"] = {
		"one", -- [1]
		"two", -- [2]
	},
	["nested"] = {
		["deep"] = {
			["ok"] = false,
		},
	},
	bare = nil,
}
Other = 5
-- a comment
'''
    d = savedvars.parse(text)
    db = d["EqVoiceDB"]
    assert db["enabled"] is True and db["radius"] == 300 and db["ratio"] == -150.0
    assert db["name"] == 'a "quoted" word\\ and\ttab'
    assert db["empty"] == [] or db["empty"] == {}
    assert db["list"] == ["one", "two"]
    assert db["nested"]["deep"]["ok"] is False and db["bare"] is None
    assert d["Other"] == 5


@pytest.mark.parametrize("bad", ["X = {", "X = ", "= 5", 'X = "abc', "X = { [1 = 2 }"])
def test_parse_errors_are_exceptions_not_hangs(bad):
    with pytest.raises(savedvars.LuaSyntaxError):
        savedvars.parse(bad)


@pytest.mark.skipif(shutil.which("luajit") is None, reason="needs luajit")
def test_reads_what_the_addon_wrote(tmp_path):
    out = tmp_path / "EqVoice.lua"
    subprocess.run(["luajit", os.path.join(ROOT, "eqvoice-addon", "tests", "dump_savedvars.lua"), str(out)],
                   check=True, capture_output=True)
    db = savedvars.parse(out.read_text(encoding="utf-8"))["EqVoiceDB"]
    assert db["diag"]["say"]["ok"] is True and "LOOPBACK" in db["diag"]["say"]["result"]
    assert db["diag"]["yell"]["ok"] is False and "NO ECHO" in db["diag"]["yell"]["result"]
    assert "SendAddonMessage" in db["diag"]["api"]["result"]
    assert any('a line with "quotes"' in line for line in db["log"])
    assert db["lastStats"]["sent"] >= 0
    # and the CLI prints it
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "read_savedvars.py"), str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "LOOPBACK" in r.stdout and "== log ==" in r.stdout


def test_cli_finds_newest_file_under_a_game_folder(tmp_path):
    sv = tmp_path / "WTF" / "Account" / "ME" / "SavedVariables"
    sv.mkdir(parents=True)
    (sv / "EqVoice.lua").write_text('EqVoiceDB = { ["diag"] = { ["say"] = { ["ok"] = true, ["result"] = "x", }, }, }', encoding="utf-8")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "read_savedvars.py"), str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "say" in r.stdout
    empty = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "read_savedvars.py"), str(tmp_path / "nothing")],
                           capture_output=True, text=True)
    assert empty.returncode == 1


def test_a_second_real_test_run_is_refused_while_the_first_holds_the_client_ports(tmp_path):
    """2026-10-17: two `realtest run`s overlapped; the second's clients crashed on the busy ports and respawned every 2 s while it
    tore down the first one's voice windows. The launcher now sees the busy ports and says so instead."""
    import socket
    import realtest
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 17421))
    except OSError:
        pytest.skip("port 17421 is in use already (a real run is going)")
    game = tmp_path / "Game"                                     # a pretend game folder: run() installs the addon into whatever it is given
    game.mkdir()
    (game / "WoW.exe").write_bytes(b"MZ")
    try:
        assert 17421 in realtest.busy_ports()
        assert realtest.run(type("A", (), {"wow_dir": [str(game)], "force": True, "test_roster": None, "python_hub": False,
                                           "fake_media": False, "headless": False})()) == 2
    finally:
        s.close()
    assert 17421 not in realtest.busy_ports()


# ------------------------------------------------------------ the two-PC test helpers

def test_a_quick_tunnel_address_is_taken_from_cloudflareds_output(tmp_path, monkeypatch):
    import tunnel
    fake = tmp_path / "fake-cloudflared.bat"
    fake.write_bytes(b"@echo off\r\necho INF Requesting new quick Tunnel on trycloudflare.com...\r\n"
                     b"echo INF ^|  https://fuzzy-cat-1234.trycloudflare.com  ^|\r\nping -n 20 127.0.0.1 >nul\r\n")
    p, wss = tunnel.start_quick_tunnel(fake, 8787, tmp_path / "t.log", timeout=20)
    try:
        assert wss == "wss://fuzzy-cat-1234.trycloudflare.com/ws"
        assert "trycloudflare" in (tmp_path / "t.log").read_text()
    finally:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
    silent = tmp_path / "silent.bat"
    silent.write_bytes(b"@echo off\r\necho nothing useful\r\n")
    with pytest.raises(RuntimeError, match="did not print a tunnel address"):
        tunnel.start_quick_tunnel(silent, 8787, tmp_path / "t2.log", timeout=3)
    monkeypatch.setenv("CLOUDFLARED", str(fake))
    assert tunnel.find_cloudflared() == fake
    assert tunnel.to_wss("https://a-b.trycloudflare.com/") == "wss://a-b.trycloudflare.com/ws"


def test_only_the_addons_own_files_name_a_character(tmp_path):
    """eqvoice-in.txt (roster), eqvoice-st.txt (status) and eqvoice-ctl.txt (commands) are the app's files, but their names look just
    like a character called In, St or Ctl: the first real two-character listening test started clients for "in" and "st"."""
    import realtest
    data = tmp_path / "CustomData"
    data.mkdir()
    bridge = "OVB2 0200BE00262F4930\nOVT 14 1792965316 inbound: something\n"
    for name, body in (("eqvoice-Equavoice.txt", bridge), ("eqvoice-Testmantwo.txt", "OVT 1 2 x\n" + bridge),
                       ("eqvoice-in.txt", "OVI1 5 1700000000 ABCD Me\nAlice\n"), ("eqvoice-st.txt", "OVS1 5 1700000000 ABCD Me\nS level=ok\n"),
                       ("eqvoice-ctl.txt", "OVC1 5 1700000000 ABCD Me\n1 mute 1\n"), ("eqvoice-in-Equavoice.txt", "OVI1 5 1 ABCD E\n"),
                       ("eqvoice-st-Equavoice.txt", "OVS1 5 1 ABCD E\n"), ("eqvoice-Old.txt", bridge), ("eqvoice.txt", bridge)):
        (data / name).write_text(body, encoding="utf-8")
    os.utime(data / "eqvoice-Old.txt", (1, 1))
    assert sorted(realtest.character_files(tmp_path)) == ["Equavoice", "Testmantwo"]
    assert realtest.character_files(tmp_path / "nowhere") == {}


def test_the_live_feed_prints_only_new_connection_lines(tmp_path):
    import realtest
    home = tmp_path / "home-Alice"
    (home / "logs").mkdir(parents=True)
    log = home / "logs" / "eqvoice.log"
    log.write_text("2026-10-17 02:00:00,100 INFO  eqvoice.trace: TRACE desktop webrtc_path name=Old path=direct-nat\n", encoding="utf-8")
    feed = realtest.Feed()
    assert feed.new_lines({"Alice": home}) == [], "what happened before the feed started is not replayed"
    with open(log, "a", encoding="utf-8") as f:
        f.write("2026-10-17 02:01:02,500 INFO  eqvoice.trace: TRACE desktop webrtc_path name=Bobby path=direct-nat turn_used=no\n")
        f.write("2026-10-17 02:01:02,600 INFO  eqvoice.trace: TRACE desktop outbound_read frame=3\n")
        f.write("2026-10-17 02:01:03,000 INFO  eqvoice.trace: TRACE desktop peer name=Bobby NEARBY_PENDING -> CONNECTING why=x\n")
        f.write("2026-10-17 02:01:03,100 INFO  eqvoice.trace: TRACE desktop peer name=Bobby UNKNOWN -> SEEN why=x\n")
    got = feed.new_lines({"Alice": home})
    assert len(got) == 2 and "[Alice] 02:01:02 webrtc_path name=Bobby path=direct-nat" in got[0] and "CONNECTING" in got[1]
    assert feed.new_lines({"Alice": home}) == []


def test_every_client_the_launcher_starts_has_a_command_line_the_client_accepts(tmp_path, monkeypatch):
    """A random token that began with '-' (urlsafe base64 has them) was read as an option and the client exited with status 2: one launch in
    sixty-four. Found by a rehearsal; this launches three hundred."""
    import subprocess
    import types
    import realtest
    from eqvoice_client.__main__ import parse
    seen = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: seen.append(cmd) or types.SimpleNamespace(poll=lambda: None))
    args = types.SimpleNamespace(test_roster=None, join_code="deadbeef", fake_media=False, headless=False)
    for i in range(300):
        realtest.start_client("Alice", i % 4, tmp_path, tmp_path, args)
    assert len(seen) == 300
    tokens = set()
    for cmd in seen:
        ns = parse(cmd[3:])                              # python -m eqvoice_client <the rest>
        assert ns.token and not ns.token.startswith("-") and ns.join_code == "deadbeef"
        tokens.add(ns.token)
    assert len(tokens) == 300, "a fresh token every launch"


def test_logs_from_a_friend_still_on_the_old_name_are_read(tmp_path):
    """A friend who has not updated sends logs/octovoice.log; one who has sends logs/eqvoice.log. Both give the same events."""
    import zipfile
    import realtest
    line = "2026-10-17 02:01:02,500 INFO  %s.trace: TRACE desktop webrtc_path name=Bobby path=direct-nat\n"
    old, new = tmp_path / "old.zip", tmp_path / "new.zip"
    with zipfile.ZipFile(old, "w") as z:
        z.writestr("logs/octovoice.log", line % "octovoice")
    with zipfile.ZipFile(new, "w") as z:
        z.writestr("logs/eqvoice.log", line % "eqvoice")
    assert len(realtest.friend_client(old).events) == 1 and len(realtest.friend_client(new).events) == 1
    folder = tmp_path / "folder"
    (folder / "logs").mkdir(parents=True)
    (folder / "logs" / "octovoice.log").write_text(line % "octovoice", encoding="utf-8")
    assert len(realtest.friend_client(folder).events) == 1


def test_realtest_installs_the_addon_only_into_a_real_game_folder(tmp_path, monkeypatch):
    """It once installed the addon into the repository, because a test handed it the repository as the game folder."""
    import realtest
    from eqvoice_client import installer
    monkeypatch.setattr(installer, "running_wow_dirs", lambda: [])
    notgame = tmp_path / "NotAGame"
    notgame.mkdir()
    ok, msg = realtest.repair_addon(notgame, "not installed")
    assert ok is False and "no WoW.exe" in msg and not list(notgame.iterdir()), "nothing was written there"
    game = tmp_path / "Game"
    game.mkdir()
    (game / "WoW.exe").write_bytes(b"MZ")
    ok, msg = realtest.repair_addon(game, "not installed")
    assert ok is True and "installed it for you" in msg and (game / "Interface" / "AddOns" / "EqVoice" / "EqVoice.toc").exists()
    monkeypatch.setattr(installer, "running_wow_dirs", lambda: [game])
    (game / "Interface" / "AddOns" / "EqVoice" / "Core.lua").write_text("-- stale")
    ok, msg = realtest.repair_addon(game, "STALE")
    assert ok is False and "game is running" in msg and (game / "Interface" / "AddOns" / "EqVoice" / "Core.lua").read_text() == "-- stale"
