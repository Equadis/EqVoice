"""The simulated game: it must behave like the addon from the client's side of the files."""
import json

import pytest

from eqvoice_client.bridge import frame
from eqvoice_client.bridge.filesource import FileSource, parse_line
from eqvoice_client.bridge.inbox import InboxWriter
from eqvoice_client.bridge.sim import ROSTER_TTL, SimGame, SimWorld

REALM = "Testrealm"


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def game(tmp_path, world, name="Alice", **kw):
    c = Clock()
    g = SimGame(world, name, REALM, tmp_path / name, clock=c, wall=lambda: 1.7e9, **kw)
    g.c = c
    g.inbox = InboxWriter(g.in_path, clock=lambda: g.c.t)
    return g


def read_frame(g):
    return parse_line(g.out_path.read_text(encoding="utf-8"))


def test_world_distances_and_overrides():
    w = SimWorld()
    w.add("Alice", 0, 0).add("Bobby", 30, 40)
    assert w.distance("Alice", "Bobby") == pytest.approx(50.0)
    assert w.distance("Alice", "Alice") is None and w.distance("Alice", "Nobody") is None
    w.pair("Alice", "Bobby", 18, 19)
    assert (w.distance("Alice", "Bobby"), w.distance("Bobby", "Alice")) == (18.0, 19.0), "each game reports its own figure"
    w.see("Alice", "Bobby", None)
    assert w.distance("Alice", "Bobby") == pytest.approx(50.0)
    w.move("Bobby", 200, 0)
    assert w.distance("Alice", "Bobby") is None, "beyond the lookup range the player is simply not loaded"
    w.remove("Bobby")
    assert w.distance("Bobby", "Alice") is None


def test_world_file_is_shared_between_processes(tmp_path):
    p = tmp_path / "world.json"
    a, b = SimWorld(p), SimWorld(p)
    a.add("Alice", 0, 0).add("Bobby", 5, 0).see("Alice", "Bobby", 12.5)
    assert b.distance("Alice", "Bobby") == 12.5 and set(b.chars) == {"Alice", "Bobby"}
    b.move("Bobby", 90, 0)
    assert a.distance("Bobby", "Alice") is None
    p.write_text("{not json", encoding="utf-8")
    assert a.distance("Alice", "Bobby") == 12.5, "a corrupt file is ignored: the last good world stays"


def test_the_game_publishes_a_valid_frame_even_with_nothing_to_do(tmp_path):
    w = SimWorld().add("Alice")
    g = game(tmp_path, w)
    g.step(g.c())
    st = read_frame(g)
    assert (st.character, st.realm, st.login_id) == ("Alice", REALM, 0x5151)
    assert st.enabled and st.lookups and not st.roster_fresh and st.results == () and st.counter == 1


def test_roster_in_distances_out(tmp_path):
    w = SimWorld().add("Alice", 0, 0).add("Bobby", 20, 0).add("Carol", 50, 0).add("Faraway", 300, 0)
    g = game(tmp_path, w)
    g.inbox.write("Alice", ["Bobby", "Carol", "Faraway", "Ghost"], {"checks": 80, "report": 60})
    g.step(g.c())
    st = read_frame(g)
    assert st.roster_fresh and st.roster_size == 4
    assert [(r.name, r.dist) for r in st.results] == [("Bobby", 20.0), ("Carol", 50.0)]
    assert (st.checked, st.loaded, st.misses) == (4, 2, 2), "Faraway and Ghost are not loaded"
    assert st.ack == g.seq


def test_report_radius_is_respected(tmp_path):
    w = SimWorld().add("Alice").add("Bobby", 55, 0).add("Carol", 70, 0)
    g = game(tmp_path, w)
    g.inbox.write("Alice", ["Bobby", "Carol"], {"report": 60})
    g.step(g.c())
    assert [r.name for r in read_frame(g).results] == ["Bobby"]


def test_a_roster_written_for_another_character_is_ignored_and_flagged(tmp_path):
    w = SimWorld().add("Alice").add("Bobby", 5, 0)
    g = game(tmp_path, w)
    g.inbox.write("Mallory", ["Bobby"])
    g.step(g.c())
    st = read_frame(g)
    assert st.id_mismatch and st.results == () and not st.roster_fresh


