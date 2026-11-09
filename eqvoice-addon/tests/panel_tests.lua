-- The in-game panel: status file in, commands out, the model, the frames, the slash commands.
-- Called from run.lua with its helpers:  require("panel_tests")(Stub, sec, check, eq)

return function(Stub, sec, check, eq)

local function login(opts)
	opts = opts or {}
	opts.fileApi = "WriteCustomFile"
	opts.classic = true
	return Stub.Login(opts)
end

-- A status file the way the desktop app writes it (uifiles.build_status), built here so the Lua side is tested alone;
-- eqvoice-client/tests/test_uifiles.py proves the Python writer produces exactly this.
local function status(OV, seq, who, o)
	o = o or {}
	local lines = { "S " .. (o.fields or "level=ok mode=open mute=0 deaf=0 tx=1 talk=0 vol=100 near=2 conn=1 key=F9 game=1 hub=1 mic=ok app=9") }
	lines[table.getn(lines) + 1] = "M " .. (o.headline or "Ready: Me on Testrealm. 2 players in range, 1 connected.")
	for i = 1, table.getn(o.more or {}) do lines[table.getn(lines) + 1] = "N " .. o.more[i] end
	local peers = o.peers or { "Alice 12.5 1 0 direct", "Bob 30.0 0 0 -" }
	for i = 1, table.getn(peers) do lines[table.getn(lines) + 1] = "P " .. peers[i] end
	local body = table.concat(lines, "\n") .. "\n"
	return string.format("OVS1 %d 1700000000 %04X %s\n%s", seq, OV.Frame.CRC16(body), who or "Me", body)
end

local function parseControl(OV, text)
	local nl = string.find(text, "\n", 1, true)
	local head, body = string.sub(text, 1, nl - 1), string.sub(text, nl + 1)
	local p, n = OV.split(head, " ")
	local out = { magic = p[1], seq = tonumber(p[2]), wall = tonumber(p[3]), crc = p[4], character = p[5], cmds = {} }
	out.crcOk = (string.upper(p[4]) == string.format("%04X", OV.Frame.CRC16(body)))
	local pos = 1
	while pos <= string.len(body) do
		local e = string.find(body, "\n", pos, true)
		local line = string.sub(body, pos, e - 1)
		pos = e + 1
		local f = OV.split(line, " ")
		out.cmds[table.getn(out.cmds) + 1] = { id = f[1], cmd = f[2], arg = f[3] }
	end
	return out
end

local function chatHas(text)
	for i = 1, table.getn(Stub.chat) do
		if string.find(Stub.chat[i], text, 1, true) then return true end
	end
	return false
end

