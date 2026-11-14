"""One-click setup: finding the game, installing the addon, the server address, and the support report."""
import json
import zipfile
from pathlib import Path

import pytest

from eqvoice_client import installer
from eqvoice_client.config import Config

ADDON = Path(__file__).resolve().parents[2] / "eqvoice-addon" / "EqVoice"


def make_game(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "WoW.exe").write_bytes(b"MZ")
    return root


def make_addon(root: Path, files=("EqVoice.toc", "Core.lua")) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for f in files:
        (root / f).write_text("-- " + f, encoding="utf-8")
    return root


# ---------------------------------------------------------------- the server address

def test_the_server_address_comes_from_hub_txt_beside_the_program(tmp_path):
    (tmp_path / "hub.txt").write_text("wss://voice.example/ws  abc123\n", encoding="utf-8")
    assert installer.read_hub(tmp_path) == ("wss://voice.example/ws", "abc123")


def test_hub_txt_may_have_no_code_a_bom_or_be_nonsense(tmp_path):
    (tmp_path / "hub.txt").write_bytes(b"\xef\xbb\xbfws://127.0.0.1:8787/ws\r\n")
    assert installer.read_hub(tmp_path) == ("ws://127.0.0.1:8787/ws", "")
    (tmp_path / "hub.txt").write_text("<html>not an address</html>", encoding="utf-8")
    assert installer.read_hub(tmp_path) is None
    (tmp_path / "hub.txt").unlink()
    assert installer.read_hub(tmp_path) is None


# ----------------------------------------------------------------------- the game

def test_a_game_folder_is_the_one_with_wow_exe(tmp_path):
    g = make_game(tmp_path / "Game")
    assert installer.is_wow_dir(g) and not installer.is_wow_dir(tmp_path) and not installer.is_wow_dir(None)


def test_the_saved_folder_wins_and_nothing_else_is_searched(tmp_path, monkeypatch):
    g = make_game(tmp_path / "Game")
    for name in ("running_wow_dirs", "registry_wow_dirs", "common_wow_dirs"):
        monkeypatch.setattr(installer, name, lambda: (_ for _ in ()).throw(AssertionError("should not search")))
    assert installer.find_wow_dir(str(g)) == g


def test_the_finders_are_tried_in_order(tmp_path, monkeypatch):
    run_dir, reg_dir, usual = make_game(tmp_path / "A"), make_game(tmp_path / "B"), make_game(tmp_path / "C")
    monkeypatch.setattr(installer, "running_wow_dirs", lambda: [run_dir])
    monkeypatch.setattr(installer, "registry_wow_dirs", lambda: [reg_dir])
    monkeypatch.setattr(installer, "common_wow_dirs", lambda: [usual])
    assert installer.find_wow_dir("") == run_dir, "a running game is the surest answer"
    monkeypatch.setattr(installer, "running_wow_dirs", lambda: [])
    assert installer.find_wow_dir("") == reg_dir
    monkeypatch.setattr(installer, "registry_wow_dirs", lambda: [tmp_path / "gone"])
    assert installer.find_wow_dir("") == usual
    assert installer.find_wow_dir(str(tmp_path / "stale")) == usual, "a saved folder that no longer exists is not trusted"


def test_when_nothing_is_found_the_player_is_asked_until_they_pick_a_game_or_cancel(tmp_path, monkeypatch):
    for name in ("running_wow_dirs", "registry_wow_dirs", "common_wow_dirs"):
        monkeypatch.setattr(installer, name, lambda: [])
    good = make_game(tmp_path / "Game")
    picks = iter([tmp_path / "not-a-game", good])
    said = []
    assert installer.find_wow_dir("", ask=lambda: next(picks), say=said.append) == good
    assert any("no WoW.exe" in s or "There is no WoW.exe" in s for s in said), "a wrong folder is explained"
    assert installer.find_wow_dir("", ask=lambda: None) is None, "cancel gives up"
    assert installer.find_wow_dir("") is None, "and with nobody to ask there is no answer"


def test_the_usual_places_skip_backups_and_prefer_the_shortest_path(tmp_path):
    root = tmp_path / "D"
    make_game(root / "Games" / "OctoWoW")
    make_game(root / "Games" / "OctoWoW_backup_before_updater")
    make_game(root / "Program Files (x86)" / "World of Warcraft")
    make_game(root / "Photos" / "holiday")                       # no hint in the name: never looked at
    found = installer.common_wow_dirs([root], budget_s=5)
    names = [p.name for p in found]
    assert "OctoWoW" in names and "World of Warcraft" in names
    assert not any("backup" in n for n in names), "updater backups hold a WoW.exe too but are never the game"
    assert "holiday" not in names
    assert [len(str(p)) for p in found] == sorted(len(str(p)) for p in found)


