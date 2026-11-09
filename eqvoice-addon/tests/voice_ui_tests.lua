-- The controls added to the panel for the voice itself (boost, noise gate, the key, the mute list), the little voice icons beside
-- player names, and the heartbeat flags that carry mute / deafen to other players. Called from run.lua like panel_tests.

return function(Stub, sec, check, eq)

local ART = "Interface\\AddOns\\EqVoice\\"

local function login(opts)
	opts = opts or {}
	opts.fileApi = "WriteCustomFile"
	opts.classic = true
	return Stub.Login(opts)
end

-- a status file the way the app writes it (see panel_tests.lua)
local DEFAULT = "level=ok mode=open mute=0 deaf=0 tx=1 talk=0 vol=100 near=2 conn=1 key=F9 game=1 hub=1 mic=ok app=9 boost=normal gate=auto gdb=-48"
local function status(OV, seq, o)
	o = o or {}
	local lines = { "S " .. (o.fields or DEFAULT), "M " .. (o.headline or "Ready: Me on Testrealm. 2 players in range, 1 connected.") }
	local peers = o.peers or { "Alice 12.5 1 0 direct s 0", "Bob 30.0 0 0 -" }
	for i = 1, table.getn(peers) do lines[table.getn(lines) + 1] = "P " .. peers[i] end
	for i = 1, table.getn(o.muted or {}) do lines[table.getn(lines) + 1] = "X " .. o.muted[i] end
	for i = 1, table.getn((o.devices or {}).inputs or {}) do lines[table.getn(lines) + 1] = "D in " .. i .. " " .. o.devices.inputs[i] end
	for i = 1, table.getn((o.devices or {}).outputs or {}) do lines[table.getn(lines) + 1] = "D out " .. i .. " " .. o.devices.outputs[i] end
	local body = table.concat(lines, "\n") .. "\n"
	return string.format("OVS1 %d 1700000000 %04X Me\n%s", seq, OV.Frame.CRC16(body), body)
end

local function put(OV, seq, o)
	Stub.fs["eqvoice-st-Me.txt"] = status(OV, seq, o)
	Stub.Advance(1)
end

