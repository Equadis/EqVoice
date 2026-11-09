"""The window (ui/index.html + ui/app.js) in a real headless browser, with a stand-in for the connection to the app: the controls show what
the app says, and what the player does with them is sent back as the commands the app understands."""
import json
import re

import pytest

import edgeharness

pytestmark = pytest.mark.skipif(not edgeharness.available(), reason="needs a Chromium-based browser")

UI = edgeharness.UI

FAKE_SOCKET = """
window.__sent = [];
window.WebSocket = class {
  constructor(url) { window.__ws = this; this.readyState = 1; setTimeout(() => this.onopen && this.onopen(), 0); }
  send(s) { window.__sent.push(JSON.parse(s)); }
  close() {}
};
"""


def window_page(script):
    """The real markup and scripts of the window, with the WebSocket replaced, followed by ``script`` (which calls done(value))."""
    html = (UI / "index.html").read_text(encoding="utf-8")
    markup = re.sub(r"<script[^>]*src=[^>]*></script>", "", html.split("<body>", 1)[1].split("</body>", 1)[0])
    base = edgeharness.page(script, scripts=())
    head, tail = base.split("<title>harness</title>\n", 1)
    scripts = "".join("<script>\n%s\n</script>\n" % (UI / f).read_text(encoding="utf-8") for f in ("voicemodel.js", "app.js"))
    css = "<style>%s</style>" % (UI / "style.css").read_text(encoding="utf-8")
    return head + "<title>harness</title>\n" + css + markup + "<script>\n" + FAKE_SOCKET + "\n</script>\n" + scripts + tail


def peer(name, vstate, **kw):
    p = {"sid": "S-" + name, "name": name, "state": "CONNECTED", "dist": 12.0, "linked": True, "ice": "connected", "path": "direct", "level": 0.0,
         "gain": 1.0, "err": "", "retry_in": 0, "capped": False, "stats": {}, "vstate": vstate, "muted_by_me": False}
    p.update(kw)
    return p


STATE = {
    "t": "state", "signalling": {"state": "registered"}, "bridge": {"status": "live"}, "identity": {"character": "Me", "realm": "R", "sid": "x"},
    "voice": {"mode": "open", "muted": False, "deafened": False, "tx": True, "ptt_label": "F9", "ptt_supported": True, "master": 1.0, "input_gain": 1.0, "calibrated": False, "cal": {"phase": "idle", "left": 0, "quality": "", "text": ""},
              "bitrate": 24, "keyboard": True, "boost": "high", "auto_level": False, "gate_mode": "manual", "gate_db": -52, "muted_players": ["bob", "carol"]},
    "mic": {"state": "ok", "label": "Test mic", "level": 0.2, "gate": True, "gopen": False, "glevel": -61.0, "gthr": -58.0},
    "devices": {"inputs": [], "outputs": []}, "audio": {}, "metrics": {}, "gate": {"open": True, "reason": ""}, "status": {"level": "ok", "text": "Ready", "more": []},
    "peers": [peer("Alice", "s"), peer("Bob", "m", muted_by_me=True), peer("Cy", "d"), peer("Di", "-", state="NEARBY_PENDING"), peer("Eve", "o")],
}


def run(script):
    return edgeharness.run(window_page("const STATE = %s;\nconst feed = (s) => window.__ws.onmessage({ data: JSON.stringify(s) });\n%s" % (json.dumps(STATE), script)),
                           timeout=60)


