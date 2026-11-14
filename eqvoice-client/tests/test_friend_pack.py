"""The zip a friend gets for the two-PC voice test: complete, self-contained, and it hands back logs without secrets."""
import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import make_test_pack  # noqa: E402


@pytest.fixture()
def pack(tmp_path):
    z = make_test_pack.build_python_pack("wss://example.trycloudflare.com/ws", "s3cretcode", tmp_path / "dist")
    out = tmp_path / "unzipped"
    with zipfile.ZipFile(z) as zf:
        zf.extractall(out)
    return out / "EqVoice-test-pack"


def friend(pack_dir):
    spec = importlib.util.spec_from_file_location("eqvoice_friend_under_test", pack_dir / "eqvoice_friend.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_pack_has_everything_and_nothing_else(pack):
    names = {p.relative_to(pack).as_posix() for p in pack.rglob("*") if p.is_file()}
    for want in ("START-EqVoice.bat", "NETCHECK.bat", "SEND-LOGS.bat", "_setup.bat", "README.txt", "hub.txt", "VERSION",
                 "eqvoice_friend.py", "client/requirements.txt", "client/eqvoice_client/__main__.py",
                 "client/eqvoice_client/netcheck.py", "client/eqvoice_client/ui/app.js", "addon/EqVoice/EqVoice.toc",
                 "addon/EqVoice/Lookup.lua"):
        assert want in names, want
    assert not any("__pycache__" in n or n.endswith(".pyc") or "/tests/" in n for n in names)
    assert (pack / "hub.txt").read_text().split() == ["wss://example.trycloudflare.com/ws", "s3cretcode"]
    assert (pack / "client" / "requirements.txt").read_text().split() == ["aiohttp>=3.9", "cryptography>=42"], "no test-only requirements"


def test_the_batch_files_are_windows_batch_files(pack):
    for bat in pack.glob("*.bat"):
        raw = bat.read_bytes()
        raw.decode("ascii")
        assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b""), bat.name + " must have CRLF line endings"
    start = (pack / "START-EqVoice.bat").read_text()
    assert "_setup.bat" in start and "eqvoice_friend.py run" in start and "PYTHONPATH" in start
    assert "venv\\Scripts\\python.exe" in (pack / "_setup.bat").read_text()
    readme = (pack / "README.txt").read_text()
    for must in ("NETCHECK.bat", "START-EqVoice.bat", "SEND-LOGS.bat", "/reload", "F9"):
        assert must in readme, must


def test_the_addon_in_the_pack_is_the_repositorys(pack):
    base = ROOT / "eqvoice-addon" / "EqVoice"
    for f in base.rglob("*"):
        if f.is_file():
            assert (pack / "addon" / "EqVoice" / f.relative_to(base)).read_bytes() == f.read_bytes(), f.name


def test_run_finds_the_game_installs_the_addon_and_starts_the_client(pack, tmp_path, monkeypatch, capsys):
    wow = tmp_path / "Game"
    wow.mkdir()
    (wow / "WoW.exe").write_bytes(b"")
    (wow / "ClassicAPI.dll").write_bytes(b"")
    (pack / "wowdir.txt").write_text(str(wow))
    seen = {}
    import eqvoice_client.__main__ as cm
    monkeypatch.setattr(cm, "main", lambda args: seen.setdefault("args", args) and 0)
    f = friend(pack)
    assert f.cmd_run() == 0
    a = seen["args"]
    assert a[a.index("--signalling") + 1] == "wss://example.trycloudflare.com/ws" and a[a.index("--join-code") + 1] == "s3cretcode"
    assert a[a.index("--wow-dir") + 1] == str(wow) and a[a.index("--ptt-code") + 1] == "F9" and a[a.index("--home") + 1] == str(pack / "data")
    assert (wow / "Interface" / "AddOns" / "EqVoice" / "EqVoice.toc").exists()
    assert "addon installed" in capsys.readouterr().out
    assert "already up to date" in f.install_addon(wow), "a second run changes nothing"


def test_run_warns_when_classicapi_is_missing(pack, tmp_path, monkeypatch, capsys):
    wow = tmp_path / "Game"
    wow.mkdir()
    (wow / "WoW.exe").write_bytes(b"")
    (pack / "wowdir.txt").write_text(str(wow))
    import eqvoice_client.__main__ as cm
    monkeypatch.setattr(cm, "main", lambda args: 0)
    friend(pack).cmd_run()
    assert "no ClassicAPI.dll" in capsys.readouterr().out


def test_the_game_folder_is_asked_for_until_it_is_right(pack, tmp_path, monkeypatch):
    wow = tmp_path / "Real"
    wow.mkdir()
    (wow / "WoW.exe").write_bytes(b"")
    f = friend(pack)
    monkeypatch.setattr(f, "running_wow_folder", lambda: None)
    answers = iter([str(tmp_path / "wrong"), '"%s"' % wow])
    monkeypatch.setattr(f, "Path", Path)
    # the common-folder guesses must not accidentally succeed on the test PC
    monkeypatch.setattr(f, "is_wow_dir", lambda p: Path(p) == wow)
    assert f.find_wow_dir(ask=lambda prompt: next(answers)) == wow
    assert (pack / "wowdir.txt").read_text() == str(wow), "and it remembers"


def test_logs_are_zipped_without_secrets(pack, tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "Desktop").mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    (pack / "data" / "logs").mkdir(parents=True)
    (pack / "data" / "logs" / "eqvoice.log").write_text("TRACE desktop webrtc_path name=Bobby path=direct-nat\n")
    (pack / "data" / "logs" / "netcheck.txt").write_text("hub OK\n")
    (pack / "data" / "config.json").write_text('{"join_code": "s3cretcode", "ui": {"token": "tok"}}')
    wow = tmp_path / "Game"
    sv = wow / "WTF" / "Account" / "ACCT" / "SavedVariables"
    sv.mkdir(parents=True)
    (sv / "EqVoice.lua").write_text("EqVoiceDB = {}\n")
    (wow / "WoW.exe").write_bytes(b"")
    (pack / "wowdir.txt").write_text(str(wow))
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: None)            # do not open Explorer
    f = friend(pack)
    assert f.cmd_logs() == 0
    zips = list((home / "Desktop").glob("EqVoice-logs-*.zip"))
    assert len(zips) == 1
    with zipfile.ZipFile(zips[0]) as z:
        names = z.namelist()
        assert "logs/eqvoice.log" in names and "logs/netcheck.txt" in names and "system.txt" in names
        assert any(n.endswith("EqVoice.lua") for n in names), "the addon's own notes come too"
        assert not any("config" in n for n in names), "the config holds the join code and the window token"
        blob = b"".join(z.read(n) for n in names).decode("utf-8", "replace")
        assert "s3cretcode" not in blob and "tok" not in blob.replace("token", "")
        assert "example.trycloudflare.com" in z.read("system.txt").decode() and "wss://" not in z.read("system.txt").decode()


def test_netcheck_uses_the_baked_in_hub(pack, monkeypatch):
    import eqvoice_client.netcheck as nc
    seen = {}
    monkeypatch.setattr(nc, "run", lambda url, code, show, report: seen.update(url=url, code=code, report=report) or 0)
    assert friend(pack).cmd_netcheck() == 0
    assert seen["url"] == "wss://example.trycloudflare.com/ws" and seen["code"] == "s3cretcode"
    assert seen["report"] == pack / "data" / "logs" / "netcheck.txt"