-- the commands the addon has written, as { cmd, "rest of the line" }
local function sent(OV)
	local text = Stub.fs["eqvoice-ctl-Me.txt"]
	local out = {}
	if not text then return out end
	local nl = string.find(text, "\n", 1, true)
	local body = string.sub(text, nl + 1)
	local pos = 1
	while pos <= string.len(body) do
		local e = string.find(body, "\n", pos, true)
		local line = string.sub(body, pos, e - 1)
		pos = e + 1
		local sp = string.find(line, " ", 1, true)
		local rest = string.sub(line, sp + 1)
		local sp2 = string.find(rest, " ", 1, true)
		if string.sub(rest, 1, sp2 - 1) ~= "watch" then        -- (a meter asking for the microphone's level is not what these tests are about)
			out[table.getn(out) + 1] = { cmd = string.sub(rest, 1, sp2 - 1), arg = string.sub(rest, sp2 + 1) }
		end
	end
	return out
end

-- every command the addon has written, the meter's watch requests included
local function sent_all(OV)
	local text = Stub.fs["eqvoice-ctl-Me.txt"]
	local out = {}
	if not text then return out end
	local nl = string.find(text, "\n", 1, true)
	local body = string.sub(text, nl + 1)
	local pos = 1
	while pos <= string.len(body) do
		local e = string.find(body, "\n", pos, true)
		local line = string.sub(body, pos, e - 1)
		pos = e + 1
		local sp = string.find(line, " ", 1, true)
		local rest = string.sub(line, sp + 1)
		local sp2 = string.find(rest, " ", 1, true)
		out[table.getn(out) + 1] = { cmd = string.sub(rest, 1, sp2 - 1), arg = string.sub(rest, sp2 + 1) }
	end
	return out
end

local function lastSent(OV)
	local s = sent(OV)
	local l = s[table.getn(s)]
	return l and (l.cmd .. " " .. l.arg) or nil
end

local function chatHas(text)
	for i = 1, table.getn(Stub.chat) do
		if string.find(Stub.chat[i], text, 1, true) then return true end
	end
	return false
end

-- ===================================================================== the status file, new lines
sec("voice ui: the status file carries who is muted and what each player is doing")
do
	local OV = login()
	local S = OV.Status
	local t = S.Parse(status(OV, 80, { peers = { "Alice 5.0 1 1 direct s 0", "Bob 9.0 1 0 direct m 1", "Cy 11.0 1 0 direct Q", "Di 12.0 0 0 -", "Ed 13.0 1 0 direct d" },
		muted = { "bob carol", "dave" } }))
	eq(t.npeers, 5, "five players")
	eq(t.peers[1].state, "s", "speaking"); eq(t.peers[1].muted, false, "not muted by me")
	eq(t.peers[2].state, "m", "muted"); eq(t.peers[2].muted, true, "and muted by me")
	eq(t.peers[3].state, "-", "a letter the addon does not know is unknown")
	eq(t.peers[4].state, "-", "an older app's row has no state"); eq(t.peers[4].muted, false, "and no mute flag")
	eq(t.peers[5].state, "d", "deafened")
	eq(t.byName.alice.state, "s", "rows are found by lower-case name")
	eq(t.nmuted, 3, "the mute list"); eq(t.muted.bob, true, "bob"); eq(t.muted.carol, true, "carol"); eq(t.muted.dave, true, "dave")
	local many = {}
	for i = 1, 60 do many[i] = "P" .. i .. " 5.0 1 0 direct" end
	local big = {}
	for i = 1, 90 do big[i] = "m" .. i end
	local t2 = S.Parse(status(OV, 81, { peers = many, muted = { table.concat(big, " ") } }))
	eq(t2.npeers, 40, "at most 40 players are read"); eq(t2.nmuted, 60, "and 60 names on the mute list")
end

-- ===================================================================== the model
sec("voice ui: what the panel says about boost, the noise gate, the key and the mute list")
do
	local OV = login()
	local P = OV.Panel
	put(OV, 1)
	local m = P.Model(Stub.now)
	eq(m.boost, "normal", "boost from the app"); eq(m.gate, "auto", "gate from the app"); eq(m.gdb, -48, "the manual bar")
	put(OV, 2, { fields = "level=ok mode=ptt mute=0 deaf=0 tx=0 talk=0 vol=100 near=0 conn=0 key=Mouse_4 mic=ok boost=high gate=manual gdb=-52" })
	m = P.Model(Stub.now)
	eq(m.boost, "high", "a new boost"); eq(m.gate, "manual", "a new gate"); eq(m.gdb, -52, "a new bar"); eq(m.key, "Mouse 4", "the key")
	put(OV, 3, { fields = "level=ok mode=open mute=0 deaf=0 tx=1 talk=0 vol=100 near=0 conn=0 key=F9 mic=ok boost=nonsense gate=nonsense" })
	m = P.Model(Stub.now)
	eq(m.boost, "normal", "an unknown boost is not shown as real"); eq(m.gate, "auto", "nor an unknown gate")
	put(OV, 4, { fields = "level=ok mode=open mute=0 deaf=0 tx=1 talk=0 vol=100 near=0 conn=0 key=F9 mic=ok" })
	m = P.Model(Stub.now)
	eq(m.boost, "normal", "an older app (no boost field): normal"); eq(m.gate, "auto", "and an automatic gate"); eq(m.gdb, -48, "at -48")
	-- clicks show at once
	check(P.SetBoost("low"), "a click goes out"); eq(P.Model(Stub.now).boost, "low", "and shows at once")
	check(not P.SetBoost("loud"), "an unknown boost cannot be sent")
	check(P.SetGate("manual"), "gate click"); eq(P.Model(Stub.now).gate, "manual", "shows at once")
	check(not P.SetGate("loud"), "an unknown gate cannot be sent")
	P.SetGateDb(-47); eq(P.pending.gdb.value, -46, "the bar moves in steps of 2: -47 -> -46")
	P.SetGateDb(-90); eq(P.pending.gdb.value, -80, "most sensitive -80")
	P.SetGateDb(0); eq(P.pending.gdb.value, -20, "least sensitive -20")
	-- who is near: icon state, mute
	put(OV, 5, { peers = { "Alice 12.5 1 1 direct s 0", "Bob 30.0 1 0 direct m 1", "Cy 31.0 0 0 -" }, muted = { "bob" } })
	m = P.Model(Stub.now)
	eq(m.rows[1].state, "s", "Alice is speaking"); eq(m.rows[1].muted, false, "and not muted")
	eq(m.rows[2].state, "b", "Bob is muted BY ME (what his own EqVoice says is hidden behind that)"); eq(m.rows[2].muted, true, "and on my mute list"); check(string.find(m.rows[2].info, "muted", 1, true), "the row says so: " .. m.rows[2].info)
	eq(m.rows[3].state, "i", "Cy: known, nothing more"); eq(m.nmuted, 1, "one name on the list")
	-- clicking a name mutes, and shows it at once
	check(P.TogglePlayerMute("Alice"), "mute Alice")
	eq(lastSent(OV), "pmute alice 1", "the command carries the lower-case name and 1")
	eq(P.Model(Stub.now).rows[1].muted, true, "the row shows it before the app answers")
	check(P.TogglePlayerMute("Alice"), "and again")
	eq(lastSent(OV), "pmute alice 0", "unmute")
	check(P.TogglePlayerMute("Bob"), "Bob is on the list: a click takes him off")
	eq(lastSent(OV), "pmute bob 0", "so it unmutes")
	check(not P.SetPlayerMute("", true), "no name, no command")
	-- a click the app never confirms lapses
	put(OV, 6, { peers = { "Alice 12.5 1 1 direct s 0" } })
	P.SetPlayerMute("Alice", true)
	Stub.Advance(4)
	eq(P.Model(Stub.now).rows[1].muted, false, "an app that never confirms: back to the truth")
	check(P.pending["pm:alice"] == nil, "and nothing is left pending")
end

-- ===================================================================== slash commands
sec("voice ui: slash commands")
do
	local OV = login()
	local s = SlashCmdList["EQVOICEUI"]
	put(OV, 1)
	s("boost high"); eq(lastSent(OV), "boost high", "/eqvc boost high")
	s("boost OFF"); eq(lastSent(OV), "boost off", "upper case works")
	s("boost wat"); check(chatHas("usage: /eqvc boost"), "an unknown boost explains itself")
	s("gate manual"); eq(lastSent(OV), "gate manual", "/eqvc gate manual")
	s("gate off"); eq(lastSent(OV), "gate off", "/eqvc gate off")
	s("gate auto"); eq(lastSent(OV), "gate auto", "/eqvc gate auto")
	s("gate -50"); eq(lastSent(OV), "gatedb -50", "/eqvc gate -50 sets the bar")
	local all = sent(OV)
	eq(all[table.getn(all) - 1].cmd .. " " .. all[table.getn(all) - 1].arg, "gate manual", "... after switching to manual")
	s("gate -51"); eq(lastSent(OV), "gatedb -50", "-51 rounds to a step of 2 (-50)")
	s("gate wat"); check(chatHas("usage: /eqvc gate"), "an unknown gate explains itself")
	s("mute Bob"); eq(lastSent(OV), "pmute bob 1", "/eqvc mute <name> mutes that player, not yourself")
	check(chatHas("muted Bob"), "and says so")
	s("unmute Bob"); eq(lastSent(OV), "pmute bob 0", "/eqvc unmute <name>")
	s("mute"); eq(lastSent(OV), "mute 1", "/eqvc mute alone still mutes you")
	s("unmute"); eq(lastSent(OV), "mute 0", "and unmute alone unmutes you")
	Stub.units = { target = { name = "Zed", player = true } }
	s("mute target"); eq(lastSent(OV), "pmute zed 1", "/eqvc mute target")
	Stub.units = { target = { name = "Wolf", player = false } }
	s("mute target"); check(chatHas("not a player"), "a target that is not a player is refused")
	eq(lastSent(OV), "pmute zed 1", "and nothing is sent")
	Stub.Advance(4)                                         -- the clicks above lapse: the list is what the app says
	put(OV, 2, { muted = { "bob", "amy" } })
	s("muted"); check(chatHas("Blocked players (2): Amy, Bob"), "/eqvc muted lists the names, sorted")
	put(OV, 3, { muted = {} })
	s("muted"); check(chatHas("blocklist is empty"), "and says when it is empty")
	s("icons off"); eq(OV.db.ui.icons, false, "/eqvc icons off is remembered"); s("icons on"); eq(OV.db.ui.icons, nil, "/eqvc icons on")
	s("icons wat"); check(chatHas("usage: /eqvc icons"), "an unknown word explains itself")
	s("help"); check(chatHas("/eqvc boost"), "help lists boost"); check(chatHas("/eqvc gate"), "the gate"); check(chatHas("/eqvc key"), "the key"); check(chatHas("blocklist"), "and the blocklist"); check(chatHas("/eqvc setup"), "and the microphone setup")
	Stub.Advance(4)
	put(OV, 4)
	s("status"); check(chatHas("noise gate auto"), "status names the noise gate")
end

-- ===================================================================== the panel's frames
sec("voice ui: the panel's controls")
do
	local OV = login()
	local P = OV.Panel
	put(OV, 1, { peers = { "Alice 12.5 1 1 direct s 0", "Bob 30.0 1 0 direct m 1", "Cy 31.0 0 0 -" }, muted = { "bob" } })
	Stub.Click(EqVoiceIndicator, "LeftButton")
	local f = EqVoicePanel
	check(f ~= nil and f:IsShown(), "the panel opens")
	eq(f.w, 480, "wide"); eq(f.h, 586, "and tall enough for its four groups: header, microphone, speaker, nearby")
	-- the key: a button of its own; hovering says what it does in the mode you are in
	eq(f.bKey.text, "Keybind: F9", "the key's button")
	f.bKey.scripts.OnEnter()
	check(string.find(Stub.tip.lines[2] or "", "mutes and unmutes", 1, true), "open mic: the key mutes (" .. tostring(Stub.tip.lines[2]) .. ")")
	f.bKey.scripts.OnLeave()
	put(OV, 2, { fields = "level=ok mode=ptt mute=0 deaf=0 tx=0 talk=0 vol=100 near=0 conn=0 key=F9 mic=ok boost=normal gate=auto gdb=-48" })
	eq(f.bPtt.text, "Push to talk", "push to talk is a plain choice beside open mic (the key has its own button)"); eq(f.bPtt.highlight, true, "and is the one lit")
	f.bKey.scripts.OnEnter()
	check(string.find(Stub.tip.lines[2] or "", "hold", 1, true), "push to talk: the key is held to talk (" .. tostring(Stub.tip.lines[2]) .. ")")
	f.bKey.scripts.OnLeave()
	-- boost: a slider with four stops
	eq(f.sBoost.frame:GetValue(), 2, "boost stands at normal (the third stop of four)"); eq(f.boostText.text, "Normal (x2)", "and says so")
	Stub.Drag(f.sBoost.frame, 3); eq(f.boostText.text, "High (x3)", "dragged to the top: the number follows"); Stub.Advance(0.5)
	eq(lastSent(OV), "boost high", "and the app is told once it rests")
	Stub.Drag(f.sBoost.frame, 0); Stub.Advance(0.5)
	eq(lastSent(OV), "boost off", "the bottom is off"); eq(f.boostText.text, "Off", "named")
	Stub.Drag(f.sBoost.frame, 1); Stub.Advance(0.5)
	eq(lastSent(OV), "boost low", "then low")
	-- the sensitivity: ONE bar (the live meter and the noise gate together), and three buttons for the mode
	eq(f.meter, f.sGate, "the meter is the gate's bar: there is no second one")
	eq(f.bGAuto.highlight, true, "the gate is on Auto"); eq(f.bGManual.highlight, false, "not Manual"); eq(f.bGOff.highlight, false, "not Off")
	eq(f.sGate.mode, "auto", "the bar knows its mode"); eq(f.sGate.enabled, false, "and in automatic mode the handle cannot be dragged")
	Stub.Click(f.bGManual); Stub.Advance(0.25)
	eq(lastSent(OV), "gate manual", "Manual goes to manual"); eq(f.bGManual.highlight, true, "and lights"); eq(f.sGate.thr, -48, "the handle is at the bar")
	eq(f.sGate.label.text, "-48 dB", "its value is written under it"); eq(f.sGate.enabled, true, "and can be dragged now")
	Stub.Click(f.bGOff); Stub.Advance(0.25)
	eq(lastSent(OV), "gate off", "Off turns the gate off"); eq(f.bGOff.highlight, true, "named"); eq(f.sGate.enabled, false, "and the handle cannot be moved while the gate is off")
	eq(f.sGate.handle.shown, false, "no handle when there is no bar")
	Stub.Click(f.bGAuto); Stub.Advance(0.25)
	eq(lastSent(OV), "gate auto", "and Auto goes back to automatic")
	Stub.Click(f.bGManual); Stub.Advance(0.25)
	-- dragging the handle: the cursor's place on the track is the bar
	local bf = f.sGate.frame
	bf.layout = { 100, 0, 532 }                                   -- 432 pixels wide, at x 100 on the screen
	local function cursorAtDb(db) Stub.cursorX = 100 + 1 + (db + 90) / 80 * 430 end
	cursorAtDb(-44); Stub.mouseDown = true
	bf.scripts.OnMouseDown(); eq(f.sGate.thr, -44, "pressing on the track puts the handle there"); eq(f.sGate.label.text, "-44 dB", "its number follows at once")
	cursorAtDb(-37); bf.scripts.OnUpdate(); eq(f.sGate.thr, -36, "dragging moves it (two decibel steps: -37 rounds to -36)")
	cursorAtDb(-10); bf.scripts.OnUpdate(); eq(f.sGate.thr, -20, "the handle stops at the end of its range (-20)")
	cursorAtDb(-85); bf.scripts.OnUpdate(); eq(f.sGate.thr, -80, "and at the other (-80)")
	cursorAtDb(-44); bf.scripts.OnUpdate(); Stub.Advance(0.5)
	eq(lastSent(OV), "gatedb -44", "the app is told once the handle rests")
	Stub.mouseDown = false; bf.scripts.OnUpdate(); eq(f.sGate.dragging, false, "letting go anywhere ends the drag")
	Stub.Click(f.bGAuto); Stub.Advance(0.25)
	cursorAtDb(-30); Stub.mouseDown = true; bf.scripts.OnMouseDown(); Stub.mouseDown = false
	eq(f.sGate.dragging, false, "in automatic mode the bar does not follow the mouse"); eq(f.sGate.pendingValue, nil, "and nothing is sent")
	Stub.Click(f.bGManual); Stub.Advance(0.25)
	-- the microphone volume slider
	eq(f.sIn.frame:GetValue(), 100, "the microphone volume stands at 100%"); eq(f.inText.text, "100%", "")
	Stub.Drag(f.sIn.frame, 150); Stub.Advance(0.5)
	eq(lastSent(OV), "input 150", "dragged to 150%")
	-- the list of nearby players: icons, and a click mutes
	put(OV, 3, { peers = { "Alice 12.5 1 1 direct s 0", "Bob 30.0 1 0 direct m 1", "Cy 31.0 0 0 -" }, muted = { "bob" } })
	eq(f.rowName[1].text, "Alice", "the list"); eq(f.rows[1].icon.state, "s", "Alice's icon: speaking")
	eq(f.rows[1].icon.tex.path, ART .. "VoiceOpen.tga", "the speaker with one wave (vol_mid): ordinary speech"); eq(f.rows[1].icon.tex.shown, true, "shown")
	eq(f.rows[1].icon.tex.pt[1], "LEFT", "the icon is to the RIGHT of the name: its left edge ..."); eq(f.rows[1].icon.tex.pt[2], f.rowName[1], "... is at the name's")
	eq(f.rows[1].icon.tex.pt[3], "LEFT", "left"); eq(f.rows[1].icon.tex.pt[4], string.len("Alice") * 6 + 3, "plus the width of the text and a gap")
	eq(f.rows[2].icon.state, "b", "Bob: muted by you"); eq(f.rows[2].icon.tex.path, ART .. "VoiceMuted.tga", "the microphone with a slash"); eq(f.rows[2].icon.tex.alpha, 0.7, "a little dimmer than a player who muted themselves")
	eq(f.rows[2].icon.tex.pt[4], string.len("Bob") * 6 + 3, "and it follows the shorter name")
	eq(f.rowInfo[2].text, "30 yd  muted", "Bob is on my mute list"); check(f.rowName[2].r > 0.8 and f.rowName[2].g < 0.55, "in red")
	eq(f.rows[3].icon.state, "i", "Cy: known to have EqVoice, nothing more"); eq(f.rows[3].icon.tex.path, ART .. "VoiceKnown.tga", "the plain speaker")
	eq(f.rows[3].icon.tex.alpha, 0.45, "dimmed")
	eq(f.rows[4].icon.tex.shown, false, "an empty row has no icon")
	_G.this = f.rows[1]
	Stub.Click(f.rows[1])
	eq(lastSent(OV), "pmute alice 1", "clicking Alice's row mutes her")
	Stub.Advance(0.25)
	eq(f.rowInfo[1].text, "13 yd  speaking  muted", "and the row shows it at once")
	Stub.Click(f.rows[2])
	eq(lastSent(OV), "pmute bob 0", "clicking Bob's row unmutes him")
	_G.this = f.rows[1]
	f.rows[1].scripts.OnEnter()
	check(Stub.tip.shown and Stub.tip.lines[1] == "Alice", "a row's tooltip names the player: " .. tostring(Stub.tip.lines[1]))
	check(string.find(Stub.tip.lines[2] or "", "muted by you", 1, true), "says what the icon says (she was muted a moment ago): " .. tostring(Stub.tip.lines[2]))
	f.rows[1].scripts.OnLeave()
	eq(Stub.tip.shown, false, "and goes away")
	eq(f.bBlock.text, "Blocklist (1)", "the blocklist button counts who is on it")
	-- no app: the new controls are off too
	Stub.Advance(8)
	eq(f.sBoost.enabled, false, "boost off without the app"); eq(f.sIn.enabled, false, "the microphone volume"); eq(f.bGManual.enabled, false, "gate"); eq(f.bKey.enabled, false, "key")
	eq(f.bSetup.enabled, false, "and the setup")
	eq(P.error, nil, "no drawing error")
end

-- ===================================================================== where things sit
sec("voice ui: nothing in the panel hangs over the edge or lies on top of another control")
do
	local OV = login()
	put(OV, 1, { peers = { "Alice 12.5 1 1 direct s 0" } })
	Stub.Click(EqVoiceIndicator, "LeftButton")
	local f = EqVoicePanel
	local function rect(w)                                       -- (left, top, right, bottom) in the panel's own pixels, y down; nil if not placed at its top left
		local pt = w.pt
		if not (pt and pt[1] == "TOPLEFT" and pt[2] == f and w.w and w.h) then return nil end
		return pt[4], -pt[5], pt[4] + w.w, -pt[5] + w.h
	end
	local controls, names = {}, {}
	for i = 1, table.getn(Stub.frames) do
		local w = Stub.frames[i]
		if w.parent == f and w ~= f and w.shown and (w.kind == "Button" or w.kind == "Slider" or w.name == "EqVoiceGateBar") then
			local l, tp, r, b = rect(w)
			if l then controls[table.getn(controls) + 1] = { l, tp, r, b, w.name or "?" } end
		end
	end
	check(table.getn(controls) >= 20, "found the controls (" .. table.getn(controls) .. ")")
	for i = 1, table.getn(controls) do
		local c = controls[i]
		check(c[1] >= 12 and c[3] <= 468 and c[2] >= 0 and c[4] <= 586 - 12, c[5] .. " lies inside the window and its cards (" .. c[1] .. "," .. c[2] .. "," .. c[3] .. "," .. c[4] .. ")")
		for j = i + 1, table.getn(controls) do
			local d = controls[j]
			local apart = c[3] <= d[1] or d[3] <= c[1] or c[4] <= d[2] or d[4] <= c[2]
			-- (the nearby rows are one frame each and the sliders' value texts are not controls: only real controls are compared)
			check(apart, c[5] .. " and " .. d[5] .. " do not overlap")
		end
	end
	-- every shape is rounded: nine pieces of the rounded fill, tinted (the client cannot round a backdrop)
	for _, w in ipairs({ f, f.bOpen, f.bMicDev, f.sGate.frame }) do
		check(w.rbox and table.getn(w.rbox.fill.t) == 9, (w.name or "the window") .. " is drawn in nine rounded pieces")
	end
	eq(f.bOpen.rbox.fill.t[1].path, ART .. "RoundFill.tga", "cut from the one rounded picture")
	eq(f.bOpen.rbox.fill.t[1].tc[2], 0.5, "the top left corner piece is the picture's top left quarter")
	check(f.bOpen.rbox.fill.t[1].w <= 8 and f.bOpen.rbox.fill.t[1].w >= 3, "the corner is a few pixels")
	eq(f.bOpen.fs.shadow[1], 0, "button text has no shadow (dark letters on gold came out smeared)")
	for i = 1, table.getn(f.fontStrings) do
		local fs = f.fontStrings[i]
		local l, tp, r, b = rect(fs)
		if l and fs.text ~= "" then check(l >= 12 and r <= 468, "the text '" .. string.sub(fs.text, 1, 30) .. "' stays inside the window (" .. l .. ".." .. r .. ")") end
	end
end

-- ===================================================================== the one sensitivity bar
sec("voice ui: the sensitivity bar is the meter and the gate in one")
do
	local OV = login()
	local P = OV.Panel
	local FIELDS = "level=ok mode=%s mute=0 deaf=0 tx=%d talk=0 vol=100 near=0 conn=0 key=F9 mic=ok boost=normal gate=%s gdb=-48 gl=%d gt=%d go=%d"
	local function status(seq, mode, tx, gate, gl, gt, go)
		put(OV, seq, { fields = string.format(FIELDS, mode, tx, gate, gl, gt, go) })
	end
	Stub.Click(EqVoiceIndicator, "LeftButton")
	local f = EqVoicePanel
	local bar = f.sGate
	local function width(box) return box.w or 0 end                        -- the bar's colours are rounded boxes: how wide each is laid out (0: hidden)
	-- the track is 432 wide with a 1 pixel edge; -90..-10 dB across it: 5.375 px a decibel
	status(1, "open", 1, "auto", -30, -52, 1)
	eq(bar.level, -30, "the bar draws the level the app measured on the gate's own signal (gl)")
	eq(math.floor(bar.pct + 0.5), 75, "which is three quarters of the way along the track")
	eq(f.meter.pct, bar.pct, "and that is the one meter value there is")
	eq(f.sGate.thr, -52, "in automatic mode the handle shows the bar the gate holds the signal against now (gt)")
	check(width(bar.amberDim) > 0 and width(bar.greenDim) > 0, "the track is amber below the bar and green above it")
	eq(width(bar.greenDim), 430, "the green runs the whole length of the track, edge to edge inside the ring")
	check(width(bar.amberDim) < width(bar.greenDim), "and the amber is laid over its left part, up to the bar")
	check(width(bar.amber) > 0 and width(bar.green) > 0, "a signal above the bar fills both: the amber part (blocked) and the green part (passing)")
	eq(width(bar.amber), width(bar.amberDim), "all the amber is lit, because the signal is past it")
	check(width(bar.green) > 0 and width(bar.green) < width(bar.greenDim), "and only part of the green")
	eq(f.gateNote.text, "Gate open: your voice is being sent.", "the line under it says the gate is open and you are sending")
	eq(bar.frame.rbox.edge.color[2] > 0.7, true, "and the bar edge turns green")
	-- below the bar: the signal is still drawn (to compare room noise with speech), amber only, and the gate is shut
	status(2, "open", 1, "auto", -70, -52, 0)
	eq(bar.level, -70, "the live signal is drawn even though the gate blocks it")
	check(width(bar.amber) > 0 and width(bar.amber) < width(bar.amberDim) and width(bar.green) == 0, "only the amber part is lit: below the bar")
	eq(f.gateNote.text, "Below the bar: nothing is sent.", "and the line says so")
	eq(bar.frame.rbox.edge.color[2] < 0.5, true, "the edge is the plain one")
	-- push to talk is still respected: the gate opens on the voice, but nothing is sent until the key is held
	status(3, "ptt", 0, "auto", -30, -52, 1)
	eq(f.gateNote.text, "Voice heard. Hold F9 to talk.", "gate open, key up: the player is told to hold the key")
	status(4, "ptt", 1, "auto", -30, -52, 1)
	eq(f.gateNote.text, "Gate open: your voice is being sent.", "key held: sending")
	-- muted: nothing is sent whatever the gate does
	put(OV, 5, { fields = "level=ok mode=open mute=1 deaf=0 tx=0 talk=0 vol=100 near=0 conn=0 key=F9 mic=ok boost=normal gate=auto gdb=-48 gl=-30 gt=-52 go=1" })
	eq(f.gateNote.text, "Muted: nothing is sent.", "muted says muted")
	-- manual: the handle is the player's setting, not the app's running bar
	status(6, "open", 1, "manual", -30, -51, 1)
	eq(bar.thr, -48, "in manual mode the handle stands at the setting (gdb), not at the gate's own -3 dB hysteresis bar")
	eq(bar.frame.alpha, 1, "and can be used")
	-- gate off: the live level is still drawn, no handle, no bar
	put(OV, 7, { fields = "level=ok mode=open mute=0 deaf=0 tx=1 talk=0 vol=100 near=0 conn=0 key=F9 mic=ok boost=normal gate=off gdb=-48 gl=-35" })
	eq(bar.mode, "off", "gate off"); eq(bar.handle.shown, false, "no handle"); check(width(bar.green) > 0, "the live level is still drawn")
	eq(f.gateNote.text, "Sensitivity off: all sound is sent.", "and the line says everything goes out")
	-- an app that does not send gl (an older one): the old level still moves the bar
	put(OV, 8, { fields = "level=ok mode=open mute=0 deaf=0 tx=1 talk=0 vol=100 near=0 conn=0 key=F9 mic=ok boost=normal gate=auto gdb=-48 lvl=50" })
	eq(math.floor(f.meter.pct + 0.5), 50, "without gl the plain level (lvl) is what the meter shows")
	-- the app is gone: the bar is empty and the handle stays put but cannot be used
	Stub.Advance(10)
	eq(bar.level, nil, "no app: no signal"); eq(bar.enabled, false, "and no dragging"); eq(f.gateNote.text, "", "and no claim about sending")
	eq(P.error, nil, "no drawing error")
end

-- ===================================================================== sliders move only under a hand; the bar is live
sec("voice ui: a slider takes values only from a hand, a saved zero volume is not replayed, the meter reads fast")
do
	local OV = login()
	local P = OV.Panel
	put(OV, 1)
	Stub.Click(EqVoiceIndicator, "LeftButton")
	local f = EqVoicePanel
	local n = table.getn(sent(OV))
	f.sIn.frame:SetValue(0); f.sVol.frame:SetValue(0); f.sBoost.frame:SetValue(0); Stub.Advance(1)
	eq(table.getn(sent(OV)), n, "a slider the client moves by itself (layout, settling) sends nothing: only a pressed mouse does")
	Stub.Drag(f.sIn.frame, 120); Stub.Advance(0.5)
	eq(lastSent(OV), "input 120", "with a hand on it, it does")
	-- the settings saved from an earlier session: an input volume of nothing is dropped, the others are handed over
	OV.db.settings = { input = "0", mode = "ptt", gate = "manual" }
	put(OV, 2, { fields = "level=ok mode=open mute=0 deaf=0 tx=1 talk=0 vol=100 near=0 conn=0 key=F9 mic=ok boost=normal gate=auto gdb=-48 ing=100" })
	P.StartReplay(Stub.now)
	local names = {}
	for i = 1, table.getn(P.replayQueue or {}) do names[P.replayQueue[i][1]] = true end
	eq(names.input, nil, "the saved input volume of 0 is not replayed"); eq(names.mode, true, "the others are"); eq(OV.db.settings.input, nil, "and it is forgotten")
	-- the meter: the file is read at once while a meter is on screen
	Stub.Advance(1)
	eq(OV.Status.POLL, P.LIVE_S, "with the panel open the status is read at the fast rate")
	f:Hide(); Stub.Advance(4)
	eq(OV.Status.POLL, 0.5, "and at the slow one when nothing needs it")
	Stub.Click(EqVoiceIndicator, "LeftButton")
	-- the bar eases between readings
	local bar = EqVoicePanel.sGate
	bar.Set(-30, -52, "auto", true)
	bar.Set(-80, -52, "auto", false)
	eq(bar.level, -80, "the new reading is the target")
	check(bar.shown > -80, "the drawing is still on its way down (" .. tostring(bar.shown) .. ")")
	local function frames(n, dt) _G.arg1 = dt; for _ = 1, n do bar.frame.scripts.OnUpdate() end end
	frames(1, 0.05); local after1 = bar.shown
	check(after1 > -80 and after1 < -30, "a frame later it has moved part of the way (" .. tostring(after1) .. ")")
	frames(30, 0.05)
	eq(bar.shown, -80, "and arrives")
	bar.Set(-20, -52, "auto", true); frames(1, 0.05)
	check(bar.shown > -40, "up is quicker than down: one frame took it to " .. tostring(bar.shown))
	eq(P.error, nil, "no drawing error")
end

-- ===================================================================== save the logs from the panel
sec("voice ui: the Save logs button asks the app for the zip and the game says where it is")
do
	local OV = login()
	local P = OV.Panel
	put(OV, 1)
	Stub.Click(EqVoiceIndicator, "LeftButton")
	local f = EqVoicePanel
	eq(f.bReport.text, "Save logs to send", "there is a button for it")
	eq(f.bReport.enabled, true, "it works while the app runs")
	Stub.Click(f.bReport); Stub.Advance(0.25)
	eq(lastSent(OV), "report 1", "it asks the app to make the zip")
	put(OV, 2, { fields = "level=ok mode=open mute=0 deaf=0 tx=1 talk=0 vol=100 near=0 conn=0 key=F9 mic=ok boost=normal gate=auto gdb=-48 rpt=EqVoice-logs-20261108-101010.zip" })
	check(string.find(f.hint.text, "EqVoice-logs-20261108-101010.zip", 1, true), "the panel names the file the app saved: " .. f.hint.text)
	put(OV, 3, { fields = "level=ok mode=open mute=0 deaf=0 tx=1 talk=0 vol=100 near=0 conn=0 key=F9 mic=ok boost=normal gate=auto gdb=-48" })
	check(not string.find(f.hint.text, "EqVoice-logs", 1, true), "and stops saying so when the app stops (about twenty-five seconds)")
	Stub.Advance(10)
	eq(f.bReport.enabled, false, "no app: the button is off")
	eq(P.error, nil, "no drawing error")
end

-- ===================================================================== changing the key
sec("voice ui: change the key in the game")
do
	local OV = login()
	local P = OV.Panel
	put(OV, 1)
	Stub.Click(EqVoiceIndicator, "LeftButton")
	local f = EqVoicePanel
	_G.arg1 = nil
	check(EqVoiceKeyCapture == nil, "nothing listens for keys until asked")
	Stub.Click(f.bKey)
	Stub.Advance(0.25)
	local c = EqVoiceKeyCapture
	check(c ~= nil and c.shown, "the key listener is up"); eq(c.keyboard, true, "it takes the keyboard"); eq(c.mouse, true, "and the mouse")
	eq(P.capturing, true, "capturing"); check(string.find(f.bKey.text, "Press a key", 1, true), "the button says so: " .. f.bKey.text)
	check(chatHas("Press the key"), "and so does the chat")
	_G.arg1 = "ESCAPE"; c.scripts.OnKeyDown()
	eq(c.shown, false, "Escape stops listening"); eq(P.capturing, false, "capturing is over"); check(chatHas("key not changed"), "and says nothing changed")
	eq(lastSent(OV), nil, "and nothing was sent")
	Stub.Click(f.bKey)
	_G.arg1 = "F10"; c.scripts.OnKeyDown()
	eq(lastSent(OV), "key F10", "the game's name for the key goes to the app as it is"); eq(c.shown, false, "and the listener is gone")
	Stub.Click(f.bKey)
	_G.arg1 = "Button4"; c.scripts.OnMouseDown()
	eq(lastSent(OV), "key Button4", "a mouse button goes the same way")
	Stub.Click(f.bKey)
	_G.arg1 = "LeftButton"; c.scripts.OnMouseDown()
	eq(lastSent(OV), "key Button4", "a left click cancels instead (nothing new sent)"); eq(P.capturing, false, "")
	Stub.Click(f.bKey)
	_G.arg1 = "RightButton"; c.scripts.OnMouseDown()
	eq(P.capturing, false, "so does a right click")
	Stub.Click(f.bKey)
	Stub.Click(f.bKey)
	eq(P.capturing, false, "clicking the button again cancels")
	put(OV, 2)
	Stub.Click(f.bKey)
	Stub.Advance(11)
	eq(P.capturing, false, "and ten seconds of nothing gives up"); eq(c.shown, false, "")
	put(OV, 3)
	Stub.Click(f.bKey)
	_G.arg1 = "UNKNOWN"; c.scripts.OnKeyDown()
	eq(P.capturing, true, "a key the client cannot name is ignored: still waiting")
	P.StopCapture(nil)
	SlashCmdList["EQVOICEUI"]("key")
	eq(P.capturing, true, "/eqvc key starts it too")
	-- without the app there is nothing to tell
	P.StopCapture(nil)
	Stub.Advance(8)
	local before = lastSent(OV)
	check(not P.CaptureKey(), "no app: no listener"); check(chatHas("key cannot be changed"), "and the player is told")
	eq(lastSent(OV), before, "")
end

-- ===================================================================== the icons
sec("voice ui: which icon for whom")
do
	local OV = login()
	local I, P = OV.Icons, OV.Panel
	eq(I.Resolve("Alice", Stub.now), nil, "no app, no heartbeats: nobody is known")
	eq(I.Resolve("Me", Stub.now), "x", "and yourself: no app, so not connected")
	put(OV, 1, { peers = { "Alice 5.0 1 1 direct s 0", "Bob 9.0 1 0 direct m 1", "Cy 11.0 1 0 direct d", "Di 12.0 0 0 -", "Ed 13.0 1 0 direct o" } })
	eq(I.Resolve("Alice", Stub.now), "s", "Alice: speaking"); eq(I.Resolve("Bob", Stub.now), "m", "Bob: muted"); eq(I.Resolve("Cy", Stub.now), "d", "Cy: deafened")
	eq(I.Resolve("Ed", Stub.now), "o", "Ed: open microphone")
	eq(I.Resolve("Di", Stub.now), "i", "Di: the app knows them but has nothing more")
	eq(I.Resolve("alice", Stub.now), "s", "the app's rows are matched without regard to case"); eq(I.Resolve("Zed", Stub.now), nil, "a stranger gets nothing")
	-- yourself: from the panel's model, so a click shows at once
	eq(I.Resolve("Me", Stub.now), "o", "you: microphone open")
	put(OV, 2, { fields = "level=ok mode=open mute=0 deaf=0 tx=1 talk=1 vol=100 near=0 conn=0 key=F9 mic=ok" })
	eq(I.Resolve("Me", Stub.now), "s", "you: speaking")
	put(OV, 3, { fields = "level=ok mode=open mute=1 deaf=0 tx=0 talk=0 vol=100 near=0 conn=0 key=F9 mic=ok" })
	eq(I.Resolve("Me", Stub.now), "m", "you: muted")
	put(OV, 4, { fields = "level=ok mode=open mute=1 deaf=1 tx=0 talk=0 vol=100 near=0 conn=0 key=F9 mic=ok" })
	eq(I.Resolve("Me", Stub.now), "d", "you: deafened (which wins over muted)")
	put(OV, 5, { fields = "level=ok mode=ptt mute=0 deaf=0 tx=0 talk=0 vol=100 near=0 conn=0 key=F9 mic=ok" })
	eq(I.Resolve("Me", Stub.now), "m", "you: push to talk and the key not held looks muted")
	put(OV, 6, { fields = "level=ok mode=ptt mute=0 deaf=0 tx=1 talk=0 vol=100 near=0 conn=0 key=F9 mic=ok" })
	eq(I.Resolve("Me", Stub.now), "o", "you: push to talk with the key held is an open microphone")
	put(OV, 7)
	P.SetMute(true)
	eq(I.Resolve("Me", Stub.now), "m", "a click on Mute shows in your own icon at once")
	-- other players' heartbeats: flags say muted / deafened / app running
	put(OV, 8, { peers = {} })
	Stub.Hear("H:AAAAAAAA:1:4:1", "SAY", "Eve");   eq(I.Resolve("Eve", Stub.now), "i", "a heartbeat with only 'enabled': has EqVoice, no app")
	Stub.Hear("H:BBBBBBBB:1:4:9", "SAY", "Fay");   eq(I.Resolve("Fay", Stub.now), "o", "the app is running")
	Stub.Hear("H:CCCCCCCC:1:4:B", "SAY", "Gus");   eq(I.Resolve("Gus", Stub.now), "m", "muted (enabled + muted + app)")
	Stub.Hear("H:DDDDDDDD:1:4:D", "SAY", "Hal");   eq(I.Resolve("Hal", Stub.now), "d", "deafened (enabled + deafened + app)")
	put(OV, 9, { peers = { "Fay 5.0 1 1 direct s 0" } })
	eq(I.Resolve("Fay", Stub.now), "s", "the other player's own app beats their heartbeat")
	put(OV, 10, { peers = { "Fay 5.0 0 0 -" } })
	eq(I.Resolve("Fay", Stub.now), "o", "but a row with nothing known falls back to the heartbeat")
	eq(I.Describe("s"), "speaking", "words for the tooltip"); eq(I.Describe("d"), "deafened", ""); eq(I.Describe(nil), nil, "")
end

sec("voice ui: drawing the icons")
do
	local OV = login()
	local I = OV.Icons
	local seq = 1
	local PEERS = { peers = { "Alice 5.0 1 1 direct s 0", "Bob 9.0 1 0 direct d 0", "Cy 11.0 1 0 direct m 0" } }
	local function alive() seq = seq + 1; put(OV, seq, PEERS) end        -- the app writes every 2 s: keep it alive through the long waits
	put(OV, 1, PEERS)
	-- the target frame: a name centred in its frame
	local tf = CreateFrame("Frame", "TargetFrame", UIParent)
	local name = tf:CreateFontString("TargetName", "OVERLAY", "GameFontNormalSmall")
	name:SetText("Alice")
	Stub.Advance(0.5)
	local ic = I.surf[1] and I.surf[1].icon
	check(ic ~= nil, "an icon was made on the target frame")
	eq(ic.tex.shown, true, "and shown"); eq(ic.tex.path, ART .. "VoiceOpen.tga", "the speaker with one wave: she is speaking")
	eq(ic.tex.r, 1, "drawn as it is (not tinted)")
	check(ic.btn ~= nil, "on a unit frame the icon is a button")
	eq(ic.btn.pt[1], "LEFT", "its left edge ..."); eq(ic.btn.pt[2], name, "... is at the name's"); eq(ic.btn.pt[3], "CENTER", "centre")
	eq(ic.btn.pt[4], (5 * 6 / 2) + 3, "plus half the text's width and a gap: just RIGHT of the name")
	eq(ic.btn.parent, tf, "it lives on the name's own frame"); eq(ic.btn.w, 16, "16 pixels")
	name:SetText("Bob"); Stub.Advance(0.5)
	eq(ic.tex.path, ART .. "VoiceDeafened.tga", "Bob is deafened: headphones with a slash")
	eq(ic.btn.pt[4], (3 * 6 / 2) + 3, "and the icon follows the shorter name")
	name:SetText("Cy"); Stub.Advance(0.5)
	eq(ic.tex.path, ART .. "VoiceMuted.tga", "Cy is muted: a microphone with a slash")
	name:SetText("Wolf"); Stub.Advance(0.5)
	eq(ic.tex.shown, false, "someone without EqVoice has no icon")
	-- your own frame
	local pf = CreateFrame("Frame", "PlayerFrameHolder", UIParent)
	local mine = pf:CreateFontString("PlayerName", "OVERLAY", "GameFontNormalSmall")
	mine:SetText("Me")
	Stub.Advance(0.5)
	eq(I.surf[2].icon.state, "o", "your own name gets yours")
	-- nameplates: found under WorldFrame by their border picture
	alive()
	local pa = Stub.AddPlate("Alice")
	local pw = Stub.AddPlate("Wolf")
	Stub.Advance(0.5)
	check(I.plates[1] ~= nil and I.plates[1].frame == pa, "the plates were found"); eq(table.getn(I.plates), 2, "both of them")
	eq(I.plates[1].icon.state, "s", "Alice's plate has an icon"); eq(I.plates[1].icon.tex.pt[2], pa.nameText, "beside the name on the plate")
	eq(I.plates[2].icon, nil, "Wolf's plate has none (and none was even made)")
	pa.shown = false
	Stub.Advance(0.5)
	eq(I.plates[1].icon.state, nil, "a plate that is not showing has no icon")
	pa.shown = true
	pa.nameText:SetText("Bob")
	Stub.Advance(0.5)
	eq(I.plates[1].icon.state, "d", "a plate the game reuses for someone else follows the new name")
	local n = table.getn(I.plates)
	alive()
	eq(table.getn(I.plates), n, "plates are not added twice")
	local later = Stub.AddPlate("Cy")
	Stub.Advance(0.5)
	eq(table.getn(I.plates), n + 1, "a plate that appears later is found"); eq(I.plates[n + 1].icon.state, "m", "and gets its icon")
	-- switched off
	alive()
	SlashCmdList["EQVOICEUI"]("icons off")
	eq(I.surf[1].icon.tex.shown, false, "off: the target frame's icon is gone"); eq(I.plates[1].icon.tex.shown, false, "and the plates'")
	alive()
	eq(I.plates[1].icon.tex.shown, false, "and they stay gone")
	SlashCmdList["EQVOICEUI"]("icons on")
	eq(I.plates[1].icon.state, "d", "on: back")
	-- a surface that fails is switched off, and the rest keeps working
	alive()
	name.GetText = function() error("boom") end
	Stub.Advance(2)
	check(I.error ~= nil and string.find(I.error, "boom", 1, true), "the failure was noted: " .. tostring(I.error))
	eq(I.errors.units, 3, "three strikes and the unit frames are given up")
	alive()
	pa.nameText:SetText("Alice"); Stub.Advance(0.5)
	eq(I.plates[1].icon.state, "s", "but the plates keep working")
	check(OV.started and OV.Panel.error == nil, "and nothing else was disturbed")
	eq(Stub.fs["eqvoice-ctl-Me.txt"], nil, "(and no command was sent by any of this)")
end

sec("voice ui: the icon sits after the words wherever they are aligned, on the text the player sees, and a click opens the panel")
do
	local OV = login()
	local I = OV.Icons
	local PEERS = { peers = { "Alice 5.0 1 1 direct s 0", "Bob 9.0 1 0 direct o 0", "Cy 11.0 0 0 -", "Di 12.0 1 0 direct d 0" } }
	local seq = 1
	put(OV, seq, PEERS)
	local function adv() seq = seq + 1; put(OV, seq, PEERS) end        -- the app writes every 2 s: a fresh file also lets the icons look again
	-- the client's own text, in each alignment
	local tf = CreateFrame("Frame", "TargetFrame", UIParent)
	local name = tf:CreateFontString("TargetName", "OVERLAY", "GameFontNormalSmall")
	name:SetText("Alice")
	name:SetJustifyH("LEFT"); adv()
	local ic = I.surf[1].icon
	eq(ic.btn.pt[1], "LEFT", "left-aligned words: the icon's left edge ..."); eq(ic.btn.pt[2], name, "... is on the text"); eq(ic.btn.pt[3], "LEFT", "at its left")
	eq(ic.btn.pt[4], 5 * 6 + 3, "plus the width of the words and a gap")
	name:SetJustifyH("RIGHT"); adv()
	eq(ic.btn.pt[3], "RIGHT", "right-aligned words end at the box's right edge"); eq(ic.btn.pt[4], 3, "so the icon is a gap after the box")
	name:SetJustifyH("CENTER"); name:SetWidth(20); adv()
	eq(ic.btn.pt[3], "CENTER", "centred again"); eq(ic.btn.pt[4], 20 / 2 + 3, "a box narrower than its words cuts them off: the icon goes after what is shown, not after what is cut")
	name:SetWidth(nil); name.w = nil; adv()
	eq(ic.btn.pt[4], 5 * 6 / 2 + 3, "and with no width of its own it follows the words")

	-- which picture: the plain speaker is "using EqVoice, not talking"; dimmed it is "has EqVoice, nothing more"
	name:SetText("Bob"); adv()
	eq(ic.state, "o", "Bob: open, not talking"); eq(ic.tex.path, ART .. "VoiceKnown.tga", "the plain speaker (vol_low)"); eq(ic.tex.alpha, 1, "at full strength")
	name:SetText("Cy"); adv()
	eq(ic.state, "i", "Cy: only known to have EqVoice"); eq(ic.tex.path, ART .. "VoiceKnown.tga", "the same picture"); eq(ic.tex.alpha, 0.45, "dimmed, so the two can be told apart")
	name:SetText("Alice"); adv()
	eq(ic.tex.alpha, 1, "a picture that is not dim goes back to full strength")

	-- a text that is not on screen has no icon
	name.shown = false; adv()
	eq(ic.state, nil, "a hidden name has no icon"); eq(ic.btn.shown, false, "and its button is gone, so it cannot be clicked")
	name.shown = true; tf.shown = false; adv()
	eq(ic.state, nil, "a name on a hidden frame has none either")
	tf.shown = true; adv()
	eq(ic.state, "s", "both shown again: back")

	-- the click: the panel opens, and closes on the next click
	check(OV.Panel.frame == nil or not OV.Panel.frame.shown, "the panel starts closed")
	Stub.Click(ic.btn)
	check(OV.Panel.frame ~= nil and OV.Panel.frame.shown, "a click on the speaker opens the EqVoice panel")
	Stub.Click(ic.btn)
	check(not OV.Panel.frame.shown, "and the next click closes it")
	ic.btn.scripts.OnEnter()
	eq(Stub.tip.lines[1], "EqVoice", "the tooltip names it"); eq(Stub.tip.lines[2], "speaking", "says what that player is doing")
	eq(Stub.tip.lines[3], "Click: open the EqVoice settings.", "and what a click does"); eq(Stub.tip.shown, true, "shown")
	ic.btn.scripts.OnLeave()
	eq(Stub.tip.shown, false, "and put away")

	-- ECO's party frames draw their own, left-aligned names
	_G.EquadisClassicOverhaul = { modules = { partyframes = { frames = {} }, nameplates = { plates = {} } } }
	local pf = CreateFrame("Button", "EquadisClassicOverhaulParty1", UIParent)
	local textFrame = CreateFrame("Frame", nil, pf)
	local pn = textFrame:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
	pn:SetJustifyH("LEFT"); pn:SetText("Di")
	_G.EquadisClassicOverhaul.modules.partyframes.frames[1] = { nameText = pn }
	adv()
	local sp = I.surf[table.getn(I.UNIT_NAMES) + 1]
	check(sp ~= nil and sp.icon.btn ~= nil, "ECO's first party frame got a clickable icon")
	eq(sp.icon.state, "d", "Di is deafened"); eq(sp.icon.btn.pt[2], pn, "beside ECO's name"); eq(sp.icon.btn.pt[3], "LEFT", "after the words of a left-aligned name")
	eq(sp.icon.btn.pt[4], 2 * 6 + 3, "right at their end")
	eq(sp.icon.btn.parent, textFrame, "on the frame that draws the name")

	-- ECO's nameplates: the client's own name text is hidden, ECO draws another; the icon follows the one you can see
	local pa = Stub.AddPlate("Wolf")                    -- the client's text says someone without EqVoice (it is hidden in the game)
	local mine = pa:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
	mine:SetText("Alice")
	_G.EquadisClassicOverhaul.modules.nameplates.plates[pa] = { name = mine, overlay = pa }
	adv()
	check(I.plates[1] ~= nil and I.plates[1].frame == pa, "the plate was found")
	eq(I.plates[1].icon.state, "s", "the icon is for the name ECO draws (Alice), not the hidden one (Wolf)")
	eq(I.plates[1].icon.tex.pt[2], mine, "beside ECO's text")
	eq(I.plates[1].icon.btn, nil, "a nameplate's icon is only drawn on: a click there still targets the player")
	mine:SetText("Bob"); adv()
	eq(I.plates[1].icon.state, "o", "a plate ECO reuses for someone else follows ECO's text")
	_G.EquadisClassicOverhaul = nil; adv()
	eq(I.plates[1].icon.state, nil, "without ECO the client's own text (Wolf, no EqVoice) is used again")
end

sec("voice ui: the microphone setup overlay")
do
	local OV = login()
	local P = OV.Panel
	local BASE = "level=ok mode=open mute=0 deaf=0 tx=1 talk=0 vol=100 near=0 conn=0 key=F9 mic=ok boost=normal gate=auto gdb=-48 ing=100"
	put(OV, 1, { fields = BASE .. " cal=idle calt=0 calq=- cald=0" })
	Stub.Click(EqVoiceIndicator, "LeftButton")
	local f = EqVoicePanel
	check(EqVoiceSetup == nil, "no overlay until the setup runs")
	check(string.find(f.hint.text, "Set up mic once", 1, true), "a player who has never done it is nudged: " .. f.hint.text)
	-- the button starts it, and the first step is on screen at once (before the app has answered)
	Stub.Click(f.bSetup)
	eq(lastSent(OV), "calibrate 1", "the button asks the app to start")
	Stub.Advance(0.25)
	local o = EqVoiceSetup
	check(o ~= nil and o.shown, "the overlay is up"); eq(o.step.text, "Step 1 of 3", "step one ..."); eq(o.title.text, "Stay silent", "... asks for silence")
	check(string.find(o.text.text, "Do not talk", 1, true), "and says why: " .. o.text.text)
	eq(o.bCancel.shown, true, "it can be cancelled"); eq(o.bDone.shown, false, "and there is nothing to dismiss yet")
	-- the app reports its steps with the seconds left
	put(OV, 2, { fields = BASE .. " cal=quiet calt=5 calq=- cald=0" })
	eq(o.left.text, "5 s", "five seconds to go")
	put(OV, 3, { fields = BASE .. " cal=normal calt=7 calq=- cald=0" })
	eq(o.step.text, "Step 2 of 3", "step two"); eq(o.title.text, "Talk normally", "talk"); eq(o.left.text, "7 s", "seven seconds")
	put(OV, 4, { fields = BASE .. " cal=loud calt=3 calq=- cald=0" })
	eq(o.step.text, "Step 3 of 3", "step three"); eq(o.title.text, "Now shout", "shout"); eq(o.left.text, "3 s", "")
	-- it went well
	put(OV, 5, { fields = "level=ok mode=open mute=0 deaf=0 tx=1 talk=0 vol=100 near=0 conn=0 key=F9 mic=ok boost=normal gate=manual gdb=-58 ing=85 cal=done calt=0 calq=good cald=1" })
	eq(o.title.text, "Your microphone is set up", "the result"); check(string.find(o.text.text, "noise gate is at -58 dB", 1, true), "with the bar: " .. o.text.text)
	check(string.find(o.text.text, "Microphone volume: 85%", 1, true), "and the volume when it was changed: " .. o.text.text)
	eq(o.left.text, "", "no countdown"); eq(o.bDone.shown, true, "Done is offered"); eq(o.bCancel.shown, false, "Cancel is not")
	Stub.Advance(6)
	put(OV, 6, { fields = "level=ok mode=open mute=0 deaf=0 tx=1 talk=0 vol=100 near=0 conn=0 key=F9 mic=ok boost=normal gate=manual gdb=-58 ing=85 cal=done calt=0 calq=good cald=1" })
	eq(o.shown, true, "the result stays until you say Done")
	Stub.Click(o.bDone)
	eq(lastSent(OV), "calibrate 0", "Done tells the app to let go"); Stub.Advance(0.25)
	eq(o.shown, false, "and the overlay is gone at once")
	put(OV, 7, { fields = "level=ok mode=open mute=0 deaf=0 tx=1 talk=0 vol=100 near=0 conn=0 key=F9 mic=ok boost=normal gate=manual gdb=-58 ing=85 cal=idle calt=0 calq=- cald=1" })
	eq(o.shown, false, "it stays gone")
	check(not string.find(f.hint.text, "Tip:", 1, true), "and the nudge is gone for a player who has done it")

	-- started from the desktop window: the overlay shows in the game too, and a run where nobody talked says so
	put(OV, 8, { fields = BASE .. " cal=quiet calt=4 calq=- cald=0" })
	eq(o.shown, true, "a setup started in the window shows here"); eq(o.title.text, "Stay silent", "")
	put(OV, 9, { fields = BASE .. " cal=idle calt=0 calq=none cald=0" })
	eq(o.shown, true, "a setup that ended with no result stays up"); eq(o.title.text, "Nothing was changed", "and says so")
	check(string.find(o.text.text, "did not hear you talk", 1, true), "with the reason: " .. o.text.text)
	Stub.Click(o.bDone); Stub.Advance(0.25)
	eq(o.shown, false, "dismissed")
	-- a noisy room
	put(OV, 10, { fields = BASE .. " cal=quiet calt=4 calq=- cald=0" })
	put(OV, 11, { fields = BASE .. " cal=done calt=0 calq=noisy cald=1" })
	eq(o.title.text, "Your room is noisy", "a result of 'noisy'"); check(string.find(o.text.text, "stays on automatic", 1, true), "explains: " .. o.text.text)
	Stub.Click(o.bDone); Stub.Advance(0.25)
	-- cancel while it runs
	put(OV, 12, { fields = BASE .. " cal=normal calt=4 calq=- cald=0" })
	Stub.Click(o.bCancel)
	eq(lastSent(OV), "calibrate 0", "Cancel sends the same: let go"); Stub.Advance(0.25)
	eq(o.shown, false, "and the overlay goes")
	put(OV, 13, { fields = BASE .. " cal=idle calt=0 calq=- cald=0" })
	eq(o.shown, false, "a cancelled run leaves no result behind")
	-- a stale result from an earlier session is not shown by a fresh panel
	put(OV, 14, { fields = BASE .. " cal=idle calt=0 calq=none cald=0" })
	eq(o.shown, false, "an old 'none' with no setup seen running is not news")
	eq(P.error, nil, "and no drawing error")
end

sec("voice ui: the blocklist window")
do
	local OV = login()
	local P = OV.Panel
	put(OV, 1, { peers = { "Alice 12.5 1 1 direct s 0" }, muted = { "zed", "bob", "carol" } })
	Stub.Click(EqVoiceIndicator, "LeftButton")
	local f = EqVoicePanel
	eq(f.bBlock.text, "Blocklist (3)", "the panel's button counts them")
	check(EqVoiceBlocklist == nil, "the window is built on first use")
	Stub.Click(f.bBlock)
	local b = EqVoiceBlocklist
	check(b ~= nil and b.shown, "the button opens it")
	eq(b.rows[1].name.text, "Bob", "sorted, with a capital"); eq(b.rows[2].name.text, "Carol", ""); eq(b.rows[3].name.text, "Zed", "")
	eq(b.rows[4].name.text, "", "and no more than there are"); eq(b.rows[4].btn.shown, false, "an empty row has no button"); eq(b.rows[1].btn.shown, true, "a full one has")
	eq(b.empty.text, "", "not empty"); eq(b.page.text, "3 players   page 1 of 1", "the count")
	local special = false
	for i = 1, table.getn(UISpecialFrames) do if UISpecialFrames[i] == "EqVoiceBlocklist" then special = true end end
	check(special, "Escape closes it")
	-- unblock
	Stub.Click(b.rows[2].btn)
	eq(lastSent(OV), "pmute carol 0", "the button beside a name unblocks that player"); Stub.Advance(0.25)
	eq(b.rows[2].name.text, "Zed", "and the list shows it at once"); eq(b.page.text, "2 players   page 1 of 1", "")
	-- block by typing
	b.input:SetText("  Dgawdawdad ")
	Stub.Click(b.bAdd)
	eq(lastSent(OV), "pmute dgawdawdad 1", "a typed name is blocked, spaces trimmed, in lower case")
	eq(b.input:GetText(), "", "and the box is emptied"); Stub.Advance(0.25)
	eq(b.rows[1].name.text, "Bob", ""); eq(b.rows[2].name.text, "Dgawdawdad", "the new name is in the list at once")
	b.input:SetText("")
	local n0 = table.getn(sent(OV))
	Stub.Click(b.bAdd)
	eq(table.getn(sent(OV)), n0, "an empty box blocks nobody")
	b.input.scripts.OnEnterPressed()
	eq(table.getn(sent(OV)), n0, "(and Enter does the same as the button)")
	-- block the target
	Stub.units = { target = { name = "Wolf", player = false } }
	Stub.Click(b.bTarget)
	check(chatHas("target is not a player"), "a target that is not a player is refused")
	Stub.units = { target = { name = "Mira", player = true } }
	Stub.Click(b.bTarget)
	eq(lastSent(OV), "pmute mira 1", "a player target is blocked")
	-- pages of ten
	Stub.Advance(4)
	local many = {}
	for i = 1, 23 do many[i] = string.format("player%02d", i) end
	put(OV, 2, { peers = {}, muted = many })
	eq(b.page.text, "23 players   page 1 of 3", "twenty-three players make three pages"); eq(b.rows[10].name.text, "Player10", "ten on the first")
	eq(b.prev.enabled, false, "no page before the first"); eq(b.next.enabled, true, "")
	Stub.Click(b.next); Stub.Advance(0.25)
	eq(b.page.text, "23 players   page 2 of 3", "the next page"); eq(b.rows[1].name.text, "Player11", "starts where the first stopped")
	Stub.Click(b.next); Stub.Advance(0.25)
	eq(b.rows[3].name.text, "Player23", "the last page has three"); eq(b.rows[4].btn.shown, false, "and no more"); eq(b.next.enabled, false, "no page after the last")
	Stub.Click(b.next); Stub.Advance(0.25)
	eq(b.page.text, "23 players   page 3 of 3", "pressing past the end stays on the last")
	put(OV, 3, { peers = {}, muted = { "player01" } })
	eq(b.page.text, "1 player   page 1 of 1", "when the list shrinks the page comes back with it")
	put(OV, 4, { peers = {}, muted = {} })
	eq(b.empty.text, "Nobody is blocked.", "an empty list says so")
	-- the slash command and the close button
	SlashCmdList["EQVOICEUI"]("blocklist")
	eq(b.shown, false, "/eqvc blocklist closes it when it is open")
	SlashCmdList["EQVOICEUI"]("blocklist")
	eq(b.shown, true, "and opens it when it is not")
	-- without the app the add buttons are off
	Stub.Advance(8)
	eq(b.bAdd.enabled, false, "no app: nothing can be added"); eq(b.bTarget.enabled, false, "")
	eq(P.error, nil, "no drawing error")
end

sec("voice ui: a client that will not make a slider")
do
	local OV = login({ noSlider = true })
	local P = OV.Panel
	put(OV, 1)
	Stub.Click(EqVoiceIndicator, "LeftButton")
	local f = EqVoicePanel
	check(f ~= nil and f:IsShown(), "the panel still opens")
	eq(f.sVol.frame, nil, "there is no slider"); check(f.sVol.down ~= nil and f.sVol.up ~= nil, "but a minus and a plus instead")
	eq(f.sVol.down.text, "-", ""); eq(f.sVol.up.text, "+", "")
	Stub.Click(f.sVol.down); Stub.Advance(0.5)
	eq(lastSent(OV), "volume 90", "minus lowers the volume a tenth of its range: 100 -> 90"); eq(f.volText.text, "90%", "and the number shows it")
	Stub.Click(f.sIn.up); Stub.Advance(0.5)
	eq(lastSent(OV), "input 120", "plus raises the microphone volume by a tenth of 200: 100 -> 120")
	Stub.Click(f.sBoost.up); Stub.Advance(0.5)
	check(P.error ~= nil and string.find(P.error, "Slider", 1, true), "the failure was noted so that it can be found: " .. tostring(P.error))
	Stub.Advance(8)
	eq(f.sVol.up.enabled, false, "without the app the buttons are off too")
end

sec("voice ui: idle, speaking and loud are vol_low, vol_mid and vol_high, and your own icon says what needs a look")
do
	local OV = login()
	local I, P = OV.Icons, OV.Panel
	local seq = 0
	local function app(fields, peers)
		seq = seq + 1
		put(OV, seq, { fields = fields, peers = peers })
	end
	local BASE = "level=ok mode=open mute=0 deaf=0 tx=1 talk=0 vol=100 near=1 conn=1 key=F9 game=1 hub=1 mic=ok"
	-- another player: the three picture files and the letters that choose them
	app(BASE, { "Ann 5.0 1 0 direct o 0", "Bea 6.0 1 1 direct s 0", "Cat 7.0 1 1 direct l 0", "Dee 8.0 0 0 -" })
	eq(I.Resolve("Ann", Stub.now), "o", "connected and silent: idle"); eq(I.Resolve("Bea", Stub.now), "s", "talking normally"); eq(I.Resolve("Cat", Stub.now), "l", "talking loudly")
	eq(I.LOOK.o.file, "VoiceKnown.tga", "idle is the plain speaker (vol_low)"); eq(I.LOOK.s.file, "VoiceOpen.tga", "speaking is the speaker with one wave (vol_mid)")
	eq(I.LOOK.l.file, "VoiceSpeaking.tga", "loud is the speaker with waves (vol_high)")
	eq(I.Describe("l"), "speaking loudly", "and the tooltip says so"); eq(I.Describe("s"), "speaking", "")
	-- the unit frame: a name that goes quiet, speaks, shouts and goes quiet again shows each in turn
	local tf = CreateFrame("Frame", "TargetFrame", UIParent)
	local name = tf:CreateFontString("TargetName", "OVERLAY", "GameFontNormalSmall")
	local function show(letter, who)
		name:SetText(who)
		app(BASE, { who .. " 5.0 1 1 direct " .. letter .. " 0" })
		return I.surf[1].icon
	end
	local ic = show("o", "Ann"); eq(ic.tex.path, ART .. "VoiceKnown.tga", "Ann is idle: vol_low"); eq(ic.tex.alpha, 1, "at full strength")
	ic = show("s", "Ann"); eq(ic.tex.path, ART .. "VoiceOpen.tga", "Ann speaks: vol_mid")
	ic = show("l", "Ann"); eq(ic.tex.path, ART .. "VoiceSpeaking.tga", "Ann shouts: vol_high")
	ic = show("o", "Ann"); eq(ic.tex.path, ART .. "VoiceKnown.tga", "and is idle again")
	-- you blocked her: whatever she does, the icon says you are not hearing it
	app(BASE, { "Ann 5.0 1 1 direct l 0" })
	P.SetPlayerMute("Ann", true)
	eq(I.Resolve("Ann", Stub.now), "b", "blocked by you"); eq(I.LOOK.b.file, "VoiceMuted.tga", "the slashed microphone")
	P.SetPlayerMute("Ann", false)
	eq(I.Resolve("Ann", Stub.now), "l", "and unblocked she is as loud as she was")

	-- your own icon, in the order of what matters
	local function mine(fields) app(fields); return I.Resolve("Me", Stub.now) end
	eq(mine(BASE), "o", "ready and quiet")
	eq(mine(BASE .. " vl=1"), "s", "you are speaking"); eq(mine(BASE .. " vl=2"), "l", "you are speaking loudly")
	eq(mine(BASE .. " vl=1 mute=1"), "m", "muted wins over speaking"); eq(mine(BASE .. " vl=1 mute=1 deaf=1"), "d", "and deafened over muted")
	eq(mine("level=ok mode=ptt mute=0 deaf=0 tx=0 talk=0 vol=100 near=0 conn=0 key=F9 game=1 hub=1 mic=ok"), "m", "push to talk with the key up: closed")
	eq(mine(string.gsub(BASE, "mic=ok", "mic=denied")), "e", "Windows says no to the microphone")
	eq(mine(string.gsub(BASE, "mic=ok", "mic=no_device")), "e", "or there is none")
	eq(mine(string.gsub(BASE, "mic=ok", "mic=starting")), "o", "a microphone that is still starting is not a problem")
	eq(mine(string.gsub(BASE, "mic=ok", "mic=no_page")), "o", "nor is an engine that is about to start (the app writes its \"no page\" with an underscore)")
	eq(mine(string.gsub(BASE, "mic=ok", "mic=denied") .. " mute=1"), "e", "a microphone problem is shown even while muted: it needs a look")
	-- the voice server cannot be reached: only after a few seconds (a login takes a moment), and gone again the moment it is back
	local down = string.gsub(BASE, "hub=1", "hub=0")
	eq(mine(down), "o", "the first second without the voice server: not yet a warning")
	for i = 1, 4 do app(down) end
	eq(I.Resolve("Me", Stub.now), "o", "five seconds: still not")
	for i = 1, 6 do app(down) end
	eq(I.Resolve("Me", Stub.now), "x", "ten seconds: not connected")
	eq(I.LOOK.x.tint[1] > 0.9 and I.LOOK.x.tint[2] < 0.4, true, "in red")
	eq(mine(BASE), "o", "back at once when the server is back")
	-- the app stops: not connected, at once
	Stub.Advance(8)
	eq(I.Resolve("Me", Stub.now), "x", "no app: not connected")
	-- and the red is really drawn on the unit frame
	local pf = CreateFrame("Frame", "PlayerFrameHolder", UIParent)
	local mineName = pf:CreateFontString("PlayerName", "OVERLAY", "GameFontNormalSmall")
	mineName:SetText("Me")
	Stub.Advance(0.5)
	local own = I.surf[2].icon
	eq(own.state, "x", "your own name has the icon"); check(own.tex.r > 0.9 and own.tex.g < 0.4 and own.tex.b < 0.4, "drawn in red")
	app(BASE .. " vl=2"); Stub.Advance(0.5)
	eq(own.state, "l", "and white again, loud"); eq(own.tex.r, 1, "no tint left behind"); eq(own.tex.g, 1, "")
	Stub.Click(own.btn)
	check(EqVoicePanel ~= nil and EqVoicePanel.shown, "clicking your own icon opens the same panel as /eqvc")
	eq(P.error, nil, "no drawing error")
end

sec("voice ui: the first-time setup box")
do
	local DEV = { inputs = { "Default - Microphone A", "Headset Mic (USB)" }, outputs = { "Default - Speakers", "Monitor (HDMI)" } }
	local BASE = "level=ok mode=open mute=0 deaf=0 tx=1 talk=0 vol=100 near=0 conn=0 key=F9 game=1 hub=1 mic=ok din=0 dout=0 lvl=0 ing=100"
	local OV = login()
	local P, W = OV.Panel, OV.Wizard
	local seq = 0
	local function app(fields, devices)
		seq = seq + 1
		put(OV, seq, { fields = fields or BASE, devices = devices or DEV })
	end
	-- it waits for the app, and then for a moment, and appears once by itself
	Stub.Advance(10)
	check(EqVoiceWizard == nil, "no app: no box")
	app()
	check(EqVoiceWizard == nil or not EqVoiceWizard.shown, "the app has only just appeared: not yet")
	for i = 1, 8 do app() end
	local f = EqVoiceWizard
	check(f ~= nil and f.shown, "a few seconds later the box opens by itself")
	eq(f.title.text, "Welcome to EqVoice", "welcome"); eq(f.step.text, "Step 1 of 6", ""); eq(f.bBack.shown, false, "no Back on the first page"); eq(f.bNext.text, "Next", "")
	check(string.find(f.pages[1][1].text, "proximity voice chat", 1, true), "it says what EqVoice is")
	-- the microphone page
	Stub.Click(f.bNext)
	eq(f.title.text, "Your microphone", "the second page"); eq(f.bBack.shown, true, "")
	app(BASE .. " lvl=0")
	local rows = f.pages[2].list.rows
	eq(rows[1].text.text, "System default", "the first choice is the system's own"); eq(rows[2].text.text, "Default - Microphone A", "then what Windows has")
	eq(rows[3].text.text, "Headset Mic (USB)", ""); eq(rows[4].shown, false, "and no more rows than there are devices")
	eq(rows[1].mark.shown, true, "the one in use is ticked"); eq(rows[3].mark.shown, false, "")
	Stub.Click(rows[3])
	eq(lastSent(OV), "micdev Headset Mic (USB)", "a click picks that microphone, by the name shown")
	Stub.Advance(0.25)
	eq(rows[3].mark.shown, true, "the tick moves at once"); eq(rows[1].mark.shown, false, "")
	Stub.Click(rows[1])
	eq(lastSent(OV), "micdev default", "'System default' is sent as 'default'")
	-- the meter: it asks for the level while it is showing, and shows what comes back
	local watching = false
	for _, c in ipairs(sent_all(OV)) do if c.cmd == "watch" and c.arg == "1" then watching = true end end
	check(watching, "the box asks the app for the microphone's level")
	app(BASE .. " lvl=40")
	eq(f.pages[2].meter.pct, 40, "the bar follows it")
	app(BASE .. " lvl=95")
	eq(f.pages[2].meter.pct, 95, "")
	app((string.gsub(BASE, "mic=ok", "mic=denied")))
	check(string.find(f.pages[2].problem.text, "cannot be used", 1, true), "a microphone Windows refuses is explained in words: " .. f.pages[2].problem.text)
	app()
	eq(f.pages[2].problem.text, "", "and the words go when it works")
	-- the speakers page
	Stub.Click(f.bNext)
	eq(f.title.text, "Your speakers", "")
	app()
	local orows = f.pages[3].list.rows
	eq(orows[3].text.text, "Monitor (HDMI)", "the speakers")
	Stub.Click(orows[3]); eq(lastSent(OV), "spkdev Monitor (HDMI)", "a click picks them")
	Stub.Click(f.pages[3].test); eq(lastSent(OV), "test 1", "the test sound")
	-- push to talk
	Stub.Click(f.bNext)
	eq(f.title.text, "Push to talk", ""); app()
	eq(f.pages[4].keytext.text, "Push-to-talk key: F9", "it shows the key that is bound now")
	Stub.Click(f.pages[4].ptt); eq(lastSent(OV), "mode ptt", "push to talk")
	Stub.Click(f.pages[4].open); eq(lastSent(OV), "mode open", "open microphone")
	Stub.Click(f.pages[4].key); Stub.Advance(0.25)
	check(P.capturing, "the key button listens for the next key"); check(string.find(f.pages[4].keytext.text, "Press the key", 1, true), f.pages[4].keytext.text)
	_G.arg1 = "F10"; EqVoiceKeyCapture.scripts.OnKeyDown()
	eq(lastSent(OV), "key F10", "any key can be bound, not only F9")
	-- options
	Stub.Click(f.bNext)
	eq(f.title.text, "Voice options", ""); app()
	Stub.Drag(f.pages[5].sIn.frame, 150); Stub.Advance(0.5); eq(lastSent(OV), "input 150", "the microphone's volume")
	Stub.Drag(f.pages[5].sOut.frame, 60); Stub.Advance(0.5); eq(lastSent(OV), "volume 60", "everyone else's")
	Stub.Click(f.pages[5].mute); eq(lastSent(OV), "mute 1", "mute"); Stub.Click(f.pages[5].deaf); eq(lastSent(OV), "deafen 1", "deafen")
	Stub.Click(f.pages[5].setup); eq(lastSent(OV), "calibrate 1", "the microphone setup is one click away")
	Stub.Click(EqVoiceSetup and EqVoiceSetup.bCancel or f.bNext)
	-- the check
	app()
	while f.title.text ~= "All set?" do Stub.Click(f.bNext) end
	eq(f.bNext.text, "Finish", "the last page ends with Finish")
	app()
	local rowsC = f.pages[6].rows
	eq(rowsC[1].text.text, "Microphone detected", ""); eq(rowsC[2].text.text, "Output detected", ""); eq(rowsC[3].text.text, "EqVoice connected", "")
	eq(rowsC[1].tex.path, "Interface\\Buttons\\UI-CheckBox-Check", "a tick"); eq(rowsC[1].tex.r, 1, "in full colour")
	eq(f.pages[6].summary.text, "Setup complete.", "everything is ready")
	app((string.gsub(BASE, "hub=1", "hub=0")))
	check(rowsC[3].tex.r < 0.6, "a check that is not yet true is grey, not a cross")
	check(string.find(f.pages[6].summary.text, "keeps trying", 1, true), "and says EqVoice keeps trying")
	for i = 1, 11 do app((string.gsub(BASE, "hub=1", "hub=0"))) end
	eq(rowsC[3].tex.path, "Interface\\Buttons\\UI-GroupLoot-Pass-Up", "after ten seconds it is a cross")
	Stub.Click(f.bNext)
	eq(f.shown, false, "Finish closes it"); eq(OV.db.ui.setupDone, true, "and it does not come back")
	Stub.Advance(30); app()
	eq(f.shown, false, "not by itself")
	-- /eqvc welcome opens it again, from the first page
	SlashCmdList["EQVOICEUI"]("welcome")
	check(f.shown and f.title.text == "Welcome to EqVoice", "/eqvc welcome opens the same box again")
	f:Hide()
	eq(OV.db.ui.setupDone, true, "closing it any way is the same as finishing")
	eq(P.error, nil, "no panel drawing error"); eq(W.error, nil, "no box drawing error")
end

sec("voice ui: the box does not come back, and skipping counts")
do
	local OV = login()
	local seq = 0
	local function app() seq = seq + 1; put(OV, seq, { fields = "level=ok mode=open mute=0 deaf=0 tx=1 talk=0 vol=100 near=0 conn=0 key=F9 game=1 hub=1 mic=ok" }) end
	for i = 1, 12 do app() end
	local f = EqVoiceWizard
	check(f and f.shown, "it opened")
	Stub.Click(f.bSkip)
	eq(f.shown, false, "Skip closes it"); eq(OV.db.ui.setupDone, true, "for good")
	-- a new login with the saved setting: it never opens
	local db = OV.db
	local OV2 = Stub.Login({ db = db, fileApi = "WriteCustomFile", classic = true })
	seq = 0
	for i = 1, 14 do seq = seq + 1; put(OV2, seq, { fields = "level=ok mode=open mute=0 deaf=0 tx=1 talk=0 vol=100 near=0 conn=0 key=F9 game=1 hub=1 mic=ok" }) end
	check(EqVoiceWizard == nil or not EqVoiceWizard.shown, "a later session does not open it")
end

sec("voice ui: the game's settings are handed back to the app when it appears")
do
	local DEV = { inputs = { "Default - Microphone A", "Headset Mic (USB)" }, outputs = { "Default - Speakers", "Monitor (HDMI)" } }
	local BASE = "level=ok mode=open mute=0 deaf=0 tx=1 talk=0 vol=100 near=0 conn=0 key=F9 game=1 hub=1 mic=ok din=0 dout=0 ing=100 boost=normal gate=auto gdb=-48"
	local db = { ui = { setupDone = true } }
	local OV = Stub.Login({ db = db, fileApi = "WriteCustomFile", classic = true })
	local P = OV.Panel
	local seq = 0
	local function app(fields) seq = seq + 1; put(OV, seq, { fields = fields or BASE, devices = DEV }) end
	app()
	-- what the player chooses is remembered (and mute and deafen are not)
	P.SetMode("ptt"); P.SetVolume(70); P.SetInput(130); P.SetBoost("high"); P.SetGate("manual"); P.SetGateDb(-52); P.SetMute(true); P.SetDeaf(true)
	P.SetDevice("in", "Headset Mic (USB)", 2); P.SetDevice("out", "Monitor (HDMI)", 2); P.Command("key", "BUTTON4")
	local s = OV.db.settings
	eq(s.mode, "ptt", "the mode"); eq(s.volume, "70", "the volume"); eq(s.input, "130", "the microphone volume"); eq(s.boost, "high", "")
	eq(s.gate, "manual", ""); eq(s.gatedb, "-52", ""); eq(s.micdev, "Headset Mic (USB)", "the microphone"); eq(s.spkdev, "Monitor (HDMI)", "the speakers"); eq(s.key, "BUTTON4", "the key")
	eq(s.mute, nil, "mute is not remembered"); eq(s.deafen, nil, "nor is deafen")
	-- the app goes away and comes back with its own defaults (a new install, a deleted config): the game's choices are handed back, one a tick
	Stub.Advance(8)
	check(not OV.Status.Alive(Stub.now), "the app is gone")
	local got, realSend = {}, OV.Control.Send
	OV.Control.Send = function(cmd, arg) got[cmd] = arg; return realSend(cmd, arg) end        -- (the file keeps only the newest eight: record every one)
	seq = seq + 1
	put(OV, seq, { fields = BASE, devices = DEV })
	Stub.Advance(4)
	eq(got.mode, "ptt", "the mode is handed back"); eq(got.volume, "70", ""); eq(got.input, "130", ""); eq(got.boost, "high", ""); eq(got.gate, "manual", "")
	eq(got.gatedb, "-52", ""); eq(got.micdev, "Headset Mic (USB)", ""); eq(got.spkdev, "Monitor (HDMI)", ""); eq(got.key, "BUTTON4", "")
	eq(got.mute, nil, "mute is not"); eq(got.deafen, nil, "")
	-- one at a time: the command file keeps only the newest eight
	local n = 0
	for _ in pairs(got) do n = n + 1 end
	eq(n, 9, "all nine arrived, none lost behind another")
	-- what the app already has is not sent again
	local OK = BASE .. " "
	local fields2 = "level=ok mode=ptt mute=0 deaf=0 tx=0 talk=0 vol=70 near=0 conn=0 key=Mouse_4 game=1 hub=1 mic=ok din=2 dout=2 ing=130 boost=high gate=manual gdb=-52"
	Stub.Advance(8)
	got = {}
	seq = seq + 1
	put(OV, seq, { fields = fields2, devices = DEV })
	Stub.Advance(4)
	local any = false
	for k, v in pairs(got) do if k ~= "watch" then any = true end end
	eq(any, false, "an app that already has them all gets nothing")
end

sec("voice ui: the panel picks the microphone and the speakers")
do
	local DEV = { inputs = { "Default - Microphone A", "Headset Mic (USB)" }, outputs = { "Default - Speakers", "Monitor (HDMI)" } }
	local OV = login()
	local P = OV.Panel
	put(OV, 1, { fields = "level=ok mode=open mute=0 deaf=0 tx=1 talk=0 vol=100 near=0 conn=0 key=F9 game=1 hub=1 mic=ok din=2 dout=0 lvl=35", devices = DEV })
	Stub.Click(EqVoiceIndicator, "LeftButton")
	local f = EqVoicePanel
	eq(f.bMicDev.text, "Headset Mic (USB)", "the panel shows the microphone in use"); eq(f.bSpkDev.text, "System default", "and the speakers")
	Stub.Advance(0.5)
	eq(f.meter.pct, 35, "the level meter beside it")
	local asked = false
	for _, c in ipairs(sent_all(OV)) do if c.cmd == "watch" and c.arg == "1" then asked = true end end
	check(asked, "an open panel asks for the level")
	Stub.Click(f.bMicDev)
	local pk = EqVoiceDevicePicker
	check(pk and pk.shown, "the button opens the list"); eq(pk.title.text, "Choose your microphone", "")
	local rows = pk.lists["in"].rows
	eq(rows[3].mark.shown, true, "the one in use is ticked"); eq(rows[1].mark.shown, false, "")
	Stub.Click(rows[2])
	eq(lastSent(OV), "micdev Default - Microphone A", "a click chooses it"); eq(pk.shown, false, "and closes the list")
	Stub.Click(f.bSpkDev)
	eq(pk.title.text, "Choose your speakers", "the speakers' list is the other one"); eq(pk.lists.out.rows[1].mark.shown, true, "")
	Stub.Click(f.bSpkDev); eq(pk.shown, false, "the same button closes it again")
	f:Hide(); Stub.Advance(2)
	local n = table.getn(sent_all(OV))
	check(sent_all(OV)[n].cmd == "watch" and sent_all(OV)[n].arg == "0", "closing the panel tells the app to stop sending the level")
	eq(P.error, nil, "no drawing error")
end

-- ===================================================================== the heartbeat says muted / deafened
sec("voice ui: the heartbeat carries mute and deafen")
do
	local OV = login({ inGuild = true, db = { transport = "AUTO" } })
	local C = OV.Comm
	local P = OV.Proto
	local function lastFlags()
		for i = table.getn(Stub.sent), 1, -1 do
			local m = P.Decode(Stub.sent[i].text)
			if m and m.t == "H" then return m.flags end
		end
		return nil
	end
	Stub.Advance(10)
	eq(lastFlags(), 1, "no app: just 'enabled', as an older addon sends")
	eq(C.Flags(Stub.now), 1, "")
	put(OV, 1)
	eq(C.Flags(Stub.now), 9, "the app is running: enabled + app")
	Stub.Advance(2)
	eq(lastFlags(), 9, "and the next heartbeat says so")
	put(OV, 2, { fields = "level=ok mode=open mute=1 deaf=0 tx=0 talk=0 vol=100 near=0 conn=0 key=F9 mic=ok" })
	eq(C.Flags(Stub.now), 11, "muted: enabled + muted + app")
	put(OV, 3, { fields = "level=ok mode=open mute=1 deaf=1 tx=0 talk=0 vol=100 near=0 conn=0 key=F9 mic=ok" })
	eq(C.Flags(Stub.now), 15, "deafened and muted: enabled + muted + deafened + app")
	-- a change is announced soon, not after a whole interval
	put(OV, 4)
	Stub.Advance(2)
	put(OV, 5)
	local n = table.getn(Stub.sent)
	Stub.Click(EqVoiceIndicator, "RightButton")             -- mute
	Stub.Advance(2)
	check(table.getn(Stub.sent) > n, "muting sends a heartbeat within two seconds")
	eq(lastFlags(), 11, "carrying the muted flag")
	-- an older receiver still decodes it
	local m = P.Decode("H:AAAAAAAA:1:4:B")
	check(m ~= nil and m.flags == 11, "the decoder takes the new bits")
end

end