def test_the_window_shows_the_new_settings_and_the_players_voice_icons():
    out = run("""
      setTimeout(() => {
        feed(STATE);
        const $ = (id) => document.getElementById(id);
        const rows = Array.from(document.querySelectorAll('#peers tr'));
        done({
          boost: $('sel-boost').value, level: $('chk-level').checked, gate: $('sel-gate').value, gdb: $('rng-gate').value, gdbText: $('gate-db').textContent,
          sliderHidden: $('rng-gate').hidden, rowHidden: $('gate-row').hidden, keyLabel: $('lbl-key').textContent,
          thr: $('gate-thr').style.left, fill: $('gate-fill').style.width, open: $('gate-fill').classList.contains('open'),
          names: rows.map((r) => r.querySelector('.pname').textContent),
          icons: rows.map((r) => r.querySelectorAll('.vico').length + ':' + r.querySelector('.vico').alt),
          files: rows.map((r) => r.querySelector('.vico').getAttribute('src')),
          loaded: rows.map((r) => r.querySelector('.vico').complete),
          pm: rows.map((r) => r.querySelector('button.pm').textContent + (r.querySelector('button.pm').classList.contains('on') ? '*' : '')),
          chips: Array.from(document.querySelectorAll('#muted-list .chip')).map((c) => c.firstChild.textContent), emptyHidden: $('muted-empty').hidden,
        });
      }, 50);
    """)
    assert (out["boost"], out["level"], out["gate"], out["gdb"]) == ("high", False, "manual", "-52")
    assert out["gdbText"] == "-52 dB" and out["sliderHidden"] is False and out["rowHidden"] is False, "the manual bar is shown and can be moved"
    assert out["keyLabel"] == "Mute key", "with an open microphone the key mutes"
    assert abs(float(out["thr"].rstrip("%")) - 45.714) < 0.2, "the line is where the gate opens: %r" % out["thr"]
    assert abs(float(out["fill"].rstrip("%")) - 41.43) < 0.2 and out["open"] is False, "the bar is the voice level against the line"
    assert out["names"] == ["Alice", "Bob", "Cy", "Di", "Eve"]
    assert out["icons"] == ["1:speaking", "1:muted (or push to talk and the key is not held)", "1:deafened", "1:has EqVoice", "1:using EqVoice, not talking"], \
        "one icon beside each name: %r" % out["icons"]
    assert out["files"] == ["/static/voice-open.png", "/static/voice-muted.png", "/static/voice-deafened.png", "/static/voice-known.png",
                            "/static/voice-known.png"], "the same pictures as in the game"
    assert out["pm"] == ["Mute", "Muted*", "Mute", "Mute", "Mute"]
    assert out["chips"] == ["bob", "carol"] and out["emptyHidden"] is True


def test_the_icons_sit_right_of_the_name_and_are_not_redrawn_while_nothing_changes():
    out = run("""
      setTimeout(() => {
        feed(STATE);
        const cell = document.querySelector('#peers tr td');
        const order = Array.from(cell.children).map((n) => n.className.baseVal === undefined ? n.className : n.className.baseVal);
        const icon = cell.querySelector('.vico');
        feed(STATE); feed(STATE);
        const same = cell.querySelector('.vico') === icon;
        const changed = JSON.parse(JSON.stringify(STATE));
        changed.peers[0].vstate = 'm';
        feed(changed);
        const now = cell.querySelector('.vico');
        done({ order, same, redrawnOnChange: now !== icon, label: now.alt, count: cell.querySelectorAll('.vico').length });
      }, 50);
    """)
    assert out["order"] == ["dot", "pname", "vico"], "the icon comes after the name: %r" % out["order"]
    assert out["same"] is True, "no flicker: the same icon while its state is the same"
    assert out["redrawnOnChange"] is True and out["count"] == 1 and out["label"].startswith("muted")


def test_the_window_serves_the_five_icon_pictures_and_nothing_else_by_that_name():
    from eqvoice_client.app import VOICE_PICTURES
    assert sorted(set(VOICE_PICTURES) - {"eqvoice-icon.png"}) == sorted(p.name for p in UI.glob("voice-*.png")),         "every voice picture in the window folder is served, and only those (and the window's own icon, below)"
    assert "eqvoice-icon.png" in VOICE_PICTURES and (UI / "eqvoice-icon.png").exists(), "and the window's own icon"


def test_the_noise_gate_rows_follow_the_mode():
    out = run("""
      setTimeout(() => {
        const $ = (id) => document.getElementById(id);
        const auto = JSON.parse(JSON.stringify(STATE)); auto.voice.gate_mode = 'auto';
        feed(auto);
        const a = { slider: $('rng-gate').hidden, row: $('gate-row').hidden, sel: $('sel-gate').value, text: $('gate-db').textContent };
        const off = JSON.parse(JSON.stringify(STATE)); off.voice.keyboard = false;
        feed(off);
        const o = { row: $('gate-row').hidden, sel: $('sel-gate').value, note: $('gate-note').textContent };
        const ptt = JSON.parse(JSON.stringify(STATE)); ptt.voice.mode = 'ptt'; ptt.mic.gopen = true;
        feed(ptt);
        done({ a, o, ptt: $('lbl-key').textContent, open: $('gate-fill').classList.contains('open') });
      }, 50);
    """)
    assert out["a"] == {"slider": True, "row": False, "sel": "auto", "text": "opens at -58 dB"}, "automatic: no slider, and it says where it opens"
    assert out["o"]["row"] is True and out["o"]["sel"] == "off" and "everything" in out["o"]["note"], out["o"]
    assert out["ptt"] == "Push-to-talk key" and out["open"] is True