-- ===================================================================== status file
sec("panel: the status file")
do
	local OV = login()
	local S = OV.Status
	eq(S.Alive(Stub.now), false, "no file, no app")
	Stub.fs["eqvoice-st-Me.txt"] = status(OV, 10)
	Stub.Advance(1)
	eq(S.Alive(Stub.now), true, "a good file makes the app alive")
	eq(S.fields.mode, "open", "fields are read")
	eq(S.fields.key, "F9", "the key label")
	check(string.find(S.headline, "Ready: Me", 1, true), "the headline")
	eq(S.npeers, 2, "two players")
	eq(S.peers[1].name, "Alice", "peer name"); eq(S.peers[1].dist, 12.5, "peer distance")
	eq(S.peers[1].connected, true, "peer connected"); eq(S.peers[1].speaking, false, "not speaking")
	eq(S.peers[2].dist, 30, "second distance"); eq(S.peers[2].connected, false, "second not connected")
	local v = S.version
	-- a heartbeat: only the header moves
	Stub.fs["eqvoice-st-Me.txt"] = status(OV, 11)
	Stub.Advance(1)
	eq(S.stats.refreshed, 1, "a heartbeat is taken from the header alone")
	eq(S.version, v, "and is not news")
	Stub.Advance(5)
	eq(S.Alive(Stub.now), true, "alive while the sequence moved within 6 s")
	Stub.Advance(3)
	eq(S.Alive(Stub.now), false, "a silent app is not alive after the TTL")
	Stub.fs["eqvoice-st-Me.txt"] = status(OV, 12, "Me", { fields = "level=warn mode=ptt mute=1 deaf=0 tx=0 talk=0 vol=40 near=0 conn=0 key=Mouse_4 game=1 hub=0 mic=ok app=9", headline = "Connecting to the voice server..." })
	Stub.Advance(1)
	eq(S.Alive(Stub.now), true, "a new sequence brings it back")
	eq(S.version, v + 1, "changed content is news")
	eq(S.fields.vol, "40", "new values"); eq(S.npeers, 2, "peers are kept or replaced (two lines were written)")
	-- file removed (app quit)
	Stub.fs["eqvoice-st-Me.txt"] = nil
	Stub.Advance(1)
	eq(S.Alive(Stub.now), false, "no file: the app is gone at once")
	-- another character's file is not ours
	Stub.fs["eqvoice-st-Me.txt"] = status(OV, 40, "Someone")
	Stub.Advance(1)
	eq(S.Alive(Stub.now), false, "a file written for another character is ignored")
	eq(S.mismatch, true, "and says why")
	-- a torn file (body changed, CRC did not) is refused
	local good = status(OV, 50)
	Stub.fs["eqvoice-st-Me.txt"] = good
	Stub.Advance(1)
	eq(S.Alive(Stub.now), true, "good again")
	local bad0 = S.stats.bad
	Stub.fs["eqvoice-st-Me.txt"] = string.gsub(status(OV, 51), "Ready: Me", "Ready: Xx")
	Stub.Advance(1)
	eq(S.stats.bad, bad0 + 1, "a torn file is counted")
	eq(S.seq, 50, "and its sequence is not taken")
	-- the plain file name works when there is no per-character file
	Stub.fs["eqvoice-st-Me.txt"] = nil
	Stub.fs["eqvoice-st.txt"] = status(OV, 60)
	Stub.Advance(1)
	eq(S.Alive(Stub.now), true, "eqvoice-st.txt is the fallback")
	-- limits
	local many = {}
	for i = 1, 60 do many[i] = "P" .. i .. " 5.0 1 0 direct" end
	local t = S.Parse(status(OV, 70, "Me", { peers = many, more = { "a", "b", "c", "d", "e", "f" } }))
	eq(t.npeers, 40, "at most 40 players are read"); eq(t.nmore, 4, "at most 4 extra lines")
	eq(select and select(2, S.Parse("garbage")) or "noheader", "noheader", "garbage is refused with a reason")
end

