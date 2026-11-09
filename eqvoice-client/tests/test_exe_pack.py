"""The one-click pack: EqVoice.exe + hub.txt + a README, and the checks that the built exe really works."""
import os
import sys
import zipfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import make_test_pack  # noqa: E402

from eqvoice_client import browser, installer  # noqa: E402


@pytest.fixture()
def fake_exe(tmp_path):
    exe = tmp_path / "bin" / "EqVoice.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ" + b"x" * 1000)
    return exe


def test_the_exe_pack_is_the_exe_the_address_and_a_readme(tmp_path, fake_exe):
    z = make_test_pack.build_exe_pack("wss://example.trycloudflare.com/ws", "s3cretcode", tmp_path / "dist", fake_exe, rebuild=False)
    assert z.name == "EqVoice-test-pack.zip", "the name realtest and the instructions already use"
    with zipfile.ZipFile(z) as zf:
        assert sorted(zf.namelist()) == ["EqVoice/EqVoice.exe", "EqVoice/README.txt", "EqVoice/VERSION", "EqVoice/hub.txt"]
        assert zf.read("EqVoice/EqVoice.exe") == fake_exe.read_bytes()
        assert zf.read("EqVoice/hub.txt").decode().split() == ["wss://example.trycloudflare.com/ws", "s3cretcode"]
        readme = zf.read("EqVoice/README.txt").decode("ascii")
        assert b"s3cretcode" not in zf.read("EqVoice/README.txt") and b"s3cretcode" not in zf.read("EqVoice/VERSION")
    assert "\r\n" in readme and all(ord(c) < 128 for c in readme), "plain text any Notepad shows correctly"
    for must in ("Double-click  EqVoice.exe", "Extract All", "More info", "/eqvc", "Right-click", "Save report", "Check my connection", "ClassicAPI"):
        assert must in readme, must
    assert ".bat" not in readme and "Python" not in readme, "nothing about scripts or Python: that is the point"


def test_the_exe_in_the_pack_reads_the_address_from_the_hub_txt_it_ships_with(tmp_path, fake_exe):
    z = make_test_pack.build_exe_pack("wss://a.example/ws", "code9", tmp_path / "dist", fake_exe, rebuild=False)
    out = tmp_path / "unzipped"
    with zipfile.ZipFile(z) as zf:
        zf.extractall(out)
    assert installer.read_hub(out / "EqVoice") == ("wss://a.example/ws", "code9")


def test_a_missing_exe_says_how_to_build_it(tmp_path):
    with pytest.raises(SystemExit) as e:
        make_test_pack.build_exe_pack("ws://x/ws", "", tmp_path / "dist", tmp_path / "nope" / "EqVoice.exe", rebuild=False)
    assert "build_exe.py" in str(e.value)


def test_an_exe_older_than_its_sources_is_stale(tmp_path, fake_exe):
    newest = make_test_pack.newest_source()
    os.utime(fake_exe, (newest + 100, newest + 100))
    assert not make_test_pack.exe_is_stale(fake_exe)
    os.utime(fake_exe, (newest - 100, newest - 100))
    assert make_test_pack.exe_is_stale(fake_exe)
    assert make_test_pack.exe_is_stale(tmp_path / "missing.exe")


def test_a_stale_exe_is_rebuilt_before_it_is_packed_and_a_fresh_one_is_not(tmp_path, fake_exe, monkeypatch):
    import build_exe
    calls = []

    def fake_build(out, hub_url="", join_code="", name="EqVoice", clean=True):
        calls.append((Path(out), clean))
        exe = Path(out) / "EqVoice.exe"
        exe.write_bytes(b"MZ" + b"fresh" * 100)
        return exe
    monkeypatch.setattr(build_exe, "build", fake_build)
    newest = make_test_pack.newest_source()
    os.utime(fake_exe, (newest - 100, newest - 100))
    z = make_test_pack.build_exe_pack("ws://x/ws", "", tmp_path / "dist", fake_exe)
    assert calls == [(fake_exe.parent, False)], "rebuilt, reusing the cache"
    with zipfile.ZipFile(z) as zf:
        assert b"fresh" in zf.read("EqVoice/EqVoice.exe"), "and the rebuilt one is what ships"
    os.utime(fake_exe, (newest + 100, newest + 100))
    make_test_pack.build_exe_pack("ws://x/ws", "", tmp_path / "dist2", fake_exe)
    assert len(calls) == 1, "an up-to-date exe is left alone"


def test_build_chooses_the_pack_kind(tmp_path, fake_exe, monkeypatch):
    seen = []
    monkeypatch.setattr(make_test_pack, "build_python_pack", lambda u, c, o: seen.append(("python", u)) or tmp_path / "py.zip")
    monkeypatch.setattr(make_test_pack, "build_exe_pack", lambda u, c, o, e: seen.append(("exe", u)) or tmp_path / "exe.zip")
    make_test_pack.build("ws://a/ws", "", tmp_path, legacy=True)
    make_test_pack.build("ws://b/ws", "", tmp_path, exe=fake_exe)
    assert seen == [("python", "ws://a/ws"), ("exe", "ws://b/ws")]


# --------------------------------------------------------------- the real exe

EXE = ROOT / "dist" / "EqVoice.exe"


@pytest.mark.skipif(sys.platform != "win32" or not EXE.exists(), reason="build it first: python tools/build_exe.py")
def test_the_built_exe_works():
    """Start dist/EqVoice.exe for real: it serves its window and, as a double-click, installs the addon and uses hub.txt."""
    if make_test_pack.exe_is_stale(EXE):
        pytest.skip("dist/EqVoice.exe is older than the sources: python tools/build_exe.py")
    import smoke_exe
    assert smoke_exe.smoke(EXE) == []
    assert smoke_exe.smoke_double_click(EXE) == []


@pytest.mark.skipif(sys.platform != "win32" or not EXE.exists() or browser.find_browser() is None, reason="needs the built exe and Edge or Chrome")
def test_the_built_exe_runs_as_two_copies_with_a_hosted_server_and_survives_losing_the_host():
    """The real exe, twice, against two pretend game windows, with real Edge engines and WebRTC (fake microphone, muted): the first copy hosts
    the voice server, each copy takes a different character, voice flows; when the host is closed the other takes over, and a new copy
    comes back and connects again, with no registration storm and no rate limit."""
    if make_test_pack.exe_is_stale(EXE):
        pytest.skip("dist/EqVoice.exe is older than the sources: python tools/build_exe.py")
    import smoke_exe
    assert smoke_exe.smoke_two_copies(EXE) == []
    assert smoke_exe.smoke_asks_first(EXE) == [], "a second copy with no free game asks first"
    assert smoke_exe.smoke_voice(EXE, say=lambda s: None) == []