def test_what_the_player_does_in_the_window_is_sent_as_commands():
    out = run("""
      setTimeout(() => {
        feed(STATE);
        const $ = (id) => document.getElementById(id);
        const fire = (el, type) => el.dispatchEvent(new Event(type, { bubbles: true }));
        $('sel-boost').value = 'low'; fire($('sel-boost'), 'change');
        $('chk-level').checked = true; fire($('chk-level'), 'change');
        $('sel-gate').value = 'auto'; fire($('sel-gate'), 'change');
        $('rng-gate').value = -40; fire($('rng-gate'), 'input');
        const rows = Array.from(document.querySelectorAll('#peers tr'));
        rows[1].querySelector('button.pm').click();      // Bob is muted: this unmutes
        rows[0].querySelector('button.pm').click();      // Alice is not: this mutes
        document.querySelector('#muted-list .chip button').click();       // the x beside "bob"
        $('inp-mute').value = '  Zed '; $('btn-mute-add').click();
        $('inp-mute').value = ''; $('btn-mute-add').click();             // nothing typed: nothing sent
        const ui = window.__sent.filter((m) => m.t === 'ui');
        done({ ui, inputCleared: $('inp-mute').value === '' });
      }, 50);
    """)
    assert out["ui"] == [
        {"t": "ui", "cmd": "boost", "value": "low"},
        {"t": "ui", "cmd": "auto_level", "value": True},
        {"t": "ui", "cmd": "gate", "value": "auto"},
        {"t": "ui", "cmd": "gate_db", "value": -40},
        {"t": "ui", "cmd": "mute_player", "name": "Bob", "value": False},
        {"t": "ui", "cmd": "mute_player", "name": "Alice", "value": True},
        {"t": "ui", "cmd": "mute_player", "name": "bob", "value": False},
        {"t": "ui", "cmd": "mute_player", "name": "Zed", "value": True},
    ], out["ui"]


def test_changing_the_key_in_the_window_still_sends_what_was_pressed():
    out = run("""
      setTimeout(() => {
        feed(STATE);
        document.getElementById('btn-key').click();
        const shown = document.getElementById('btn-key').textContent;
        window.dispatchEvent(new KeyboardEvent('keydown', { code: 'F10', key: 'F10', bubbles: true }));
        done({ shown, sent: window.__sent.filter((m) => m.t === 'ui') });
      }, 50);
    """)
    assert "press a key" in out["shown"]
    assert out["sent"] == [{"t": "ui", "cmd": "ptt_key", "code": "F10", "key": "F10"}]


def test_the_page_follows_distance_readings_smoothly_so_the_boost_never_steps():
    """Readings arrive a couple of times a second, and a player walking fast moves several yards between them. The volume must still
    rise continuously (the old page stepped to each new reading): sample the gain every 20 ms through a fast walk up to someone."""
    out = run("""
      setTimeout(() => {
        const media = window.__ov.media, VM = window.VoiceModel;
        const p = { sid: 's', ep: 1, audio: { volume: 0, muted: false }, fx: null, loud: new VM.Loudness(), loudT: performance.now(), dist: 12, distS: 12,
                    gate: true, slew: 0.1, step: 1, target: 1, gain: 1, userVol: 1, mute: false, refDb: null, levDb: 0, value: 1, send: true };
        media.peers.set('s', p); media.master = 1; media.autoLevel = false;
        const samples = [];
        const sampler = setInterval(() => samples.push(p.gain), 20);
        const readings = [12, 9, 6, 3, 0.5];                               // 6 yd a second
        readings.forEach((d, i) => setTimeout(() => media.setGain({ sid: 's', ep: 1, dist: d, gate: true, value: 1, ms: 300 }), i * 500));
        setTimeout(() => {
          clearInterval(sampler);
          let worst = 0, falls = 0;
          for (let i = 1; i < samples.length; i++) { worst = Math.max(worst, Math.abs(samples[i] - samples[i - 1])); if (samples[i] < samples[i - 1] - 1e-9) falls++; }
          done({ n: samples.length, worst, falls, first: samples[0], last: samples[samples.length - 1], want: VM.curveGain(0.5, media.curve, 0),
                 shouldHaveStepped: VM.curveGain(3, media.curve, 0) - VM.curveGain(6, media.curve, 0) });
        }, 3200);
      }, 50);
    """)
    assert out["n"] > 100, "the page was running its 20 ms ticks"
    assert out["falls"] == 0, "walking up to someone never makes them quieter on the way"
    assert out["shouldHaveStepped"] > 0.4 and out["worst"] < 0.04, \
        "a reading that moves the target by %.2f is followed in small steps (the biggest 20 ms change was %.3f)" % (out["shouldHaveStepped"], out["worst"])
    assert abs(out["last"] - out["want"]) < 0.03 and out["last"] > 1.9, "and it arrives: %r of %r" % (out["last"], out["want"])