-- ===================================================================== the model
sec("panel: what the panel says")
do
	local OV = login()
	local P, S = OV.Panel, OV.Status
	local function with(seq, fields, o)
		o = o or {}
		o.fields = fields
		Stub.fs["eqvoice-st-Me.txt"] = status(OV, seq, "Me", o)
		Stub.Advance(1)
		return P.Model(Stub.now)
	end
	local m = P.Model(Stub.now)
	eq(m.alive, false, "no app"); eq(m.badge, "NO APP", "badge without the app"); eq(m.level, "off", "level off")
	check(string.find(m.text, "not running", 1, true), "it says the app is not running")
	check(m.hint and string.find(m.hint, "Start EqVoice", 1, true), "and what to do")
	m = with(1, "level=ok mode=open mute=0 deaf=0 tx=1 talk=0 vol=80 near=2 conn=1 key=F9 mic=ok")
	eq(m.badge, "MIC ON", "open mic and fine"); eq(m.vol, 80, "volume"); eq(m.mode, "open", "mode")
	check(m.color == P.COLORS.ok, "green")
	m = with(2, "level=ok mode=open mute=0 deaf=0 tx=1 talk=1 vol=80 near=2 conn=1 key=F9 mic=ok")
	eq(m.badge, "TALKING", "talking")
	m = with(3, "level=ok mode=ptt mute=0 deaf=0 tx=0 talk=0 vol=80 near=2 conn=1 key=Mouse_4 mic=ok")
	eq(m.badge, "PTT Mouse 4", "push to talk shows the key, underscores turned back into spaces")
	m = with(4, "level=ok mode=open mute=1 deaf=0 tx=0 talk=0 vol=80 near=2 conn=1 key=F9 mic=ok")
	eq(m.badge, "MUTED", "muted"); check(m.color == P.COLORS.bad, "red"); check(string.find(m.text, "Muted: nobody hears you", 1, true), "the text says so too")
	m = with(5, "level=ok mode=open mute=0 deaf=1 tx=0 talk=0 vol=80 near=2 conn=1 key=F9 mic=ok")
	eq(m.badge, "DEAF", "deafened")
	m = with(6, "level=warn mode=open mute=0 deaf=0 tx=1 talk=0 vol=80 near=0 conn=0 key=F9 mic=ok", { headline = "Connecting to the voice server..." })
	eq(m.badge, "CHECK", "a warning"); check(m.color == P.COLORS.warn, "yellow"); eq(m.text, "Connecting to the voice server...", "its own words")
	m = with(7, "level=bad mode=open mute=0 deaf=0 tx=1 talk=0 vol=80 near=0 conn=0 key=F9 mic=denied", { headline = "Microphone problem (denied)", more = { "No voice window is connected." } })
	eq(m.badge, "ERROR", "a problem"); check(string.find(m.text, "1 more problem", 1, true), "says there is more: " .. m.text)
	-- who is near
	m = with(8, "level=ok mode=open mute=0 deaf=0 tx=1 talk=0 vol=80 near=3 conn=2 key=F9 mic=ok",
		{ peers = { "Alice 12.5 1 1 direct", "Bob 30.0 0 0 -", "Cy 41.0 1 0 relay", "Di 5.0 1 0 direct", "Ed 6.0 1 0 direct", "Fy 7.0 1 0 direct" } })
	eq(m.nrows, 5, "five rows"); eq(m.extra, 1, "and a count of the rest")
	eq(m.rows[1].info, "13 yd  speaking", "a speaking player: " .. m.rows[1].info)
	eq(m.rows[2].info, "30 yd  connecting", "not yet connected")
	eq(m.rows[3].info, "41 yd  relay", "through a relay")
	-- a click is shown at once, then the app has to confirm
	m = with(9, "level=ok mode=open mute=0 deaf=0 tx=1 talk=0 vol=80 near=0 conn=0 key=F9 mic=ok")
	check(P.SetMute(true), "a click goes out")
	eq(P.Model(Stub.now).mute, true, "the panel shows it at once")
	Stub.Advance(2)
	eq(P.Model(Stub.now).mute, true, "still within 3 s")
	Stub.Advance(2)
	eq(P.Model(Stub.now).mute, false, "an app that never confirms: the panel goes back to the truth")
	P.SetMute(true)
	with(10, "level=ok mode=open mute=1 deaf=0 tx=0 talk=0 vol=80 near=0 conn=0 key=F9 mic=ok")
	eq(P.Model(Stub.now).mute, true, "confirmed by the app")
	check(P.pending.mute == nil, "and the override is dropped")
	-- the microphone volume: 100% unless the app says otherwise, and a click is shown at once like the others
	eq(P.Model(Stub.now).input, 100, "no ing field (an older app): as the microphone gives it")
	m = with(11, "level=ok mode=open mute=0 deaf=0 tx=1 talk=0 vol=80 near=0 conn=0 key=F9 mic=ok ing=150")
	eq(m.input, 150, "the app's own answer")
	m = with(12, "level=ok mode=open mute=0 deaf=0 tx=1 talk=0 vol=80 near=0 conn=0 key=F9 mic=ok ing=nonsense")
	eq(m.input, 100, "an unknown value is not shown as if it were real")
	P.SetInput(73); eq(P.pending.ing.value, 75, "73 -> 75"); P.SetInput(-5); eq(P.pending.ing.value, 0, "floor 0"); P.SetInput(999); eq(P.pending.ing.value, 200, "ceiling 200")
	-- the microphone setup: what the app reports, and nothing unknown is believed
	m = with(13, "level=ok mode=open mute=0 deaf=0 tx=1 talk=0 vol=80 near=0 conn=0 key=F9 mic=ok cal=normal calt=4 calq=- cald=1")
	eq(m.cal, "normal", "the step the setup is on"); eq(m.calt, 4, "seconds left"); eq(m.calq, "-", "no result yet"); eq(m.cald, true, "done before")
	m = with(14, "level=ok mode=open mute=0 deaf=0 tx=1 talk=0 vol=80 near=0 conn=0 key=F9 mic=ok cal=banana calq=banana")
	eq(m.cal, "idle", "an unknown step is idle"); eq(m.calq, "-", "an unknown result is none yet"); eq(m.cald, false, "and an older app has never done it")
	m = with(15, "level=ok mode=open mute=0 deaf=0 tx=1 talk=0 vol=80 near=0 conn=0 key=F9 mic=ok cal=done calq=good gdb=-58 ing=85")
	eq(m.cal, "done", "done"); eq(m.calq, "good", "well")
	-- volume snaps to steps of 5 and stays in range
	P.SetVolume(73); eq(P.pending.vol.value, 75, "73 -> 75")
	P.SetVolume(-20); eq(P.pending.vol.value, 0, "floor 0")
	P.SetVolume(500); eq(P.pending.vol.value, 100, "ceiling 100")
end

