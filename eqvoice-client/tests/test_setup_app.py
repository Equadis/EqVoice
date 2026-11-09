"""The one-click installer: the whole install and uninstall in a scratch folder and a scratch registry key (nothing real is touched)."""
import json
import sys
import uuid
from pathlib import Path

import pytest

from eqvoice_client import setup_app

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows registry and shortcuts")

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "eqvoice-addon" / "EqVoice"


@pytest.fixture
def world(tmp_path):
    """A fake game folder with the two mods, a fake payload, scratch places for everything, and a scratch registry key that is removed after."""
    wow = tmp_path / "Game"
    (wow / "Interface" / "AddOns").mkdir(parents=True)
    (wow / "CustomData").mkdir()
    (wow / "WoW.exe").write_bytes(b"MZ")
    for dll in setup_app.MODS:
        (wow / dll).write_bytes(b"MZ")
    (wow / "dlls.txt").write_text("nampower.dll\nClassicAPI.dll\n", encoding="utf-8")
    payload = tmp_path / "payload.exe"
    payload.write_bytes(Path(sys.executable).read_bytes() + b"build-one")        # a real program (Windows reads its icon for the shortcuts), with a tail that tells builds apart
    base = r"Software\EqVoiceSetupTest-" + uuid.uuid4().hex[:8]
    w = {"wow": wow, "payload": payload, "install": tmp_path / "Programs" / "EqVoice", "menu": tmp_path / "Menu" / "EqVoice",
         "data": tmp_path / "AppData" / "EqVoice", "uninst": base + r"\Uninstall", "run": base + r"\Run", "base": base}
    yield w
    import winreg
    for sub in (r"\Uninstall\EqVoice", r"\Uninstall", r"\Run", ""):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, base + sub)
        except OSError:
            pass


def do_install(w, **kw):
    args = dict(addon_src=ADDON, uninstall_root=w["uninst"], run_root=w["run"], menu_dir=w["menu"], data_dir=w["data"], stop=lambda d: 0)
    args.update(kw)
    return setup_app.install(w["payload"], w["install"], w["wow"], **args)


def reg_value(root, name, key=None):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, root + ("\\" + key if key else "")) as k:
        return winreg.QueryValueEx(k, name)[0]


def test_a_fresh_install_puts_everything_where_it_belongs(world):
    w = world
    rep = do_install(w)
    assert not rep.upgraded
    assert (w["install"] / "EqVoice.exe").read_bytes() == w["payload"].read_bytes(), "the program"
    assert "Test Build" in (w["install"] / "VERSION").read_text(), "which build this is, on disk"
    addon = w["wow"] / "Interface" / "AddOns" / "EqVoice"
    assert (addon / "EqVoice.toc").exists() and (addon / "Wizard.lua").exists(), "the addon, all of it"
    m = setup_app.Manifest.load(w["install"] / setup_app.MANIFEST)
    assert m.wow_dir == str(w["wow"]) and m.addon_dir == str(addon) and m.version, "what an uninstall needs to know"
    cfg = json.loads((w["data"] / "config.json").read_text(encoding="utf-8"))
    assert cfg["bridge"]["wow_dir"] == str(w["wow"]), "the game folder is saved: the program never has to ask for it"
    assert reg_value(w["uninst"], "DisplayName", "EqVoice").startswith("EqVoice"), "Settings > Apps lists it"
    assert reg_value(w["uninst"], "UninstallString", "EqVoice") == '"%s" --uninstall' % (w["install"] / "EqVoice.exe")
    assert reg_value(w["run"], "EqVoice") == '"%s"' % (w["install"] / "EqVoice.exe"), "and it starts with Windows"
    assert rep.shortcuts and (w["menu"] / "EqVoice.lnk").exists() and (w["menu"] / "Uninstall EqVoice.lnk").exists(), "the Start menu"
    assert rep.warnings == [], "a good game folder: nothing to warn about"
    text = setup_app.final_text(rep)
    assert "Test Build" in text and "system tray" in text and "starts with Windows" in text and "Settings > Apps" in text and "/eqvoice" in text


def test_an_upgrade_keeps_the_settings_stops_the_old_program_and_replaces_it(world):
    w = world
    do_install(w)
    (w["data"] / "config.json").write_text(json.dumps({"audio": {"mode": "ptt"}, "bridge": {"wow_dir": "old"}}), encoding="utf-8")
    w["payload"].write_bytes(Path(sys.executable).read_bytes() + b"build-two")
    stopped = []
    rep = do_install(w, stop=lambda d: stopped.append(d) or 1)
    assert rep.upgraded, "it knows it is an upgrade"
    assert stopped == [w["install"]], "a running copy in that folder was stopped first"
    assert (w["install"] / "EqVoice.exe").read_bytes() == w["payload"].read_bytes(), "the new program"
    cfg = json.loads((w["data"] / "config.json").read_text(encoding="utf-8"))
    assert cfg["audio"]["mode"] == "ptt", "the player's settings are kept"
    assert cfg["bridge"]["wow_dir"] == str(w["wow"]), "and the game folder is brought up to date"
    assert "updated" in setup_app.final_text(rep)