def test_a_damaged_roster_file_is_ignored(tmp_path):
    w = SimWorld().add("Alice").add("Bobby", 5, 0)
    g = game(tmp_path, w)
    g.inbox.write("Alice", ["Bobby"])
    g.step(g.c())
    g.in_path.write_text(g.in_path.read_text().replace("Bobby", "Bobbz"), encoding="utf-8")
    g.c.t += 0.5
    g.step(g.c())
    assert g.inbox_bad == 1
    assert [r.name for r in read_frame(g).results] == ["Bobby"], "the last good roster stays in force"


def test_stale_roster_fails_closed(tmp_path):
    w = SimWorld().add("Alice").add("Bobby", 5, 0)
    g = game(tmp_path, w)
    g.inbox.write("Alice", ["Bobby"])
    g.step(g.c())
    assert read_frame(g).results
    g.c.t += ROSTER_TTL - 1
    g.step(g.c())
    assert read_frame(g).results, "still inside the 15 s window: still reporting"
    g.c.t += 2
    g.step(g.c())
    st = read_frame(g)
    assert st.results == () and not st.roster_fresh, "no new roster for 15 s: the client is gone, report nobody"


def test_results_expire_when_a_player_stops_being_loaded(tmp_path):
    w = SimWorld().add("Alice").add("Bobby", 5, 0)
    g = game(tmp_path, w)
    g.inbox.write("Alice", ["Bobby"])
    g.step(g.c())
    assert read_frame(g).results
    w.move("Bobby", 500, 0)
    g.c.t += 0.5
    g.step(g.c())
    assert read_frame(g).results == (), "the lookup failed this scan; the result does not linger"


def test_round_robin_budget_and_priority_for_known_players(tmp_path):
    w = SimWorld().add("Alice")
    names = ["Player%03d" % i for i in range(300)]
    for i, n in enumerate(names):
        w.add(n, 500 + i, 0)                                    # all out of range
    w.add("Nearby", 10, 0)
    names.append("Nearby")
    g = game(tmp_path, w, checks=80)
    g.inbox.write("Alice", names)
    seen_checked = []
    found_at = None
    for k in range(6):
        g.c.t += 0.5
        g.step(g.c())
        st = read_frame(g)
        seen_checked.append(st.checked)
        if found_at is None and any(r.name == "Nearby" for r in st.results):
            found_at = k
    assert max(seen_checked) <= 81 and min(seen_checked) >= 1, "a scan never exceeds its budget (plus known players)"
    assert found_at is not None and found_at <= 4, "the whole roster is covered within a few scans"
    for _ in range(3):
        g.c.t += 0.5
        g.step(g.c())
        assert any(r.name == "Nearby" for r in read_frame(g).results), "once found, a player is rechecked every scan"


def test_freeze_logout_and_login(tmp_path):
    w = SimWorld().add("Alice").add("Bobby", 5, 0)
    g = game(tmp_path, w)
    g.step(g.c())
    n = read_frame(g).counter
    g.frozen = True
    assert g.step(g.c()) is None and read_frame(g).counter == n, "a frozen game writes nothing"
    g.frozen = False
    g.logout()
    st = read_frame(g)
    assert st.logout and st.identity[1] == "Alice"
    g.login("Mage")
    g.step(g.c())
    st2 = read_frame(g)
    assert st2.character == "Mage" and not st2.logout and st2.login_id != st.login_id


def test_switches_disable_features_like_the_real_addon(tmp_path):
    w = SimWorld().add("Alice").add("Bobby", 5, 0)
    g = game(tmp_path, w)
    g.inbox.write("Alice", ["Bobby"])
    g.lookups = False
    g.step(g.c())
    st = read_frame(g)
    assert not st.lookups and st.results == ()
    g.lookups, g.enabled = True, False
    g.step(g.c())
    st = read_frame(g)
    assert not st.enabled and st.results == ()


def test_the_file_source_reads_what_the_game_writes(tmp_path):
    w = SimWorld().add("Alice").add("Bobby", 7, 0)
    g = game(tmp_path, w)
    g.inbox.write("Alice", ["Bobby"])
    g.step(g.c())
    fs = FileSource([g.out_path])
    got = fs.poll_once()
    assert got.results[0].name == "Bobby" and got.results[0].dist == 7.0 and fs.inbox_path == g.in_path