-- ===================================================================== commands out
sec("panel: commands to the app")
do
	local OV = login()
	local P = OV.Panel
	-- app not running: nothing is sent, and the player is told
	check(not P.SetMode("ptt"), "no app: no command")
	check(chatHas("EqVoice app is not running"), "the player is told")
	eq(Stub.fs["eqvoice-ctl-Me.txt"], nil, "and no file was written")
	Stub.fs["eqvoice-st-Me.txt"] = status(OV, 1)
	Stub.Advance(1)
	check(P.SetMode("ptt"), "app running: sent")
	local a = parseControl(OV, Stub.fs["eqvoice-ctl-Me.txt"])
	eq(a.magic, "OVC1", "magic"); eq(a.character, "Me", "character"); eq(a.crcOk, true, "the CRC covers the body")
	eq(table.getn(a.cmds), 1, "one command"); eq(a.cmds[1].cmd, "mode", "command"); eq(a.cmds[1].arg, "ptt", "argument")
	eq(Stub.fs["eqvoice-ctl.txt"], Stub.fs["eqvoice-ctl-Me.txt"], "the plain file carries the same text")
	check(not string.find(Stub.fs["eqvoice-ctl-Me.txt"], "e+", 1, true), "ids are written without an exponent (%.0f), so the 32-bit game keeps every digit")
	check(string.find(a.cmds[1].id, "^%d+$") ~= nil, "id is plain digits")
	P.SetMute(true); P.SetDeaf(true); P.SetVolume(50); P.Command("test", "1")
	local b = parseControl(OV, Stub.fs["eqvoice-ctl-Me.txt"])
	eq(table.getn(b.cmds), 5, "the file carries the recent commands (a second click inside one desktop poll is not lost)")
	local ok = true
	for i = 2, table.getn(b.cmds) do
		if tonumber(b.cmds[i].id) <= tonumber(b.cmds[i - 1].id) then ok = false end
	end
	check(ok, "ids strictly increase even inside one second")
	check(tonumber(b.cmds[1].id) >= 1700000000 * 100, "and start from the wall clock x 100")
	eq(b.cmds[2].cmd .. b.cmds[2].arg, "mute1", "mute 1"); eq(b.cmds[3].cmd .. b.cmds[3].arg, "deafen1", "deafen 1")
	eq(b.cmds[4].cmd .. b.cmds[4].arg, "volume50", "volume 50"); eq(b.cmds[5].cmd .. b.cmds[5].arg, "test1", "test 1")
	eq(b.seq > a.seq, true, "the file sequence moves")
	for i = 1, 12 do P.Command("test", "1") end
	local c = parseControl(OV, Stub.fs["eqvoice-ctl-Me.txt"])
	eq(table.getn(c.cmds), 8, "at most 8 commands per file")
	eq(c.crcOk, true, "still a valid file")
	-- a failing write is reported, not raised
	Stub.fileError = "disk says no"
	check(not P.Command("test", "1"), "a write error returns false")
	check(chatHas("Could not reach the app"), "and tells the player")
	Stub.fileError = nil
	-- no file API at all (no ClassicAPI)
	local OV2 = Stub.Login({})
	Stub.fs["eqvoice-st-Me.txt"] = status(OV2, 1)
	Stub.Advance(1)
	check(not OV2.Panel.Command("test", "1"), "no file API: nothing sent")
end