def test_a_game_that_does_not_load_the_two_mods_is_said_so_in_words(world):
    w = world
    (w["wow"] / "dlls.txt").write_text("VanillaFixes.dll\n", encoding="utf-8")
    rep = do_install(w)
    assert len(rep.warnings) >= 1 and "nampower.dll" in rep.warnings[0] and "dlls.txt" in rep.warnings[0] and "ClassicAPI.dll" in rep.warnings[0]
    (w["wow"] / "ClassicAPI.dll").unlink()
    assert any("not in the game folder" in x for x in setup_app.missing_mods(w["wow"]))
    assert "OctoWoW launcher" in setup_app.mods_text(w["wow"], setup_app.missing_mods(w["wow"]))


def test_a_payload_that_is_missing_is_a_clean_error_not_a_crash(world):
    w = world
    w["payload"].unlink()
    with pytest.raises(setup_app.SetupError) as e:
        do_install(w)
    assert "damaged" in str(e.value) and not (w["install"] / "EqVoice.exe").exists()


def test_uninstall_takes_everything_away_and_keeps_or_deletes_the_settings_as_asked(world):
    w = world
    rep = do_install(w)
    (w["wow"] / "CustomData" / "eqvoice-Me.txt").write_text("x", encoding="utf-8")
    (w["wow"] / "CustomData" / "eqvoice-st-Me.txt").write_text("x", encoding="utf-8")
    (w["wow"] / "CustomData" / "other-addon.txt").write_text("keep me", encoding="utf-8")
    (w["data"] / "logs").mkdir(parents=True, exist_ok=True)
    (w["data"] / "logs" / "eqvoice.log").write_text("log", encoding="utf-8")
    done = setup_app.uninstall(w["install"], keep_data=True, uninstall_root=w["uninst"], run_root=w["run"], menu_dir=w["menu"], data_dir=w["data"],
                               stop=lambda d: 0)
    assert not (w["wow"] / "Interface" / "AddOns" / "EqVoice").exists(), "the addon is out of the game"
    assert not list((w["wow"] / "CustomData").glob("eqvoice*")) and (w["wow"] / "CustomData" / "other-addon.txt").exists(), "its bridge files, and only those"
    assert not w["install"].exists() and not w["menu"].exists(), "the program and the Start menu folder"
    import winreg
    for root, name in ((w["uninst"] + "\\EqVoice", None), (w["run"], "EqVoice")):
        with pytest.raises(OSError):
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, root) as k:
                if name:
                    winreg.QueryValueEx(k, name)
    assert (w["data"] / "logs" / "eqvoice.log").exists(), "the settings and logs are kept when asked"
    assert "program removed" in done
    do_install(w)
    setup_app.uninstall(w["install"], keep_data=False, uninstall_root=w["uninst"], run_root=w["run"], menu_dir=w["menu"], data_dir=w["data"], stop=lambda d: 0)
    assert not w["data"].exists(), "and deleted when asked"


def test_uninstalling_something_that_was_never_installed_is_harmless(world):
    w = world
    done = setup_app.uninstall(w["install"], uninstall_root=w["uninst"], run_root=w["run"], menu_dir=w["menu"], data_dir=w["data"], stop=lambda d: 0)
    assert isinstance(done, list)


def test_stop_running_leaves_this_process_and_other_folders_alone(tmp_path):
    assert setup_app.stop_running(tmp_path / "nowhere", wait_s=0.5) == 0


def test_setup_asks_only_for_what_it_must_and_runs_whole_in_a_scratch_world(world, monkeypatch):
    """The setup program's own flow, silent, against a scratch world: what a double-click does."""
    w = world
    monkeypatch.setenv("EQVOICE_SETUP_SILENT", "1")
    monkeypatch.setenv("EQVOICE_SETUP_ROOT", str(w["install"]))
    monkeypatch.setenv("EQVOICE_SETUP_WOW", str(w["wow"]))
    monkeypatch.setenv("EQVOICE_SETUP_REGBASE", w["base"])
    monkeypatch.setenv("EQVOICE_SETUP_NO_LAUNCH", "1")
    monkeypatch.setenv("EQVOICE_SETUP_NO_SHORTCUTS", "1")
    monkeypatch.setenv("EQVOICE_SETUP_DATA", str(w["data"]))
    monkeypatch.setattr(setup_app, "bundled", lambda name: {"payload/EqVoice.exe": w["payload"], "addon/EqVoice": ADDON}.get(name))
    monkeypatch.setattr(setup_app, "stop_running", lambda *a, **k: 0)
    assert setup_app.run_setup([]) == 0
    assert (w["install"] / "EqVoice.exe").exists() and (w["wow"] / "Interface" / "AddOns" / "EqVoice" / "EqVoice.toc").exists()
    assert reg_value(w["uninst"], "DisplayVersion", "EqVoice")
    monkeypatch.setenv("EQVOICE_SETUP_WOW", str(w["wow"] / "nowhere"))
    assert setup_app.run_setup([]) == 1, "no game folder: nothing is done and it says so"
    monkeypatch.setenv("EQVOICE_SETUP_WOW", str(w["wow"]))
    assert setup_app.run_uninstall(["--quiet"]) == 0
    assert not (w["install"] / "EqVoice.exe").exists() and not (w["wow"] / "Interface" / "AddOns" / "EqVoice").exists()