def test_the_usual_places_look_one_level_into_a_client_folder(tmp_path):
    root = tmp_path / "E"
    make_game(root / "Games" / "OctoWoW" / "Client")
    assert [p.name for p in installer.common_wow_dirs([root], budget_s=5)] == ["Client"]


# ----------------------------------------------------------------------- the addon

def test_the_addon_is_copied_then_left_alone(tmp_path):
    wow, src = make_game(tmp_path / "Game"), make_addon(tmp_path / "src")
    r1 = installer.install_addon(wow, src)
    dst = wow / "Interface" / "AddOns" / "EqVoice"
    assert r1.folder == dst and r1.files == 2 and r1.changed == 2 and r1.removed == 0
    assert (dst / "Core.lua").read_text() == "-- Core.lua"
    r2 = installer.install_addon(wow, src)
    assert r2.changed == 0 and "already up to date" in r2.words(), "a second run changes nothing"
    (src / "Core.lua").write_text("-- new core", encoding="utf-8")
    assert installer.install_addon(wow, src).changed == 1, "only the file that differs is written"
    assert (dst / "Core.lua").read_text() == "-- new core"


def test_files_of_an_older_version_are_removed_but_other_files_are_not(tmp_path):
    wow, src = make_game(tmp_path / "Game"), make_addon(tmp_path / "src")
    dst = wow / "Interface" / "AddOns" / "EqVoice"
    dst.mkdir(parents=True)
    (dst / "Old.lua").write_text("-- gone in this version")
    (dst / "Old.toc").write_text("")
    (dst / "notes.txt").write_text("mine")
    r = installer.install_addon(wow, src)
    assert r.removed == 2 and not (dst / "Old.lua").exists() and not (dst / "Old.toc").exists()
    assert (dst / "notes.txt").read_text() == "mine"


def test_a_damaged_download_or_a_protected_folder_is_explained(tmp_path, monkeypatch):
    wow = make_game(tmp_path / "Game")
    with pytest.raises(installer.InstallError) as e:
        installer.install_addon(wow, tmp_path / "nowhere")
    assert "missing" in str(e.value) and not e.value.permission
    src = make_addon(tmp_path / "src")
    real = Path.write_bytes

    def refuse(self, data):
        raise PermissionError("access denied")
    monkeypatch.setattr(Path, "write_bytes", refuse)
    with pytest.raises(installer.InstallError) as e2:
        installer.install_addon(wow, src)
    assert e2.value.permission and "would not let EqVoice write" in str(e2.value), "this is the case that asks for administrator rights"
    monkeypatch.setattr(Path, "write_bytes", real)


def test_the_real_addon_installs_completely(tmp_path):
    """Every file the .toc lists reaches the game folder: a missing module would load the addon half-way."""
    wow = make_game(tmp_path / "Game")
    r = installer.install_addon(wow)
    dst = wow / "Interface" / "AddOns" / "EqVoice"
    listed = [ln.strip() for ln in (ADDON / "EqVoice.toc").read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]
    assert listed and all((dst / f).exists() for f in listed)
    assert r.files == len([p for p in ADDON.rglob('*') if p.is_file()])
    assert {str(p.relative_to(dst)) for p in dst.rglob('*') if p.is_file()} == {str(p.relative_to(ADDON)) for p in ADDON.rglob('*') if p.is_file()}


def test_classicapi_is_checked_for(tmp_path):
    wow = make_game(tmp_path / "Game")
    assert not installer.classicapi_present(wow)
    (wow / "ClassicAPI.dll").write_bytes(b"MZ")
    assert installer.classicapi_present(wow)


# ------------------------------------------- switching the addon on for each character

def put_list(wow: Path, realm: str, char: str, data: bytes, account: str = "ACCT") -> Path:
    p = wow / "WTF" / "Account" / account / realm / char / "AddOns.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def test_a_character_with_the_addon_disabled_is_switched_on_and_nothing_else_changes(tmp_path, monkeypatch):
    """The real failure: a character made before EqVoice was installed lists it as disabled, so /eqvc is an unknown command."""
    monkeypatch.setattr(installer, "running_wow_dirs", lambda: [])
    wow = make_game(tmp_path / "Game")
    nue = put_list(wow, "N'Zoth", "Nue", b"T-RestedXP: disabled\r\nEqVoice: disabled\r\nEqVoiceOld: disabled\r\nBigWigs: enabled\r\n")
    ok = put_list(wow, "N'Zoth", "Equavoice", b"EqVoice: enabled\r\n")
    unlisted = put_list(wow, "C'Thun", "Kyubi", b"BigWigs: enabled")
    unix = put_list(wow, "C'Thun", "Seiryu", b"Bagshui: enabled\nEqVoice : DISABLED\n")
    r = installer.enable_addon(wow)
    assert nue.read_bytes() == b"T-RestedXP: disabled\r\nEqVoice: enabled\r\nEqVoiceOld: disabled\r\nBigWigs: enabled\r\n", "one line, same line endings"
    assert ok.read_bytes() == b"EqVoice: enabled\r\n"
    assert unlisted.read_bytes() == b"BigWigs: enabled\r\nEqVoice: enabled\r\n", "a list with no EqVoice line gets one"
    assert unix.read_bytes() == b"Bagshui: enabled\nEqVoice: enabled\n", "a list that uses plain newlines keeps them"
    assert sorted(r.turned_on) == ["Kyubi (C'Thun)", "Nue (N'Zoth)", "Seiryu (C'Thun)"] and r.still_off == []
    assert not list(wow.rglob("*.eqvoice-tmp")), "no temporary file is left behind"
    again = installer.enable_addon(wow)
    assert again.turned_on == [] and again.still_off == [] and "already on" in again.words(), "a second run changes nothing"