-- ===================================================================== slash commands
sec("panel: /eqvc and /eqvoice")
do
	local OV = login()
	eq(SLASH_EQVOICEUI1, "/eqvc", "/eqvc"); eq(SLASH_EQVOICEUI2, "/eqvoice", "/eqvoice")
	check(type(SlashCmdList["EQVOICEUI"]) == "function", "registered")
	eq(SLASH_EQVOICE1, "/ev", "/ev is still there for diagnostics")
	check(type(SlashCmdList["EQVOICE"]) == "function", "and still registered")
	local function ctl() return parseControl(OV, Stub.fs["eqvoice-ctl-Me.txt"]) end
	local function last(n) local c = ctl(); return c.cmds[table.getn(c.cmds)].cmd .. " " .. c.cmds[table.getn(c.cmds)].arg end
	Stub.fs["eqvoice-st-Me.txt"] = status(OV, 1)
	Stub.Advance(1)
	local s = SlashCmdList["EQVOICEUI"]
	s("open"); eq(last(), "mode open", "/eqvc open")
	s("ptt"); eq(last(), "mode ptt", "/eqvc ptt")
	s("mute"); eq(last(), "mute 1", "/eqvc mute")
	s("unmute"); eq(last(), "mute 0", "/eqvc unmute")
	s("deafen"); eq(last(), "deafen 1", "/eqvc deafen")
	s("undeafen"); eq(last(), "deafen 0", "/eqvc undeafen")
	s("input 73"); eq(last(), "input 75", "/eqvc input 73 -> 75")
	s("mic 500"); eq(last(), "input 200", "/eqvc mic works too, and the top is 200")
	s("setup"); eq(last(), "calibrate 1", "/eqvc setup starts the microphone setup")
	s("vol 73"); eq(last(), "volume 75", "/eqvc vol 73 -> 75")
	s("volume 5"); eq(last(), "volume 5", "/eqvc volume 5")
	s("test"); eq(last(), "test 1", "/eqvc test")
	s("OPEN"); eq(last(), "mode open", "upper case works")
	Stub.Advance(4)                      -- let the clicked values lapse, so status shows what the app said
	local n = table.getn(Stub.chat)
	s("vol"); check(chatHas("usage: /eqvc vol 0-100"), "vol without a number explains itself")
	s("input"); check(chatHas("usage: /eqvc input 0-200"), "and so does input")
	s("status"); check(chatHas("Ready: Me on Testrealm"), "status prints the app's headline")
	check(chatHas("volume 100%"), "and the volume")
	s("help"); check(chatHas("/eqvc open"), "help lists the commands")
	s("nonsense"); check(table.getn(Stub.chat) > n, "an unknown word shows help")
	-- before the world is entered
	local OV0 = Stub.Boot({ fileApi = "WriteCustomFile", classic = true })
	Stub.chat = {}
	SlashCmdList["EQVOICEUI"]("open")
	check(chatHas("not ready yet"), "before login it says to log in first")
end

-- ===================================================================== frames
sec("panel: the frames")
do
	local OV = login()
	local P = OV.Panel
	local ind = EqVoiceIndicator
	check(ind ~= nil, "the indicator exists after login")
	eq(ind.badgeText, "NO APP", "and says the app is missing")
	check(ind.glyph.r > 0.6 and ind.glyph.r < 0.7 and ind.glyph.g == ind.glyph.r, "with a grey microphone")
	eq(ind.slash.shown, false, "and no slash")
	eq(ind.kind, "Button", "it is a button"); eq(ind.mouse, true, "that takes the mouse"); eq(ind.movable, true, "can be moved")
	Stub.fs["eqvoice-st-Me.txt"] = status(OV, 1)
	Stub.Advance(1)
	eq(ind.badgeText, "MIC ON", "the indicator follows the status")
	check(ind.glyph.g > 0.9 and ind.glyph.r < 0.5, "a green microphone")
	Stub.fs["eqvoice-st-Me.txt"] = status(OV, 2, "Me", { fields = "level=ok mode=open mute=0 deaf=0 tx=1 talk=1 vol=100 near=2 conn=1 key=F9 mic=ok" })
	Stub.Advance(1)
	eq(ind.badgeText, "TALKING", "it shows talking")
	check(ind.glyph.b > 0.9 and ind.glyph.r < 0.6, "a cyan microphone")
	-- right click mutes, and shows it at once
	Stub.Click(ind, "RightButton")
	eq(parseControl(OV, Stub.fs["eqvoice-ctl-Me.txt"]).cmds[1].cmd, "mute", "right-click sends mute")
	Stub.Advance(0.25)
	eq(ind.badgeText, "MUTED", "and the indicator shows it before the app answers")
	eq(ind.slash.shown, true, "with a red slash over the microphone"); check(ind.glyph.r > 0.9 and ind.glyph.g < 0.4, "which is red")
	-- left click opens the panel
	check(EqVoicePanel == nil, "the panel is built on first use")
	Stub.Click(ind, "LeftButton")
	local f = EqVoicePanel
	check(f ~= nil and f:IsShown(), "left-click opens the panel")
	eq(f.mouse, true, "it takes the mouse"); eq(f.movable, true, "and can be dragged")
	check(f.status.text ~= "", "it has a status line: " .. f.status.text)
	eq(f.bMute.text, "Unmute", "the mute button offers the way back")
	eq(f.bMute.highlight, true, "and is lit")
	eq(f.volText.text, "100%", "volume")
	eq(f.rowName[1].text, "Alice", "the nearby list"); eq(f.rowInfo[1].text, "13 yd", "with distances")
	eq(f.rowName[3].text, "", "empty rows are blank")
	eq(f.bOpen.highlight, true, "the current mode is lit"); eq(f.bPtt.highlight, false, "the other is not")
	check(f.bOpen.enabled and f.bMute.enabled, "buttons work while the app runs")
	-- buttons send commands
	Stub.Click(f.bPtt)
	eq(parseControl(OV, Stub.fs["eqvoice-ctl-Me.txt"]).cmds[table.getn(parseControl(OV, Stub.fs["eqvoice-ctl-Me.txt"]).cmds)].arg, "ptt", "Push to talk")
	Stub.Advance(0.25)
	eq(f.bPtt.highlight, true, "and lights at once"); eq(f.bOpen.highlight, false, "taking the light from open mic")
	check(string.find(f.bPtt.text, "Push to talk", 1, true), "its label: " .. f.bPtt.text)
	Stub.Click(f.bDeaf)
	eq(P.Model(Stub.now).deaf, true, "Deafen")
	-- the output volume is a slider: the number follows the hand at once, the value is sent once the hand rests
	check(f.sVol.frame ~= nil, "the output volume is a slider"); eq(f.sVol.frame.kind, "Slider", "of the client's own kind")
	eq(f.sVol.frame.orientation, "HORIZONTAL", "lying down"); eq(f.sVol.frame.min, 0, "from 0"); eq(f.sVol.frame.max, 100, "to 100"); eq(f.sVol.frame.step, 5, "in steps of 5")
	eq(f.sVol.frame:GetValue(), 100, "it stands at the volume")
	local nsent = table.getn(parseControl(OV, Stub.fs["eqvoice-ctl-Me.txt"]).cmds)
	Stub.Drag(f.sVol.frame, 80)                                       -- the player drags it
	eq(f.volText.text, "80%", "the number follows the hand at once")
	eq(table.getn(parseControl(OV, Stub.fs["eqvoice-ctl-Me.txt"]).cmds), nsent, "and nothing is sent while the hand is moving")
	Stub.Advance(0.5)
	eq(P.Model(Stub.now).vol, 80, "it rested: the volume went out and the panel shows it")
	local vc = parseControl(OV, Stub.fs["eqvoice-ctl-Me.txt"])
	eq(vc.cmds[table.getn(vc.cmds)].cmd .. " " .. vc.cmds[table.getn(vc.cmds)].arg, "volume 80", "as a command to the app")
	Stub.Drag(f.sVol.frame, 95); Stub.Drag(f.sVol.frame, 90); Stub.Advance(0.5)
	vc = parseControl(OV, Stub.fs["eqvoice-ctl-Me.txt"])
	eq(vc.cmds[table.getn(vc.cmds)].arg, "90", "a drag sends where it ended, not every step on the way")
	eq(P.Model(Stub.now).vol, 90, "volume dragged: 80 -> 90")
	Stub.Click(f.bTest)
	local c = parseControl(OV, Stub.fs["eqvoice-ctl-Me.txt"])
	eq(c.cmds[table.getn(c.cmds)].cmd, "test", "Test speakers")
	check(OV.Panel.error == nil, "no drawing error so far: " .. tostring(OV.Panel.error))
	-- Escape closes it (it is in UISpecialFrames), and the indicator toggles it
	local special = false
	for i = 1, table.getn(UISpecialFrames) do if UISpecialFrames[i] == "EqVoicePanel" then special = true end end
	check(special, "Escape closes the panel")
	Stub.Click(ind, "LeftButton")
	eq(f:IsShown(), false, "the indicator closes it again")
	Stub.Click(ind, "LeftButton")
	eq(f:IsShown(), true, "and opens it again")
	-- the app stops: the panel says so and its buttons are off
	Stub.Advance(8)
	check(string.find(f.status.text, "not running", 1, true), "app gone: " .. f.status.text)
	eq(f.bOpen.enabled, false, "buttons are disabled"); eq(ind.badgeText, "NO APP", "indicator"); eq(ind.slash.shown, false, "no slash without the app")
	-- the tooltip
	ind.scripts.OnEnter()
	check(Stub.tip.shown and Stub.tip.lines[1] == "EqVoice: NO APP", "hovering the indicator shows a tooltip: " .. tostring(Stub.tip.lines[1]))
	check(string.find(Stub.tip.lines[3] or "", "Shift-drag", 1, true), "which says how to move it")
	_G.this = ind
	ind.scripts.OnLeave()
	eq(Stub.tip.shown, false, "and leaving hides it")
	-- dragging (with Shift) remembers the place; /eqvc reset forgets it
	_G.this = ind
	ind.scripts.OnDragStart()
	eq(ind.moving, nil, "a drag without Shift does not move it")
	Stub.shift = true
	ind.scripts.OnDragStart()
	eq(ind.moving, true, "Shift-drag moves it")
	ind.pt = { "BOTTOMLEFT", UIParent, "BOTTOMLEFT", 120, 400 }
	ind.scripts.OnDragStop()
	Stub.shift = false
	eq(OV.db.ui.indicatorPos.x, 120, "the indicator's place is saved"); eq(OV.db.ui.indicatorPos.point, "BOTTOMLEFT", "with its anchor")
	f.pt = { "CENTER", UIParent, "CENTER", 5, 6 }
	_G.this = f
	f.scripts.OnDragStop()
	eq(OV.db.ui.panelPos.y, 6, "the panel's place is saved")
	SlashCmdList["EQVOICEUI"]("reset")
	eq(OV.db.ui.indicatorPos, nil, "/eqvc reset forgets both"); eq(ind.pt[1], "TOP", "the indicator is back at the top")
	-- hide and show the indicator
	SlashCmdList["EQVOICEUI"]("hide")
	eq(ind.shown, false, "/eqvc hide"); eq(OV.db.ui.hideIndicator, true, "remembered")
	SlashCmdList["EQVOICEUI"]("show")
	eq(ind.shown, true, "/eqvc show"); eq(OV.db.ui.hideIndicator, nil, "remembered")
	eq(OV.Panel.error, nil, "and still no drawing error")
	-- a saved position is used on the next login
	local db = OV.db
	db.ui.indicatorPos = { point = "TOPLEFT", rel = "TOPLEFT", x = 33, y = -44 }
	local OV3 = Stub.Login({ db = db, fileApi = "WriteCustomFile", classic = true })
	eq(EqVoiceIndicator.pt[4], 33, "the saved indicator position is restored at login")
	-- hidden at login when the player turned it off
	db.ui.hideIndicator = true
	local OV4 = Stub.Login({ db = db, fileApi = "WriteCustomFile", classic = true })
	check(EqVoiceIndicator == nil, "no indicator when it was turned off")