def test_while_the_game_is_open_nothing_is_written_and_the_characters_are_named(tmp_path, monkeypatch):
    """The game rewrites these lists when a character is left, so a change made under it would be undone."""
    wow = make_game(tmp_path / "Game")
    monkeypatch.setattr(installer, "running_wow_dirs", lambda: [Path(str(wow).upper())])         # the same folder, spelled differently
    nue = put_list(wow, "N'Zoth", "Nue", b"EqVoice: disabled\r\n")
    unlisted = put_list(wow, "N'Zoth", "Kaiju", b"BigWigs: enabled\r\n")
    r = installer.enable_addon(wow)
    assert installer.game_is_open(wow) or not installer.IS_WINDOWS
    assert nue.read_bytes() == b"EqVoice: disabled\r\n" and unlisted.read_bytes() == b"BigWigs: enabled\r\n"
    if installer.IS_WINDOWS:
        assert r.turned_on == [] and r.still_off == ["Nue (N'Zoth)"], "only the character that really needs it is named"
    assert installer.addon_off_for(wow) == ["Nue (N'Zoth)"]


def test_another_game_being_open_does_not_hold_this_one_back(tmp_path, monkeypatch):
    wow = make_game(tmp_path / "Game")
    monkeypatch.setattr(installer, "running_wow_dirs", lambda: [make_game(tmp_path / "Other")])
    nue = put_list(wow, "N'Zoth", "Nue", b"EqVoice: disabled\r\n")
    assert installer.enable_addon(wow).turned_on == ["Nue (N'Zoth)"] and nue.read_bytes() == b"EqVoice: enabled\r\n"


def test_a_list_that_cannot_be_written_is_reported_and_setup_goes_on(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "running_wow_dirs", lambda: [])
    wow = make_game(tmp_path / "Game")
    nue = put_list(wow, "N'Zoth", "Nue", b"EqVoice: disabled\r\n")

    def locked(src, dst):
        raise PermissionError("locked")
    monkeypatch.setattr(installer.os, "replace", locked)
    r = installer.enable_addon(wow)
    assert r.still_off == ["Nue (N'Zoth)"] and r.turned_on == []
    assert nue.read_bytes() == b"EqVoice: disabled\r\n" and not list(wow.rglob("*.eqvoice-tmp"))


def test_a_game_without_character_folders_has_nothing_to_switch(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "running_wow_dirs", lambda: [])
    wow = make_game(tmp_path / "Game")
    r = installer.enable_addon(wow)
    assert r.turned_on == [] and r.still_off == [] and installer.addon_off_for(wow) == []


# ------------------------------------------------------------------------ report

def test_the_report_has_the_logs_and_never_the_server_code(tmp_path):
    home, wow = tmp_path / "home", make_game(tmp_path / "Game")
    (home / "logs").mkdir(parents=True)
    (home / "logs" / "eqvoice.log").write_text("2026 INFO started\n", encoding="utf-8")
    sv = wow / "WTF" / "Account" / "ACC" / "SavedVariables"
    sv.mkdir(parents=True)
    (sv / "EqVoice.lua").write_text("EqVoiceDB = {}", encoding="utf-8")
    cfg = Config.load(home, {"join_code": "SECRETCODE", "signalling_url": "wss://voice.example/ws", "bridge": {"wow_dir": str(wow)}})
    cfg.save()
    out = installer.make_report(cfg, tmp_path / "out")
    assert out.parent == tmp_path / "out", "the destination folder is created when it is missing"
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert {"system.txt", "config.json", "logs/eqvoice.log", "addon/ACC-EqVoice.lua"} <= set(names)
        blob = b"".join(z.read(n) for n in names)
        assert b"SECRETCODE" not in blob, "the code that lets people into the server is never in a report"
        assert json.loads(z.read("config.json"))["join_code"] == "***"
        sysinfo = z.read("system.txt").decode()
        assert "voice server host: voice.example" in sysinfo and "game folder found: True" in sysinfo and "ClassicAPI.dll: False" in sysinfo


def test_the_report_goes_to_the_desktop_when_there_is_one(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / "Desktop").mkdir()
    cfg = Config.load(tmp_path / "home")
    out = installer.make_report(cfg)
    assert out.parent == tmp_path / "Desktop" and out.name.startswith("EqVoice-logs-")


# ------------------------------------------------- the product's former name (OctoVoice) is retired, not left beside the new one

def test_the_addon_under_its_old_name_is_taken_away_with_the_files_it_left(tmp_path):
    """Two addons registering /eqvc would fight; the old bridge files in CustomData are noise. Only what is certainly ours goes."""
    wow, src = make_game(tmp_path / "Game"), make_addon(tmp_path / "src", ("EqVoice.toc", "Core.lua"))
    old = wow / "Interface" / "AddOns" / "OctoVoice"
    old.mkdir(parents=True)
    (old / "OctoVoice.toc").write_text("## Title: OctoVoice")
    (old / "Core.lua").write_text("-- old")
    other = wow / "Interface" / "AddOns" / "SomethingElse"
    other.mkdir()
    (other / "OctoVoice.toc").write_text("not ours: a different folder")
    data = wow / "CustomData"
    data.mkdir()
    for name in ("octovoice-Alice.txt", "octovoice.txt", "octovoice-in.txt", "octovoice-st-Alice.txt", "octovoice-ctl.txt"):
        (data / name).write_text("old")
    for name in ("eqvoice-Alice.txt", "notes.txt", "other-octovoice.txt"):
        (data / name).write_text("keep")
    r = installer.install_addon(wow, src)
    assert not old.exists(), "the old addon folder is gone"
    assert r.legacy == 6 and "old OctoVoice addon removed" in r.words()
    assert sorted(p.name for p in data.iterdir()) == ["eqvoice-Alice.txt", "notes.txt", "other-octovoice.txt"], "its bridge files too, and nothing else"
    assert (other / "OctoVoice.toc").exists(), "a folder that is not the old addon is left alone"
    assert (wow / "Interface" / "AddOns" / "EqVoice" / "EqVoice.toc").exists()
    again = installer.install_addon(wow, src)
    assert again.legacy == 0 and "old OctoVoice" not in again.words(), "a second run finds nothing left to remove"


def test_a_folder_with_the_old_name_but_not_the_old_addon_is_not_deleted(tmp_path):
    wow, src = make_game(tmp_path / "Game"), make_addon(tmp_path / "src", ("EqVoice.toc", "Core.lua"))
    mine = wow / "Interface" / "AddOns" / "OctoVoice"
    mine.mkdir(parents=True)
    (mine / "my-notes.txt").write_text("someone's own folder")
    assert installer.install_addon(wow, src).legacy == 0 and (mine / "my-notes.txt").exists()


def test_an_old_character_list_gets_the_new_addon_switched_on(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "running_wow_dirs", lambda: [])
    wow = make_game(tmp_path / "Game")
    nue = put_list(wow, "N'Zoth", "Nue", b"BigWigs: enabled\r\nOctoVoice: enabled\r\n")
    assert installer.enable_addon(wow).turned_on == ["Nue (N'Zoth)"]
    assert nue.read_bytes() == b"BigWigs: enabled\r\nOctoVoice: enabled\r\nEqVoice: enabled\r\n", "the new name is added; the old line is left as it was"


def test_the_report_carries_what_the_game_and_the_app_last_said_and_stays_small(tmp_path):
    home, wow = tmp_path / "home", make_game(tmp_path / "Game")
    (home / "logs").mkdir(parents=True)
    (home / "logs" / "console.txt").write_bytes(b"x" * (installer.REPORT_FILE_CAP + 50_000))          # a log that has grown too long
    (wow / "CustomData").mkdir()
    (wow / "CustomData" / "eqvoice-st-Me.txt").write_text("OVS1 1 2 0000 Me\nS level=ok\n", encoding="utf-8")
    (wow / "CustomData" / "other-addon.txt").write_text("not ours", encoding="utf-8")
    cfg = Config.load(home, {"join_code": "SECRETCODE", "bridge": {"wow_dir": str(wow)}})
    out = installer.make_report(cfg, tmp_path / "out")
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        assert "game/eqvoice-st-Me.txt" in names and "game/other-addon.txt" not in names, "the addon's files, and only those"
        big = z.read("logs/console.txt")
        assert len(big) < installer.REPORT_FILE_CAP + 200 and big.startswith(b"[... the start of this file is left out"), "a long log goes in as its newest part"
    assert out.stat().st_size < 1_000_000, "small enough to drag into a chat"