end

-- ===================================================================== the micro bar
sec("panel: the indicator sits beside the micro bar")
do
	local function enter(scale, setup)
		local OV = Stub.Boot({ fileApi = "WriteCustomFile", classic = true })
		local bar = Stub.MicroBar(800, 4, scale or 1)
		if setup then setup(bar) end
		Stub.FireEvent("VARIABLES_LOADED")
		Stub.FireEvent("PLAYER_ENTERING_WORLD")
		return OV, bar, EqVoiceIndicator
	end
	local ART = "Interface\\AddOns\\EqVoice\\"

	-- docked to the right-most micro button, drawn like one
	local OV, bar, ind = enter(1)
	eq(ind.pt[1], "BOTTOMLEFT", "its bottom-left corner..."); eq(ind.pt[2], bar.HelpMicroButton, "...is anchored to the right-most micro button")
	eq(ind.pt[3], "BOTTOMRIGHT", "at that button's bottom-right"); eq(ind.pt[4], 2, "two pixels along"); eq(ind.pt[5], 0, "on the same baseline")
	eq(ind.w, 28, "as wide as a micro button"); eq(ind.h, 58, "and as tall")
	eq(ind.normalTex, ART .. "MicroUp.tga", "the frame art"); eq(ind.pushedTex, ART .. "MicroDown.tga", "and its pressed art")
	eq(ind.hl.path, "Interface\\Buttons\\ButtonHilight-Square", "the client's own highlight"); eq(ind.hl.blend, "ADD", "added to the picture")
	eq(ind.glyph.path, ART .. "MicGlyph.tga", "the microphone"); eq(ind.slash.path, ART .. "MicSlash.tga", "and the slash")
	eq(ind.scale, 1, "at the micro bar's size")
	eq(OV.Panel.error, nil, "no drawing error")
	-- pressing it moves the microphone down a pixel, like the real buttons' pressed art
	_G.this = ind
	ind.scripts.OnMouseDown(); eq(ind.glyph.pt[5], -10, "pressed")
	ind.scripts.OnMouseUp(); eq(ind.glyph.pt[5], -9, "released")

	-- the micro bar is hidden (a UI addon hides it): the indicator floats at the top of the screen, and comes back
	for name, b in pairs(bar) do b.shown = false end
	Stub.Advance(1.5)
	eq(ind.pt[1], "TOP", "with the micro bar hidden it floats at the top"); eq(ind.pt[2], UIParent, "of the screen")
	for name, b in pairs(bar) do b.shown = true end
	Stub.Advance(1.5)
	eq(ind.pt[2], bar.HelpMicroButton, "and docks again when the micro bar returns")

	-- a micro bar gathered into a smaller bar of its own: the indicator takes its size
	local OV2, bar2, ind2 = enter(0.65)
	eq(ind2.pt[2], bar2.HelpMicroButton, "a scaled micro bar is found too"); check(math.abs(ind2.scale - 0.65) < 0.001, "and the indicator is scaled to match: " .. tostring(ind2.scale))

	-- more buttons in the row (Turtle's) are part of it; buttons elsewhere on the screen are not
	local OV3, bar3, ind3 = enter(1)
	local shop = CreateFrame("Button", "ShopMicroButton", UIParent)
	shop.layout = { 1060, 4, 1088 }
	Stub.Advance(1.5)
	eq(ind3.pt[2], shop, "an extra button on the same row counts: the indicator goes after it")
	shop.layout = { 1060, 600, 1088 }
	Stub.Advance(1.5)
	eq(ind3.pt[2], bar3.HelpMicroButton, "one somewhere else on the screen does not")

	-- the player moves it: that wins over the micro bar until /eqvc reset
	local OV4, bar4, ind4 = enter(1)
	_G.this = ind4
	Stub.shift = true
	ind4.scripts.OnDragStart()
	ind4.pt = { "BOTTOMLEFT", UIParent, "BOTTOMLEFT", 300, 500 }
	ind4.scripts.OnDragStop()
	Stub.shift = false
	check(OV4.db.ui.indicatorPos ~= nil and OV4.db.ui.indicatorPos.scale == 1, "the place and the scale are saved")
	for name, b in pairs(bar4) do b.shown = false end
	Stub.Advance(1.5)
	for name, b in pairs(bar4) do b.shown = true end
	Stub.Advance(1.5)
	eq(ind4.pt[4], 300, "it stays where the player put it, whatever the micro bar does")
	SlashCmdList["EQVOICEUI"]("reset")
	eq(OV4.db.ui.indicatorPos, nil, "/eqvc reset forgets that"); eq(ind4.pt[2], bar4.HelpMicroButton, "and docks it beside the micro bar again")

	-- and a client whose frames lack a method the code relies on (a hit rectangle) still draws the button
	local inset
	local OV5 = Stub.Boot({ fileApi = "WriteCustomFile", classic = true })
	local real = _G.CreateFrame
	_G.CreateFrame = function(kind, name, parent, template)
		local f = real(kind, name, parent, template)
		if name == "EqVoiceIndicator" then f.SetHitRectInsets = function(self, l, r, t, b) inset = { l, r, t, b } end end
		return f
	end
	Stub.FireEvent("VARIABLES_LOADED"); Stub.FireEvent("PLAYER_ENTERING_WORLD")
	check(inset ~= nil and inset[3] == 18, "where the client has it, only the visible body of the button takes clicks")
	_G.CreateFrame = real
end

-- ===================================================================== a drawing failure never stops the voice
sec("panel: a drawing failure is contained")
do
	local OV = Stub.Boot({ fileApi = "WriteCustomFile", classic = true })
	local real = _G.CreateFrame
	_G.CreateFrame = function(kind, name, parent, template)
		if name == "EqVoiceIndicator" or name == "EqVoicePanel" then error("template missing: " .. tostring(template)) end
		return real(kind, name, parent, template)
	end
	Stub.FireEvent("VARIABLES_LOADED")
	Stub.FireEvent("PLAYER_ENTERING_WORLD")
	check(OV.started, "the addon started")
	check(OV.Panel.error ~= nil, "the failure was noted: " .. tostring(OV.Panel.error))
	local frames = OV.Bridge.stats.frames
	Stub.Advance(3)
	check(OV.Bridge.stats.frames > frames, "and the bridge keeps publishing")
	Stub.fs["eqvoice-st-Me.txt"] = status(OV, 1)
	Stub.Advance(1)
	SlashCmdList["EQVOICEUI"]("")
	check(chatHas("could not be drawn"), "the player is told to use the text commands")
	SlashCmdList["EQVOICEUI"]("mute")
	local c = parseControl(OV, Stub.fs["eqvoice-ctl-Me.txt"])
	eq(c.cmds[1].cmd, "mute", "and the text commands still work")
	_G.CreateFrame = real
end

end
