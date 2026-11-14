-- EqVoice / Panel.lua
-- The in-game face of EqVoice. /eqvc and /eqvoice open a panel: is voice working, the microphone mode (open mic or push to
-- talk) and its key, mute, deafen, how loud your microphone is, how loud everyone is, how much louder people are right next
-- to you (boost), the noise gate, the microphone setup, a speaker test, who is near (click a name to mute that player) and the
-- full list of players you have blocked. A small indicator button shows at a glance whether you are live, muted or off: click
-- it for the panel, right-click it to mute, drag it anywhere. The little speaker beside a name on the unit frames (Icons.lua)
-- opens the panel too.
--
-- The addon draws and asks; the desktop app does. Every click becomes a command in eqvoice-ctl*.txt (Control.lua), and
-- what the panel shows comes back from eqvoice-st*.txt (Status.lua), so what you see is what the app really did. For a
-- moment after a click the panel shows the wanted value at once (a "pending" override) so it feels instant, then the app's
-- own answer takes over. A slider sends its value once it has rested for a moment, not at every pixel of a drag.
--
-- Written for the 1.12 client: Lua 5.0, `this` and `arg1` inside scripts, no ScrollFrame (it hid a whole settings window
-- on this client once), no SetShown/HookScript/SetColorTexture. The look is the addon's own, not the client's templates:
-- opaque charcoal windows, flat buttons, one purple accent (THEME), cards for the groups (header, Microphone, Speaker, Nearby).
-- The sliders are plain Slider frames with a thin track and a purple fill; if the client refuses one, that row gets a minus
-- and a plus button instead. The sensitivity bar (NewGateBar) is ONE bar: the live microphone level, drawn from the signal the
-- noise gate itself judges (status fields gl, gt, go), with the gate's bar as a handle on it; there is no second meter or slider.
-- Building the frames is wrapped in pcall: a drawing mistake must never stop the voice pipeline.
--
-- Model() is pure (no frames): it turns the status file into the words and colours the panel shows, and is what the
-- tests check. The drawing code only copies the model onto widgets.

local OV = EqVoice
local Panel = {}
OV.Panel = Panel

Panel.ROWS      = 5          -- nearby players listed
Panel.PENDING_S = 3          -- seconds a clicked value is shown before the app's answer is required
Panel.WIDTH     = 480
Panel.HEIGHT    = 642
Panel.GATE_LABEL  = { off = "Off", auto = "Auto", manual = "Manual" }                -- the noise gate (shown in the order Manual, Auto, Off)
Panel.GATE_NEXT   = { auto = "manual", manual = "off", off = "auto" }
Panel.GATE_MIN, Panel.GATE_MAX, Panel.GATE_STEP = -80, -20, 2                         -- manual bar, dB
Panel.SLIDER_SEND_S = 0.2    -- a slider that has rested this long sends its value
Panel.DISCONNECT_S = 8       -- seconds without the voice server before your own icon says so (a login takes a moment)
Panel.BL_ROWS   = 10         -- blocked players shown on a page of the blocklist
Panel.DEVICE_ROWS = 8        -- microphones (or speakers) listed in a picker, besides "system default"
Panel.WATCH_S   = 1.5        -- how often a visible meter asks the app for the microphone's level (the app answers for three seconds)
Panel.REPLAY_S  = 0.3        -- seconds between the saved settings handed to the app (the command file keeps the last eight, so one at a time)
-- What the game remembers and tells the app again whenever the app (re)appears, so that the settings in the game are the ones in force.
-- Mute and deafen are not: they are a moment's choice, and starting a session deaf would only be a surprise.
Panel.REMEMBER  = { mode = true, volume = true, input = true, gate = true, gatedb = true, key = true, micdev = true, spkdev = true, spatial = true }
Panel.REPLAY_ORDER = { "micdev", "spkdev", "mode", "volume", "input", "spatial", "gate", "gatedb", "key" }
Panel.SPATIAL_LABEL = { mono = "Mono", speakers = "Speakers", headphones = "Headphones" }
Panel.SPATIAL_ORDER = { "mono", "speakers", "headphones" }
Panel.CAPTURE_S = 10         -- seconds "press a key" waits
Panel.ART       = "Interface\\AddOns\\EqVoice\\"     -- MicroUp, MicroDown, MicGlyph, MicSlash (.tga), drawn by tools/make_micro_art.py
Panel.BTN_W     = 28                                   -- the size of the client's own micro buttons
Panel.BTN_H     = 58
Panel.DOCK_GAP  = 2
Panel.DOCK_S    = 1                                    -- seconds between looks at where the micro bar is
Panel.MICRO     = { "CharacterMicroButton", "SpellbookMicroButton", "TalentMicroButton", "QuestLogMicroButton", "SocialsMicroButton",
										"WorldMapMicroButton", "MainMenuMicroButton", "HelpMicroButton", "LFGMicroButton", "PVPMicroButton", "ShopMicroButton" }

-- What the microphone setup asks of you, in order (the app times each step and says which one it is on).
Panel.SETUP_STEPS = {
	{ title = "Stay silent",    text = "Do not talk or type. Let your room be as quiet as it normally is." },
	{ title = "Talk normally",  text = "Say anything, the way you talk in the game. Keep going until the time runs out." },
	{ title = "Now shout",      text = "Shout the way you would at someone a long way off." },
}

Panel.pending     = {}       -- key -> { value, untilT }
Panel.frame       = nil
Panel.indicator   = nil
Panel.capture     = nil      -- the invisible frame that listens for the next key
Panel.capturing   = false
Panel.captureEnds = 0
Panel.seenVersion = -1
Panel.dirty       = true
Panel.lastRefresh = -100
Panel.lastDock    = -100
Panel.dockedTo    = nil      -- the micro button the indicator is anchored to (false: floating at the top of the screen)
Panel.dockedScale = nil
Panel.dragging    = false
Panel.error       = nil
Panel.sliders     = {}       -- every slider made, so that the tick can send the ones that have come to rest
Panel.setupActive = false    -- the setup was seen running
Panel.setupShown  = false    -- its result is on screen until Done is pressed
Panel.setup       = nil      -- the overlay that tells you what to do
Panel.blocklist   = nil      -- the window with everyone you have blocked
Panel.blPage      = 1
Panel.picker      = nil      -- the pop-up that lists microphones or speakers
Panel.lastWatch   = -100
Panel.watching    = false
Panel.wasAlive    = false
Panel.replayQueue = nil
Panel.replayAt    = 0

local THEME = {                                              -- EqUpdater's look: deep navy panels, gold headings and selections, cream text
	bg = { 0.024, 0.075, 0.176 }, card = { 0.031, 0.102, 0.208 }, edge = { 0.094, 0.220, 0.373 },
	btn = { 0.161, 0.227, 0.325 }, btnEdge = { 0.188, 0.286, 0.400 },
	accent = { 0.788, 0.604, 0.239 }, goldLt = { 0.894, 0.741, 0.404 },
	text = { 0.933, 0.914, 0.863 }, muted = { 0.569, 0.639, 0.741 }, dark = { 0.020, 0.055, 0.130 }, track = { 0.016, 0.063, 0.153 },
	amber = { 0.89, 0.62, 0.18 }, amberDim = { 0.30, 0.22, 0.09 }, green = { 0.475, 0.749, 0.451 }, greenDim = { 0.07, 0.22, 0.12 },
	danger = { 0.55, 0.22, 0.22 }, dangerEdge = { 0.72, 0.38, 0.38 },
}
Panel.THEME = THEME

local COLORS = {                           -- the microphone indicator's colours (and the setup pages' text colours)
	ok   = { 0.3, 1.0, 0.3 },
	talk = { 0.5, 1.0, 1.0 },
	warn = { 1.0, 0.82, 0.0 },
	bad  = { 1.0, 0.3, 0.3 },
	off  = { 0.65, 0.65, 0.65 },
	text = { 0.9, 0.9, 0.9 },
	gold = THEME.text,                    -- (was gold: the panel has no gold now)
}
-- the same meanings, softer, for the panel itself
local PC = { ok = THEME.green, talk = { 0.55, 0.80, 1.0 }, warn = THEME.goldLt, bad = { 0.82, 0.48, 0.48 }, off = THEME.muted, text = THEME.text }
Panel.COLORS = COLORS

-- ------------------------------------------------------------------ the model

local function pending(key, now)
	local p = Panel.pending[key]
	if p and now < p.untilT then return p.value end
	Panel.pending[key] = nil
	return nil
end

-- A value for the panel: the player's click while it is fresh and the app has not yet said otherwise.
local function pick(key, statusValue, now)
	local v = pending(key, now)
	if v == nil then return statusValue end
	if v == statusValue then
		Panel.pending[key] = nil          -- the app agrees: stop overriding
		return statusValue
	end
	return v
end

local CAL_PHASES = { idle = true, quiet = true, normal = true, loud = true, done = true }
local CAL_QUALITIES = { good = true, noisy = true, none = true }

function Panel.DefaultName(name)
	if name and name ~= "" then return "System default (" .. name .. ")" end
	return "System default"
end

function Panel.Model(now)
	local S = OV.Status
	local f = S.fields
	local alive = S.Alive(now)
	local m = { alive = alive }
	m.mode = pick("mode", f.mode or "open", now)
	m.mute = pick("mute", f.mute == "1", now)
	m.deaf = pick("deaf", f.deaf == "1", now)
	m.vol  = pick("vol", tonumber(f.vol) or 100, now)
	m.input = pick("ing", tonumber(f.ing) or 100, now)
	m.dkeep = pick("dk", f.dk == "1", now)                                         -- deafened with the microphone left open
	m.sp   = pick("sp", Panel.SPATIAL_LABEL[f.sp or ""] and f.sp or "headphones", now)
	m.mt   = pick("mt", f.mt == "1", now)                                          -- the microphone test is running
	m.gate = pick("gate", Panel.GATE_LABEL[f.gate or ""] and f.gate or "auto", now)
	m.gdb  = pick("gdb", tonumber(f.gdb) or -48, now)
	m.key  = (string.gsub(f.key or "F9", "_", " "))
	m.capturing = Panel.capturing and true or false
	m.tx, m.talk = (f.tx == "1"), (f.talk == "1")
	m.near, m.conn = tonumber(f.near) or 0, tonumber(f.conn) or 0
	m.mic = f.mic or "starting"
	m.voice = tonumber(f.vl) or 0                                                  -- how you sound, steadied by the app: 0 nothing, 1 speaking, 2 a raised voice
	m.micBad = (f.mic ~= nil) and f.mic ~= "ok" and f.mic ~= "starting" and f.mic ~= "no page" and f.mic ~= "no_page"   -- Windows says no, or there is none
	if alive and f.hub == "0" and f.game == "1" then                               -- cannot reach the voice server: only worth showing after a few seconds
		Panel.hubDownAt = Panel.hubDownAt or now
	else
		Panel.hubDownAt = nil
	end
	m.disconnected = alive and Panel.hubDownAt ~= nil and (now - Panel.hubDownAt) >= Panel.DISCONNECT_S
	m.devIn, m.devOut = S.devices["in"], S.devices.out                              -- the names Windows has, as the app lists them
	m.din  = pick("din", tonumber(f.din) or 0, now)                                  -- which is in use: 0 the system's own, else its place in the list
	m.dout = pick("dout", tonumber(f.dout) or 0, now)
	m.defIn, m.defOut = S.defaults["in"] or "", S.defaults.out or ""
	m.micName = (m.din > 0 and m.devIn[m.din]) or Panel.DefaultName(m.defIn)
	m.spkName = (m.dout > 0 and m.devOut[m.dout]) or Panel.DefaultName(m.defOut)
	m.lvl  = tonumber(f.lvl) or 0                                                    -- the microphone's level, 0..100, while a meter is showing
	m.rpt = f.rpt                                                                    -- the name of the log zip the app has just saved (for twenty-odd seconds)
	m.loud = (m.voice == 2)                                                          -- your voice is loud enough to carry at yell distance
	m.glive = tonumber(f.gl)                                                         -- dB of the signal the gate judges (while a meter is showing), nil if the app does not say
	m.gthr  = tonumber(f.gt)                                                         -- the bar the gate holds it against now (the room-following bar in auto)
	m.ghas  = (f.go ~= nil)                                                          -- the app runs a gate and says whether it is open
	m.gopen = (f.go == "1")
	if m.glive then m.lvl = Panel.DbToPct(m.glive) end                              -- one scale for every meter: the gate's own
	m.cal  = pick("cal", CAL_PHASES[f.cal or ""] and f.cal or "idle", now)        -- the microphone setup: idle, quiet, normal, loud or done
	m.calt = tonumber(f.calt) or 0                                                 -- seconds left in the step it is on
	m.calq = CAL_QUALITIES[f.calq or ""] and f.calq or "-"                         -- how the last setup went: good, noisy, none
	m.cald = (f.cald == "1")                                                        -- a setup has been done at least once
	if not alive then
		m.level = "off"
		m.text = "The EqVoice app is not running."
		m.hint = "Start EqVoice (its icon on your desktop), then give it a few seconds."
	else
		m.level = f.level or "warn"
		m.text = (S.headline ~= "" and S.headline) or "EqVoice"
		if S.nmore > 0 then m.text = m.text .. "  (" .. S.nmore .. " more problem" .. (S.nmore == 1 and "" or "s") .. ": see the EqVoice window)" end
		m.hint = nil
		if m.deaf and m.dkeep then m.text = m.text .. " Deafened: you hear nobody; they can still hear you."
		elseif m.deaf then m.text = m.text .. " Deafened: you hear nobody and nobody hears you."
		elseif m.mt then m.text = m.text .. " Testing your microphone: nobody hears you."
		elseif m.mute then m.text = m.text .. " Muted: nobody hears you." end
	end
	-- the indicator
	if not alive then m.badge, m.color = "NO APP", COLORS.off
	elseif m.level == "bad" then m.badge, m.color = "ERROR", COLORS.bad
	elseif m.deaf and m.dkeep then m.badge, m.color = "DEAF", COLORS.warn
	elseif m.deaf then m.badge, m.color = "DEAF", COLORS.bad
	elseif m.mt then m.badge, m.color = "TEST", COLORS.warn
	elseif m.mute then m.badge, m.color = "MUTED", COLORS.bad
	elseif m.level == "warn" then m.badge, m.color = "CHECK", COLORS.warn
	elseif m.talk then m.badge, m.color = "TALKING", COLORS.talk
	elseif m.mode == "ptt" then m.badge, m.color = "PTT " .. m.key, COLORS.ok
	else m.badge, m.color = "MIC ON", COLORS.ok end
	-- who is near
	m.rows = {}
	for i = 1, math.min(Panel.ROWS, S.npeers) do
		local p = S.peers[i]
		local key = string.lower(p.name)
		local muted = pick("pm:" .. key, (p.muted or S.muted[key]) and true or false, now)
		local blocked = pick("pb:" .. key, (p.blocked or S.blocked[key]) and true or false, now)
		local vol = pick("pv:" .. key, p.vol or 100, now)
		local info = (p.dist and (string.format("%d yd", math.floor(p.dist + 0.5))) or "? yd")
		if p.speaking then info = info .. "  speaking"
		elseif not p.connected then info = info .. "  connecting" end
		if p.path == "relay" then info = info .. "  relay" end
		if muted then info = info .. "  muted" end
		if blocked then info = info .. "  blocked" end
		m.rows[i] = { name = p.name, info = info, live = p.connected, speaking = p.speaking, muted = muted, blocked = blocked, vol = vol,
			state = OV.Icons and OV.Icons.Resolve(p.name, now) or nil }
	end
	m.nrows = table.getn(m.rows)
	m.extra = S.npeers - m.nrows
	m.nmuted = S.nmuted
	return m
end

-- What the setup overlay should say now, or nil when it should not be on screen. Pure: the model in, words out.
--   kind "step"    one of the three recordings (step, title, text, left = seconds still to go)
--   kind "result"  the end: how it went (quality good / noisy / none), and the words for it
function Panel.SetupView(m)
	local c = m.cal
	local idx = (c == "quiet" and 1) or (c == "normal" and 2) or (c == "loud" and 3) or nil
	if idx then
		local s = Panel.SETUP_STEPS[idx]
		return { kind = "step", step = idx, title = s.title, text = s.text, left = m.calt, step_text = "Step " .. idx .. " of 3" }
	end
	if not Panel.setupShown then return nil end
	if m.calq == "good" then
		local text = string.format("Your microphone is set up. The noise gate is at %d dB.", m.gdb)
		if m.input ~= 100 then text = text .. string.format(" Microphone volume: %d%%.", m.input) end
		return { kind = "result", quality = "good", title = "Your microphone is set up", text = text, step_text = "Done" }
	elseif m.calq == "noisy" then
		return { kind = "result", quality = "noisy", title = "Your room is noisy", step_text = "Finished",
			text = "Your voice is close to the level of your room, so the noise gate stays on automatic. A quieter room, or a microphone closer to your mouth, will help." }
	end
	return { kind = "result", quality = "none", title = "Nothing was changed", step_text = "It did not work",
		text = "I did not hear you talk. Check that the right microphone is chosen in the EqVoice window and that it is not muted, then try again." }
end

-- Remember that the setup ran, so that its ending (done, or nobody talked) stays on screen until Done is pressed.
function Panel.TrackSetup(m)
	local c = m.cal
	if c == "quiet" or c == "normal" or c == "loud" then
		Panel.setupActive, Panel.setupShown = true, false
	elseif c == "done" then
		Panel.setupActive, Panel.setupShown = false, true
	elseif Panel.setupActive then
		Panel.setupActive = false
		if m.calq ~= "-" then Panel.setupShown = true end
	end
end

-- ------------------------------------------------------------------- actions

-- Queue a command for the app. `pkey`/`pvalue`: show that value in the panel until the app answers.
function Panel.Command(cmd, arg, pkey, pvalue)
	local now = OV.Now()
	if not OV.Status.Alive(now) then
		OV.Print("The EqVoice app is not running, so that did nothing. Start EqVoice first.")
		return false
	end
	if not OV.Control.Send(cmd, arg) then
		OV.Print("Could not reach the app: " .. tostring(OV.Control.lastError) .. ". Is ClassicAPI loaded?")
		return false
	end
	if pkey then Panel.pending[pkey] = { value = pvalue, untilT = now + Panel.PENDING_S } end
	if Panel.REMEMBER[cmd] and OV.db then
		OV.db.settings = OV.db.settings or {}
		OV.db.settings[cmd] = arg
	end
	Panel.dirty = true
	return true
end

function Panel.SetMode(mode)
	return Panel.Command("mode", mode, "mode", mode)
end

-- The microphone ("in") or the speakers ("out") by the name the list shows ("default" for the system's own).
function Panel.SetDevice(kind, label, index)
	if kind == "in" then return Panel.Command("micdev", label, "din", index or 0) end
	return Panel.Command("spkdev", label, "dout", index or 0)
end

-- The saved settings, handed back to the app one at a time, whenever the app appears. Only what differs from what the app says is sent.
local function differs(cmd, value, m)
	local f = OV.Status.fields
	if cmd == "mode" then return f.mode ~= value
	elseif cmd == "volume" then return tonumber(f.vol) ~= tonumber(value)
	elseif cmd == "input" then return tonumber(f.ing) ~= tonumber(value)
	elseif cmd == "spatial" then return f.sp ~= value
	elseif cmd == "gate" then return f.gate ~= value
	elseif cmd == "gatedb" then return tonumber(f.gdb) ~= tonumber(value)
	elseif cmd == "key" then                                     -- the game says BUTTON4, the app calls it Mouse 4
		local function plain(s) return (string.gsub(string.upper((string.gsub(s or "", "[_ ]", ""))), "^BUTTON", "MOUSE")) end
		return plain(f.key) ~= plain(value)
	elseif cmd == "micdev" or cmd == "spkdev" then
		local list = (cmd == "micdev") and m.devIn or m.devOut
		local at = (cmd == "micdev") and m.din or m.dout
		local now = (at > 0 and list[at]) or "default"
		if string.lower(value) == "default" or string.find(string.lower(value), "^system default") then return at ~= 0 end
		for i = 1, table.getn(list) do                                    -- a device that is not there now cannot be chosen: nothing to send
			if string.lower(list[i]) == string.lower(value) then return now ~= list[i] end
		end
		return false
	end
	return false
end

function Panel.StartReplay(now)
	local saved = OV.db and OV.db.settings
	if not saved then return end
	local m = Panel.Model(now)
	local q = {}
	for i = 1, table.getn(Panel.REPLAY_ORDER) do
		local cmd = Panel.REPLAY_ORDER[i]
		local v = saved[cmd]
		if cmd == "input" and v ~= nil and (tonumber(v) or 0) < 5 then v = nil; saved.input = nil end      -- (a volume of 0 once got saved: it is not a setting anybody means)
		if v ~= nil and type(v) == "string" and v ~= "" and differs(cmd, v, m) then q[table.getn(q) + 1] = { cmd, v } end
	end
	if table.getn(q) > 0 then
		Panel.replayQueue, Panel.replayAt = q, now
		OV.Trace("settings: handing " .. table.getn(q) .. " saved setting(s) to the app", true)
	end
end

function Panel.StepReplay(now)
	local q = Panel.replayQueue
	if not q or now < Panel.replayAt then return end
	local item = table.remove(q, 1)
	if item then OV.Control.Send(item[1], item[2]) end
	if table.getn(q) == 0 then Panel.replayQueue = nil else Panel.replayAt = now + Panel.REPLAY_S end
end

function Panel.SetMute(on)
	return Panel.Command("mute", on and "1" or "0", "mute", on and true or false)
end

-- Deafen: you hear nobody, and your microphone closes with it (your own mute setting is not touched, so undeafening gives it back).
function Panel.SetDeaf(on)
	Panel.pending.dk = { value = false, untilT = OV.Now() + Panel.PENDING_S }
	return Panel.Command("deafen", on and "1" or "0", "deaf", on and true or false)
end

-- The other deafen: you hear nobody, but your microphone stays as it was and they still hear you.
function Panel.SetDeafKeep(on)
	Panel.pending.dk = { value = on and true or false, untilT = OV.Now() + Panel.PENDING_S }
	return Panel.Command("deafen", on and "2" or "0", "deaf", on and true or false)
end

-- Where voices are heard from: mono, speakers or headphones.
function Panel.SetSpatial(mode)
	if not Panel.SPATIAL_LABEL[mode] then return false end
	return Panel.Command("spatial", mode, "sp", mode)
end

-- The microphone test: you hear yourself, nobody else does. It goes on until it is stopped (closing the panel does not stop it).
function Panel.SetMicTest(on)
	return Panel.Command("mictest", on and "1" or "0", "mt", on and true or false)
end

-- One player: their own volume for you (0..200%, 100 = as everyone), whether you hear them (mute), whether they hear you (block).
function Panel.SetPlayerVolume(name, v)
	if type(name) ~= "string" or name == "" then return false end
	v = OV.clamp(math.floor(v + 0.5), 0, 200)
	return Panel.Command("pvol", string.lower(name) .. " " .. string.format("%d", v), "pv:" .. string.lower(name), v)
end

function Panel.SetPlayerBlock(name, on)
	if type(name) ~= "string" or name == "" then return false end
	return Panel.Command("pblock", string.lower(name) .. " " .. (on and "1" or "0"), "pb:" .. string.lower(name), on and true or false)
end

-- How loud everyone is for you, 0..100 in steps of 5.
function Panel.SetVolume(v)
	v = OV.clamp(math.floor(v + 0.5), 0, 100)
	return Panel.Command("volume", string.format("%d", v), "vol", v)
end

-- Microphone boost: how loud your microphone is before the noise gate and everything else, 0..200% continuously (100 = as it comes).
function Panel.SetInput(v)
	v = OV.clamp(math.floor(v + 0.5), 0, 200)
	return Panel.Command("input", string.format("%d", v), "ing", v)
end

function Panel.SetGate(mode)
	if not Panel.GATE_LABEL[mode] then return false end
	return Panel.Command("gate", mode, "gate", mode)
end

-- The manual bar, in dB (-80 very sensitive .. -20), in steps of 2.
function Panel.SetGateDb(db)
	db = OV.clamp(math.floor(db / Panel.GATE_STEP + 0.5) * Panel.GATE_STEP, Panel.GATE_MIN, Panel.GATE_MAX)
	return Panel.Command("gatedb", string.format("%d", db), "gdb", db)
end

function Panel.ToggleMute()
	local m = Panel.Model(OV.Now())
	return Panel.SetMute(not m.mute)
end

-- The microphone setup: stay silent, talk, shout. The app times it; the overlay follows what the app reports.
function Panel.StartSetup()
	Panel.setupShown = false
	return Panel.Command("calibrate", "1", "cal", "quiet")
end

-- Cancel while it runs, or dismiss its result (the same command: back to idle).
function Panel.EndSetup()
	Panel.setupShown, Panel.setupActive = false, false
	return Panel.Command("calibrate", "0", "cal", "idle")
end

-- The mute list (the blocklist): a muted player is never heard, wherever they are. They can still hear you.
function Panel.SetPlayerMute(name, on)
	if type(name) ~= "string" or name == "" then return false end
	return Panel.Command("pmute", string.lower(name) .. " " .. (on and "1" or "0"), "pm:" .. string.lower(name), on and true or false)
end

function Panel.IsPlayerMuted(name, now)
	local key = string.lower(name)
	local v = pending("pm:" .. key, now)
	if v ~= nil then return v end
	return OV.Status.muted[key] and true or false
end

function Panel.TogglePlayerMute(name)
	if type(name) ~= "string" or name == "" then return false end
	return Panel.SetPlayerMute(name, not Panel.IsPlayerMuted(name, OV.Now()))
end

-- Everyone on the list, by name (lower case, as the app keeps them), including a click the app has not yet answered.
function Panel.BlockedNames(now)
	local set = {}
	for k in pairs(OV.Status.muted) do set[k] = true end
	for key, p in pairs(Panel.pending) do
		if string.sub(key, 1, 3) == "pm:" and now < p.untilT then
			local name = string.sub(key, 4)
			if p.value then set[name] = true else set[name] = nil end
		end
	end
	local out, n = {}, 0
	for k in pairs(set) do n = n + 1; out[n] = k end
	table.sort(out)
	return out, n
end

-- "shortrounds" -> "Shortrounds": the list keeps lower case, the eye wants a capital.
function Panel.ShowName(name)
	return string.upper(string.sub(name, 1, 1)) .. string.sub(name, 2)
end

-- "Change the key": listen for the next key (or mouse button 3, 4, 5) and hand it to the app, which turns it into the key it
-- watches. The game's own names for keys are sent as they are (F9, A, NUMPAD1, LSHIFT, BUTTON4 ...). Escape, a left or right
-- click, or ten seconds of nothing cancels.
function Panel.CaptureKey()
	if not OV.Status.Alive(OV.Now()) then
		OV.Print("The EqVoice app is not running, so the key cannot be changed. Start EqVoice first.")
		return false
	end
	local ok, err = pcall(Panel.BuildCapture)
	if not ok or not Panel.capture then
		OV.Print("The key listener could not be set up (" .. tostring(err) .. ").")
		return false
	end
	Panel.capturing, Panel.captureEnds = true, OV.Now() + Panel.CAPTURE_S
	Panel.capture:Show()
	Panel.dirty = true
	OV.Print("Press the key (or mouse button 3, 4 or 5) to use. Escape or a left or right click cancels.")
	return true
end

function Panel.StopCapture(why)
	if not Panel.capturing then return end
	Panel.capturing = false
	if Panel.capture then Panel.capture:Hide() end
	Panel.dirty = true
	if why then OV.Print(why) end
end

-- `name` is what the client says was pressed: "F9", "A", "ESCAPE", "LeftButton", "Button4" ...
function Panel.OnCapturedKey(name)
	if not Panel.capturing then return end
	if name == nil or name == "" or name == "UNKNOWN" then return end
	if name == "ESCAPE" or name == "LeftButton" or name == "RightButton" then
		Panel.StopCapture("key not changed")
		return
	end
	Panel.StopCapture(nil)
	if Panel.Command("key", name) then OV.Print("key: " .. tostring(name) .. " (the app confirms it in the panel)") end
end

-- ------------------------------------------------------------------- drawing

local function fail(err)
	if Panel.error ~= tostring(err) then
		Panel.error = tostring(err)
		OV.Trace("panel error: " .. tostring(err))
	end
end

local function safe(fn)
	return function()
		local ok, err = pcall(fn)
		if not ok then fail(err) end
	end
end

local WHITE = "Interface\\Buttons\\WHITE8X8"
local FONT_DIR = Panel.ART .. "fonts\\ggsans-"
local FONT = FONT_DIR .. "Medium.ttf"             -- gg sans, as Discord draws it: Medium for text, Semibold for labels and buttons, Bold for titles
local FONT_REG = FONT_DIR .. "Regular.ttf"
local FONT_SEMI = FONT_DIR .. "Semibold.ttf"
local FONT_TITLE = FONT_DIR .. "Bold.ttf"
local FLAT, BACKDROP, BOX_BACKDROP, SLIDER_BACKDROP = nil, nil, nil, nil       -- (square backdrops are gone: every shape is drawn by newBox, rounded)

local function setColor(fs, c)
	fs:SetTextColor(c[1], c[2], c[3])
end

-- Rounded rectangles. The client cannot round a backdrop, so a shape is nine textures cut from RoundFill.tga (a white rounded square):
-- four corners, four edges and the middle, tinted with SetVertexColor. A box is laid out in the frame's own pixels from its top left.
local ROUND = Panel.ART .. "RoundFill.tga"
local ROUND_TC = {                                                          -- left, right, top, bottom of each of the nine pieces
	{ 0, 0.5, 0, 0.5 }, { 0.47, 0.53, 0, 0.5 }, { 0.5, 1, 0, 0.5 },
	{ 0, 0.5, 0.47, 0.53 }, { 0.47, 0.53, 0.47, 0.53 }, { 0.5, 1, 0.47, 0.53 },
	{ 0, 0.5, 0.5, 1 }, { 0.47, 0.53, 0.5, 1 }, { 0.5, 1, 0.5, 1 },
}

local function newBox(owner, anchor, layer)
	local box = { t = {}, color = { 1, 1, 1 }, w = 0 }
	for i = 1, 9 do
		local x = owner:CreateTexture(nil, layer)
		x:SetTexture(ROUND)
		local c = ROUND_TC[i]
		x:SetTexCoord(c[1], c[2], c[3], c[4])
		box.t[i] = x
	end
	-- x, y: the top left corner from the anchor's top left (down is negative); w, h: the size; r: the size of the rounded corners
	function box.Layout(x, y, w, h, r)
		r = math.max(0, math.min(r, math.floor(w / 2), math.floor(h / 2)))
		box.w = w
		local xs, ys, ws, hs = { x, x + r, x + w - r }, { y, y - r, y - h + r }, { r, w - 2 * r, r }, { r, h - 2 * r, r }
		for row = 1, 3 do
			for col = 1, 3 do
				local tx = box.t[(row - 1) * 3 + col]
				if ws[col] >= 1 and hs[row] >= 1 then
					tx:ClearAllPoints()
					tx:SetPoint("TOPLEFT", anchor, "TOPLEFT", xs[col], ys[row])
					tx:SetWidth(ws[col])
					tx:SetHeight(hs[row])
					tx:Show()
				else
					tx:Hide()
				end
			end
		end
	end
	function box.SetColor(c, a)
		box.color = c
		for i = 1, 9 do box.t[i]:SetVertexColor(c[1], c[2], c[3], a or 1) end
	end
	function box.Hide() box.w = 0; for i = 1, 9 do box.t[i]:Hide() end end
	box.SetColor(box.color)
	return box
end

-- A frame's rounded background: `bg` the fill, `edge` (optional) a one-pixel ring round it, `r` the size of the corners. The frame must have its size.
local function paint(f, bg, edge, r)
	local w, h = f:GetWidth() or 0, f:GetHeight() or 0
	local rad = r or ((w >= 150 and h >= 80) and 8 or 4)
	local rb = f.rbox
	if not rb then
		rb = { edge = newBox(f, f, "BACKGROUND"), fill = newBox(f, f, "BORDER") }
		f.rbox = rb
	end
	local ringed = edge ~= nil
	if rb.w ~= w or rb.h ~= h or rb.ringed ~= ringed or rb.rad ~= rad then
		rb.w, rb.h, rb.ringed, rb.rad = w, h, ringed, rad
		if ringed then
			rb.edge.Layout(0, 0, w, h, rad)
			rb.fill.Layout(1, -1, w - 2, h - 2, rad - 1)
		else
			rb.edge.Hide()
			rb.fill.Layout(0, 0, w, h, rad)
		end
	end
	rb.fill.SetColor(bg)
	if ringed then rb.edge.SetColor(edge) end
	f.bgc = bg
end

-- The ring round a painted frame turned another colour (the sensitivity bar's green edge).
local function setEdge(f, c)
	if f.rbox and f.rbox.ringed then f.rbox.edge.SetColor(c) end
end

-- A button lit a little while the mouse is on it.
local function hover(b, on)
	if not (b and b.rbox and b.bgc) then return end
	if on and b.IsEnabled and not b:IsEnabled() then on = false end
	local c = b.bgc
	b.rbox.fill.SetColor(on and { math.min(1, c[1] + 0.07), math.min(1, c[2] + 0.07), math.min(1, c[3] + 0.07) } or c)
end

local function styleText(fs, size, face)
	if fs.SetFont then fs:SetFont(face or FONT, size) end
end

local function buttonText(b, text, c)
	b:SetText(text)
	local fs = b:GetFontString()
	if fs and c then setColor(fs, c) end
end

-- A button turned on or off: dimmed and dead when off (the app is not running).
local function setEnabled(b, on)
	if on then b:Enable() else b:Disable() end
	b:SetAlpha(on and 1 or 0.45)
end

-- A button "pressed in": the purple accent (danger: red, for muted and deafened).
local function setSelected(b, on, danger)
	if on then b:LockHighlight() else b:UnlockHighlight() end
	if on then
		paint(b, danger and THEME.danger or THEME.accent)
	else
		paint(b, THEME.btn)
	end
	local fs = b:GetFontString()
	if fs then setColor(fs, (on and not danger) and THEME.dark or THEME.text) end          -- dark letters on the gold
end

local function newButton(parent, name, text, w, x, y, onclick, h)
	local b = CreateFrame("Button", name, parent)
	b:SetWidth(w)
	b:SetHeight(h or 22)
	b:SetPoint("TOPLEFT", parent, "TOPLEFT", x, y)
	paint(b, THEME.btn)
	local fs = b:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
	styleText(fs, 12, FONT_SEMI)
	if fs.SetShadowOffset then fs:SetShadowOffset(0, 0) end                -- no shadow: dark letters on gold came out smeared
	fs:SetPoint("CENTER", b, "CENTER", 0, 0)
	setColor(fs, THEME.text)
	b:SetFontString(fs)
	b:SetScript("OnEnter", function() hover(this, true) end)
	b:SetScript("OnLeave", function() hover(this, false) end)
	b:SetText(text)
	b:SetScript("OnClick", safe(onclick))
	return b
end

-- A choice box in the style of a drop-down: the chosen name, a small arrow; a click calls onclick (it opens a list).
local function newDropdown(parent, name, text, w, x, y, onclick, h)
	local b = newButton(parent, name, text, w, x, y, onclick, h or 26)
	local fs = b:GetFontString()
	fs:ClearAllPoints()
	fs:SetPoint("LEFT", b, "LEFT", 10, 0)
	fs:SetWidth(w - 34)
	fs:SetHeight(14)
	fs:SetJustifyH("LEFT")
	local arrow = b:CreateTexture(nil, "OVERLAY")                             -- a small triangle pointing down
	arrow:SetTexture(Panel.ART .. "IconDown.tga")
	arrow:SetWidth(10)
	arrow:SetHeight(10)
	arrow:SetPoint("RIGHT", b, "RIGHT", -11, 0)
	arrow:SetVertexColor(THEME.muted[1], THEME.muted[2], THEME.muted[3], 1)
	b.arrow = arrow
	return b
end

local TEXT_STYLE = {                       -- the client's font objects, by the look they were asked for: size and colour
	GameFontNormalLarge = { 22, "text", FONT_TITLE }, GameFontNormal = { 14, "accent", FONT_SEMI }, GameFontNormalSmall = { 12, "muted", FONT_REG },
	GameFontHighlight = { 14, "text" }, GameFontHighlightSmall = { 12, "text" },
	GameFontDisable = { 13, "muted", FONT_REG }, GameFontDisableSmall = { 11, "muted", FONT_REG },
}

local function newText(parent, font, x, y, w, h, justify)
	local fs = parent:CreateFontString(nil, "OVERLAY", font)
	local st = TEXT_STYLE[font]
	if st then
		styleText(fs, st[1], st[3])
		setColor(fs, THEME[st[2]])
	end
	fs:SetPoint("TOPLEFT", parent, "TOPLEFT", x, y)
	if w then fs:SetWidth(w) end
	if h then fs:SetHeight(h) end
	fs:SetJustifyH(justify or "LEFT")
	fs:SetJustifyV("TOP")
	return fs
end

-- The small x at the top right of a window.
local function closeButton(parent, name, onclick)
	local b = newButton(parent, name, "", 22, 0, 0, onclick or function() parent:Hide() end, 22)
	b:ClearAllPoints()
	b:SetPoint("TOPRIGHT", parent, "TOPRIGHT", -8, -8)
	local x = b:CreateTexture(nil, "OVERLAY")                                  -- a heavy cross
	x:SetTexture(Panel.ART .. "IconClose.tga")
	x:SetWidth(11)
	x:SetHeight(11)
	x:SetPoint("CENTER", b, "CENTER", 0, 0)
	x:SetVertexColor(THEME.text[1], THEME.text[2], THEME.text[3], 1)
	b.icon = x
	return b
end

-- A small picture at the left of a button (one of the addon's own: a microphone, a slashed microphone, a speaker ...).
local function buttonIcon(b, file)
	local t = b:CreateTexture(nil, "OVERLAY")
	t:SetTexture(Panel.ART .. file)
	t:SetWidth(14)
	t:SetHeight(14)
	t:SetPoint("LEFT", b, "LEFT", 9, 0)
	local fs = b:GetFontString()
	if fs then
		fs:ClearAllPoints()
		fs:SetPoint("CENTER", b, "CENTER", 8, 0)
	end
	b.icon = t
	return t
end

-- A section title.
local function heading(parent, text, y)
	local fs = newText(parent, "GameFontNormal", 24, y, 300, 16)
	fs:SetText(string.upper(text))
	return fs
end

-- A raised panel inside a window: a one-pixel edge and a slightly lighter fill, drawn behind the texts (textures, so the texts stay above).
local function card(f, top, bottom)
	local o = newBox(f, f, "BACKGROUND")
	o.Layout(12, top, Panel.WIDTH - 24, top - bottom, 7)
	o.SetColor(THEME.edge)
	local c = newBox(f, f, "BORDER")
	c.Layout(13, top - 1, Panel.WIDTH - 26, top - bottom - 2, 6)
	c.SetColor(THEME.card)
	return c
end

local function savePos(frame, key)
	if not (OV.db and OV.db.ui) then return end
	local point, rel, relPoint, x, y = frame:GetPoint()
	OV.db.ui[key] = { point = point, rel = relPoint, x = x, y = y, scale = frame.GetScale and frame:GetScale() or 1 }
end

local function restorePos(frame, key, point, x, y)
	frame:ClearAllPoints()
	local s = OV.db and OV.db.ui and OV.db.ui[key]
	if s and s.point then
		if frame.SetScale then frame:SetScale(s.scale or 1) end
		frame:SetPoint(s.point, UIParent, s.rel or s.point, s.x or 0, s.y or 0)
	else
		frame:SetPoint(point, UIParent, point, x, y)
	end
end

-- A slider: a thin dark track with a purple fill up to a small handle, `min`..`max` in `step`s. Dragging calls `show(value)` at once
-- (to draw the number) and `commit(value)` once the slider has rested for Panel.SLIDER_SEND_S. If the client will not make a Slider, the row
-- gets a minus and a plus button that move it by one step of a tenth of its range, and the same two functions are called.
-- s.Set(v) moves it without calling either (the model drawing itself); s.Enable(on) turns it on or off.
local function newSlider(parent, key, x, y, w, minv, maxv, step, show, commit)
	local s = { key = key, min = minv, max = maxv, step = step, value = minv, pendingValue = nil, dragging = false, enabled = true }
	local function drawFill()
		if not (s.frame and s.fillBox) then return end
		local frac = (s.value - minv) / (maxv - minv)
		if frac < 0 then frac = 0 elseif frac > 1 then frac = 1 end
		s.fillBox.Layout(0, -5, math.max(5, math.floor(frac * (w - 10) + 5)), 5, 2)
	end
	local function moved(v)
		s.value = v
		s.pendingValue, s.restAt = v, OV.Now()
		drawFill()
		show(v)
	end
	local ok, err = pcall(function()
		local f = CreateFrame("Slider", "EqVoiceSlider" .. key, parent)
		f:SetWidth(w)
		f:SetHeight(15)
		f:SetPoint("TOPLEFT", parent, "TOPLEFT", x, y)
		f:SetOrientation("HORIZONTAL")
		f:SetMinMaxValues(minv, maxv)
		f:SetValueStep(step)
		f:SetThumbTexture(ROUND)
		local th = f:GetThumbTexture()
		if th then
			th:SetWidth(10)
			th:SetHeight(13)
			th:SetVertexColor(0.95, 0.95, 1.0, 1)
		end
		local track = newBox(f, f, "BACKGROUND")
		track.Layout(0, -5, w, 5, 2)
		track.SetColor(THEME.track)
		s.fillBox = newBox(f, f, "BORDER")
		s.fillBox.SetColor(THEME.accent)
		s.fillBox.Layout(0, -5, 5, 5, 2)
		f:EnableMouse(true)
		f:SetValue(minv)
		f:SetScript("OnValueChanged", function()
			if s.setting then return end
			local v = arg1
			if not s.dragging then return end                                                  -- only a hand moves a slider: the client's own settling and layout never send a value
			if type(v) ~= "number" or math.abs(v - s.value) < step / 2 then return end        -- the echo of Set(), not a drag
			moved(v)
		end)
		f:SetScript("OnMouseDown", function() s.dragging = true end)
		f:SetScript("OnMouseUp", function() s.dragging = false end)
		s.frame = f
	end)
	if not ok then
		fail(err)
		s.frame = nil
		local nudge = (maxv - minv) / 10
		s.down = newButton(parent, "EqVoiceSlider" .. key .. "Down", "-", 24, x, y + 3, function() moved(OV.clamp(s.value - nudge, minv, maxv)); s.restAt = -100 end)
		s.up = newButton(parent, "EqVoiceSlider" .. key .. "Up", "+", 24, x + 30, y + 3, function() moved(OV.clamp(s.value + nudge, minv, maxv)); s.restAt = -100 end)
	end
	function s.Set(v)
		s.value = v
		if s.frame then
			s.setting = true
			s.frame:SetValue(v)
			s.setting = false
			drawFill()
		end
	end
	function s.Enable(on)
		s.enabled = on
		if s.frame then
			s.frame:EnableMouse(on)
			s.frame:SetAlpha(on and 1 or 0.45)
		else
			if on then s.down:Enable(); s.up:Enable() else s.down:Disable(); s.up:Disable() end
		end
	end
	function s.Busy() return s.dragging or s.pendingValue ~= nil end
	function s.Flush(now)
		if s.dragging and IsMouseButtonDown and not IsMouseButtonDown("LeftButton") then s.dragging = false end      -- let go outside the slider: OnMouseUp never came
		if s.pendingValue ~= nil and now - (s.restAt or 0) >= Panel.SLIDER_SEND_S then
			local v = s.pendingValue
			s.pendingValue = nil
			commit(v)
		end
	end
	s.commit = commit
	Panel.sliders[table.getn(Panel.sliders) + 1] = s
	return s
end

-- A row with a name on the left, a slider, and the number it stands for. `at` = { name x, name width, slider x, slider width, number x } places it
-- inside a card; without it the row is the one the setup pages use.
local function sliderRow(f, key, label, y, minv, maxv, step, textW, show, commit, at)
	at = at or {}
	local name = newText(f, "GameFontHighlightSmall", at[1] or 16, y - 4, at[2] or 84, 14)
	name:SetText(label)
	local value = newText(f, "GameFontHighlightSmall", at[5] or 262, y - 4, textW, 14)
	local s = newSlider(f, key, at[3] or 100, y - 3, at[4] or 154, minv, maxv, step, function(v) value:SetText(show(v)) end, commit)
	s.valueText, s.format = value, show
	return s, value
end

-- One line of the nearby list: the name, a voice icon after it, the distance and what they are doing. Clicking it mutes or unmutes them.
local function newRow(parent, i, y)
	local b = CreateFrame("Button", "EqVoicePanelRow" .. i, parent)
	b:SetWidth(432)
	b:SetHeight(15)
	b:SetPoint("TOPLEFT", parent, "TOPLEFT", 24, y)
	b:RegisterForClicks("LeftButtonUp")
	b:SetHighlightTexture(WHITE)
	local hl = b:GetHighlightTexture()
	if hl then
		hl:SetVertexColor(THEME.accent[1], THEME.accent[2], THEME.accent[3], 0.25)
		if hl.SetBlendMode then hl:SetBlendMode("ADD") end
	end
	b.icon = OV.Icons.New(b, 14)
	b.nameText = b:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
	styleText(b.nameText, 12)
	b.nameText:SetPoint("LEFT", b, "LEFT", 4, 0)
	b.nameText:SetWidth(120)
	b.nameText:SetHeight(14)
	b.nameText:SetJustifyH("LEFT")
	b.infoText = b:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
	styleText(b.infoText, 11)
	b.infoText:SetPoint("RIGHT", b, "RIGHT", -4, 0)
	b.infoText:SetWidth(250)
	b.infoText:SetHeight(14)
	b.infoText:SetJustifyH("RIGHT")
	b.who = nil
	b:SetScript("OnClick", safe(function() if this.who then Panel.OpenPlayer(this.who) end end))          -- a click opens that player's settings
	return b
end

-- What Wizard.lua borrows to look like the panel.
Panel.UI = { newButton = newButton, newText = newText, heading = heading, buttonIcon = buttonIcon, newSlider = newSlider, sliderRow = sliderRow,
	safe = safe, fail = fail, setColor = setColor, buttonText = buttonText, savePos = savePos, restorePos = restorePos,
	BACKDROP = BACKDROP, BOX_BACKDROP = BOX_BACKDROP, SLIDER_BACKDROP = SLIDER_BACKDROP,
	paint = paint, closeButton = closeButton, newDropdown = newDropdown, setEnabled = setEnabled, setSelected = setSelected, card = card,
	THEME = THEME, WHITE = WHITE, FLAT = FLAT, styleText = styleText, FONT = FONT, FONT_SEMI = FONT_SEMI, FONT_TITLE = FONT_TITLE }

-- A list of the microphones (kind "in") or speakers ("out"), one row each with the chosen one ticked: "System default" first, then what Windows has.
-- A click calls onPick(kind, name, index). Refresh(model) redraws it; the rows are only as many as there are devices.
function Panel.NewDeviceList(parent, kind, x, y, w, onPick)
	local list = { kind = kind, rows = {} }
	for i = 0, Panel.DEVICE_ROWS do
		local b = CreateFrame("Button", nil, parent)
		b:SetWidth(w)
		b:SetHeight(18)
		b:SetPoint("TOPLEFT", parent, "TOPLEFT", x, y - i * 20)
		b:RegisterForClicks("LeftButtonUp")
		b:SetHighlightTexture(WHITE)
		local hl = b:GetHighlightTexture()
		if hl then hl:SetVertexColor(THEME.accent[1], THEME.accent[2], THEME.accent[3], 0.25) end
		if hl and hl.SetBlendMode then hl:SetBlendMode("ADD") end
		b.mark = b:CreateTexture(nil, "OVERLAY")
		b.mark:SetTexture("Interface\\Buttons\\UI-CheckBox-Check")
		b.mark:SetWidth(16)
		b.mark:SetHeight(16)
		b.mark:SetPoint("LEFT", b, "LEFT", 2, 0)
		b.text = b:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
		b.text:SetPoint("LEFT", b, "LEFT", 22, 0)
		b.text:SetWidth(w - 26)
		b.text:SetHeight(12)
		b.text:SetJustifyH("LEFT")
		b.index = i
		b:SetScript("OnClick", safe(function() if this.label and onPick then onPick(kind, this.label, this.index) end end))
		b:Hide()
		list.rows[i + 1] = b
	end
	function list.Refresh(m)
		local names = (kind == "in") and m.devIn or m.devOut
		local chosen = (kind == "in") and m.din or m.dout
		local n = table.getn(names)
		for i = 0, Panel.DEVICE_ROWS do
			local b = list.rows[i + 1]
			local name = (i == 0) and Panel.DefaultName((kind == "in") and m.defIn or m.defOut) or names[i]
			if name and i <= n then
				b.label, b.full = (i == 0) and "default" or name, name
				b.text:SetText(name)
				if chosen == i then b.mark:Show() else b.mark:Hide() end
				setColor(b.text, chosen == i and PC.ok or PC.text)
				b:Show()
			else
				b.label, b.full = nil, nil
				b:Hide()
			end
		end
	end
	function list.Hide()
		for i = 1, table.getn(list.rows) do list.rows[i]:Hide() end
	end
	return list
end

-- A thin bar that fills with the microphone's level (the setup pages' meter: the same dB scale as the panel's sensitivity bar).
function Panel.NewMeter(parent, x, y, w)
	local bg = parent:CreateTexture(nil, "BACKGROUND")
	bg:SetTexture(WHITE)
	bg:SetVertexColor(THEME.track[1], THEME.track[2], THEME.track[3], 1)
	bg:SetWidth(w)
	bg:SetHeight(8)
	bg:SetPoint("TOPLEFT", parent, "TOPLEFT", x, y)
	local fill = parent:CreateTexture(nil, "ARTWORK")
	fill:SetTexture(WHITE)
	fill:SetHeight(8)
	fill:SetWidth(1)
	fill:SetPoint("TOPLEFT", parent, "TOPLEFT", x, y)
	local meter = { bg = bg, fill = fill, w = w, pct = 0 }
	function meter.Set(pct)
		pct = OV.clamp(pct or 0, 0, 100)
		meter.pct = pct
		fill:SetWidth(math.max(1, w * pct / 100))
		local c = (pct >= 92) and PC.bad or ((pct >= 75) and PC.warn or THEME.green)
		fill:SetVertexColor(c[1], c[2], c[3], 1)
		if pct <= 0 then fill:Hide() else fill:Show() end
	end
	function meter.Show() bg:Show(); if meter.pct > 0 then fill:Show() end end
	function meter.Hide() bg:Hide(); fill:Hide() end
	meter.Set(0)
	return meter
end

-- The sensitivity bar: ONE bar that is both the live microphone meter and the noise gate's setting, as Discord's input sensitivity.
--   * the dark track runs DB_MIN..DB_MAX dB; the live signal fills it from the left: the signal the gate itself judges (the app's `gl`);
--   * the part of the track below the bar is amber, the part above it green (dim), and the live fill takes the bright version of each,
--     so you see at once whether your voice clears the bar; a green edge says the gate is open;
--   * the handle sits on the track at the bar (manual: drag it, or click the track; auto: it shows where the room-following bar is, and cannot
--     be moved; gate off: no handle); its value in dB is written under it.
-- bar.Set(level dB or nil, bar dB, mode "auto"|"manual"|"off", open) draws; dragging calls commit(dB) once the handle has rested.
Panel.DB_MIN, Panel.DB_MAX = -90, -10

function Panel.DbToPct(db)
	return OV.clamp(((db or Panel.DB_MIN) - Panel.DB_MIN) / (Panel.DB_MAX - Panel.DB_MIN) * 100, 0, 100)
end

function Panel.NewGateBar(parent, x, y, w, h, commit)
	local bar = { w = w, h = h, loud = false, level = nil, shown = nil, thr = -48, mode = "auto", open = false, enabled = true, dragging = false, pct = 0,
		pendingValue = nil, restAt = 0 }
	local t = CreateFrame("Frame", "EqVoiceGateBar", parent)
	t:SetWidth(w)
	t:SetHeight(h)
	t:SetPoint("TOPLEFT", parent, "TOPLEFT", x, y)
	t:EnableMouse(true)
	paint(t, THEME.track, THEME.edge, 4)
	bar.frame = t
	local function segBox(c, layer)
		local b = newBox(t, t, layer)
		b.SetColor(c)
		b.Hide()
		return b
	end
	bar.greenDim = segBox(THEME.greenDim, "ARTWORK")                          -- the whole track, green: the part above the bar
	bar.amberDim = segBox(THEME.amberDim, "ARTWORK")                          -- laid over its left part: the part below the bar
	bar.amber = segBox(THEME.amber, "OVERLAY")                                -- the live signal below the bar
	bar.green = segBox(THEME.green, "OVERLAY")                                -- and above it
	bar.handle = t:CreateTexture(nil, "OVERLAY")
	bar.handle:SetTexture(WHITE)
	bar.handle:SetVertexColor(0.96, 0.96, 1.0, 1)
	bar.handle:Hide()
	bar.label = t:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
	styleText(bar.label, 12)
	bar.label:SetWidth(60)
	bar.label:SetHeight(14)
	bar.label:SetJustifyH("CENTER")
	local function px(db) return 1 + Panel.DbToPct(db) / 100 * (w - 2) end
	local function seg(b, x0, x1)
		if x1 - x0 >= 1 then b.Layout(x0, -1, x1 - x0, h - 2, 3) else b.Hide() end
	end
	local LOUD = { 0.92, 0.27, 0.27 }
	function bar.Draw()
		local lx = bar.shown and px(bar.shown) or 1
		bar.amber.SetColor(bar.loud and LOUD or THEME.amber)                      -- red: loud enough to be heard at yell distance
		bar.green.SetColor(bar.loud and LOUD or THEME.green)
		seg(bar.greenDim, 1, w - 1)
		if bar.mode == "off" then
			bar.amberDim.Hide()
			bar.amber.Hide()
			seg(bar.green, 1, lx)
			bar.handle:Hide()
			bar.label:SetText("")
		else
			local tx = px(bar.thr)
			seg(bar.amberDim, 1, math.min(w - 1, tx + 2))                           -- (the two colours meet under the handle, which is four wide)
			seg(bar.amber, 1, math.min(lx, tx + 2))
			if lx > tx then seg(bar.green, tx - 2, lx) else bar.green.Hide() end
			bar.handle:ClearAllPoints()
			bar.handle:SetPoint("TOPLEFT", t, "TOPLEFT", tx - 2, 4)
			bar.handle:SetWidth(4)
			bar.handle:SetHeight(h + 8)
			local hc = (bar.mode == "manual") and { 0.96, 0.96, 1.0 } or THEME.muted
			bar.handle:SetVertexColor(hc[1], hc[2], hc[3], 1)
			bar.handle:Show()
			bar.label:ClearAllPoints()
			bar.label:SetPoint("TOPLEFT", t, "TOPLEFT", OV.clamp(tx - 30, 0, w - 60), -(h + 6))
			bar.label:SetText(string.format("%d dB", bar.thr))
			setColor(bar.label, (bar.mode == "manual") and THEME.text or THEME.muted)
		end
		local e = bar.open and THEME.green or THEME.edge
		setEdge(t, e)
	end
	function bar.Set(level, thr, mode, open, loud)
		bar.level, bar.mode, bar.open, bar.loud = level, mode or "auto", open and true or false, loud and true or false
		if bar.shown == nil or level == nil then bar.shown = level end          -- the first reading (and silence) is drawn at once; the rest is eased in OnUpdate
		if not bar.Busy() and thr then bar.thr = thr end
		bar.pct = level and Panel.DbToPct(level) or 0
		bar.Draw()
	end
	local function cursorDb()
		local cx = GetCursorPosition()
		local sc, left = t:GetEffectiveScale(), t:GetLeft()
		if not (cx and sc and left) then return nil end
		local db = Panel.DB_MIN + (cx / sc - left - 1) / (w - 2) * (Panel.DB_MAX - Panel.DB_MIN)
		return OV.clamp(math.floor(db / Panel.GATE_STEP + 0.5) * Panel.GATE_STEP, Panel.GATE_MIN, Panel.GATE_MAX)
	end
	local function follow()
		local db = cursorDb()
		if db and db ~= bar.thr then
			bar.thr = db
			bar.pendingValue, bar.restAt = db, OV.Now()
			bar.Draw()
		end
	end
	t:SetScript("OnMouseDown", safe(function()
		if bar.enabled and bar.mode == "manual" then bar.dragging = true; follow() end
	end))
	t:SetScript("OnMouseUp", function() bar.dragging = false end)
	t:SetScript("OnUpdate", safe(function()
		if bar.dragging then
			if IsMouseButtonDown and not IsMouseButtonDown("LeftButton") then bar.dragging = false else follow() end
		end
		if bar.level and bar.shown and bar.shown ~= bar.level then          -- ease towards the reading: quick up, a little slower down
			local k = (bar.level > bar.shown) and 0.04 or 0.14
			bar.shown = bar.shown + (bar.level - bar.shown) * (1 - math.exp(-(arg1 or 0.02) / k))
			if math.abs(bar.level - bar.shown) < 0.2 then bar.shown = bar.level end
			bar.Draw()
		end
	end))
	function bar.Enable(on)
		bar.enabled = on
		t:SetAlpha(on and 1 or 0.6)
	end
	function bar.Busy() return bar.dragging or bar.pendingValue ~= nil end
	function bar.Flush(now)
		if bar.pendingValue ~= nil and now - (bar.restAt or 0) >= Panel.SLIDER_SEND_S then
			local v = bar.pendingValue
			bar.pendingValue = nil
			commit(v)
		end
	end
	Panel.sliders[table.getn(Panel.sliders) + 1] = bar
	bar.Draw()
	return bar
end

function Panel.Build()
	if Panel.frame then return Panel.frame end
	if not CreateFrame or not UIParent then return nil end
	local f = CreateFrame("Frame", "EqVoicePanel", UIParent)
	f:SetWidth(Panel.WIDTH)
	f:SetHeight(Panel.HEIGHT)
	f:SetFrameStrata("DIALOG")
	f:EnableMouse(true)
	f:SetMovable(true)
	f:SetClampedToScreen(true)
	f:RegisterForDrag("LeftButton")
	f:SetScript("OnDragStart", function() this:StartMoving() end)
	f:SetScript("OnDragStop", safe(function() this:StopMovingOrSizing(); savePos(Panel.frame, "panelPos") end))
	paint(f, THEME.bg, THEME.edge)
	restorePos(f, "panelPos", "CENTER", 0, 40)
	f:Hide()
	card(f, -84, -346)
	card(f, -354, -486)
	card(f, -494, -630)

	-- the header: name, connection status, mute and the two deafens
	f.logo = f:CreateTexture(nil, "OVERLAY")
	f.logo:SetTexture(Panel.ART .. "Logo.tga")
	f.logo:SetWidth(30)
	f.logo:SetHeight(30)
	f.logo:SetPoint("TOPLEFT", f, "TOPLEFT", 22, -10)
	f.title = newText(f, "GameFontNormalLarge", 60, -14, 200, 26)
	f.title:SetText("EqVoice")
	closeButton(f, "EqVoicePanelClose")
	f.dot = f:CreateTexture(nil, "OVERLAY")
	f.dot:SetTexture(WHITE)
	f.dot:SetWidth(8)
	f.dot:SetHeight(8)
	f.dot:SetPoint("TOPLEFT", f, "TOPLEFT", 24, -45)
	f.status = newText(f, "GameFontHighlightSmall", 38, -42, 232, 40)
	f.bMute = newButton(f, "EqVoicePanelMute", "Mute", 88, 276, -36, function() Panel.ToggleMute() end, 30)
	buttonIcon(f.bMute, "MicGlyph.tga")
	do                                                                       -- the slash over the microphone: a second picture of the same size
		local sl = f.bMute:CreateTexture(nil, "OVERLAY")
		sl:SetTexture(Panel.ART .. "MicSlash.tga")
		sl:SetWidth(14)
		sl:SetHeight(14)
		sl:SetPoint("LEFT", f.bMute, "LEFT", 9, 0)
		f.bMute.slash = sl
	end
	f.bDeaf = newButton(f, "EqVoicePanelDeafen", "Deafen", 80, 368, -36, function()
		local m = Panel.Model(OV.Now())
		Panel.SetDeaf(not (m.deaf and not m.dkeep))
	end, 30)
	buttonIcon(f.bDeaf, "VoiceDeafened.tga")
	f.bDeafKeep = newButton(f, "EqVoicePanelDeafenKeep", "", 20, 448, -36, function()           -- the other deafen: you hear nobody, they still hear you
		local m = Panel.Model(OV.Now())
		Panel.SetDeafKeep(not (m.deaf and m.dkeep))
	end, 30)
	do
		local g = f.bDeafKeep:CreateTexture(nil, "OVERLAY")
		g:SetTexture(Panel.ART .. "IconRight.tga")
		g:SetWidth(10)
		g:SetHeight(10)
		g:SetPoint("CENTER", f.bDeafKeep, "CENTER", 0, 0)
		g:SetVertexColor(THEME.text[1], THEME.text[2], THEME.text[3], 1)
		f.bDeafKeep.icon = g
	end

	-- Microphone: device, boost, mode and key, calibrate, the one sensitivity bar, the test
	heading(f, "Microphone", -92)
	local lab = newText(f, "GameFontHighlightSmall", 24, -122, 100, 14)
	lab:SetText("Input device")
	f.bMicDev = newDropdown(f, "EqVoicePanelMicDevice", "System default", 328, 128, -116, function() Panel.OpenPicker("in") end)
	f.sIn, f.inText = sliderRow(f, "Input", "Boost", -152, 0, 200, 1, 56,
		function(v) return string.format("%d%%", v) end, function(v) Panel.SetInput(v) end, { 24, 100, 128, 258, 394 })
	f.bOpen = newButton(f, "EqVoicePanelOpenMic", "Open mic", 108, 24, -182, function() Panel.SetMode("open") end, 28)
	buttonIcon(f.bOpen, "MicGlyph.tga")
	f.bPtt = newButton(f, "EqVoicePanelPtt", "Push to talk", 118, 132, -182, function() Panel.SetMode("ptt") end, 28)
	f.bKey = newButton(f, "EqVoicePanelKey", "Keybind: F9", 108, 258, -182, function()
		if Panel.capturing then Panel.StopCapture("key not changed") else Panel.CaptureKey() end
	end, 28)
	f.bSetup = newButton(f, "EqVoicePanelSetup", "Calibrate", 82, 374, -182, function() Panel.StartSetup() end, 28)

	local sens = newText(f, "GameFontHighlightSmall", 24, -222, 260, 14)
	sens:SetText("Input sensitivity")
	f.bGManual = newButton(f, "EqVoicePanelGateManual", "Manual", 60, 292, -218, function() Panel.SetGate("manual") end, 22)
	f.bGAuto = newButton(f, "EqVoicePanelGateAuto", "Auto", 52, 354, -218, function() Panel.SetGate("auto") end, 22)
	f.bGOff = newButton(f, "EqVoicePanelGateOff", "Off", 48, 408, -218, function() Panel.SetGate("off") end, 22)
	f.sGate = Panel.NewGateBar(f, 24, -246, 432, 22, function(v)           -- moving the handle means you want it where you put it: manual
		local m = Panel.Model(OV.Now())
		if m.gate ~= "manual" then Panel.SetGate("manual") end
		Panel.SetGateDb(v)
	end)
	f.meter = f.sGate                                                      -- (the one bar is the meter: there is no second one)
	f.gateNote = newText(f, "GameFontDisableSmall", 24, -296, 432, 12)
	f.bTestMic = newButton(f, "EqVoicePanelTestMic", "Test Microphone", 432, 24, -316, function()
		local m = Panel.Model(OV.Now())
		Panel.SetMicTest(not m.mt)
	end, 26)

	-- Speaker: device, volume, where voices are heard from
	heading(f, "Speaker", -362)
	local lab2 = newText(f, "GameFontHighlightSmall", 24, -392, 100, 14)
	lab2:SetText("Output device")
	f.bSpkDev = newDropdown(f, "EqVoicePanelSpeakerDevice", "System default", 328, 128, -386, function() Panel.OpenPicker("out") end)
	f.sVol, f.volText = sliderRow(f, "Volume", "Output volume", -424, 0, 100, 1, 56,
		function(v) return string.format("%d%%", v) end, function(v) Panel.SetVolume(v) end, { 24, 100, 128, 258, 394 })
	local lab3 = newText(f, "GameFontHighlightSmall", 24, -458, 100, 14)
	lab3:SetText("Sound")
	f.bSpatial = {}
	for i = 1, 3 do
		local mode = Panel.SPATIAL_ORDER[i]
		f.bSpatial[mode] = newButton(f, "EqVoicePanelSound" .. i, Panel.SPATIAL_LABEL[mode], (i == 3) and 114 or 100, 128 + (i - 1) * 102, -452, function() Panel.SetSpatial(mode) end, 26)
	end

	-- Nearby players, the overhead icons and the blocklist
	heading(f, "Nearby", -502)
	f.nearHint = newText(f, "GameFontDisableSmall", 104, -505, 130, 12)
	f.nearHint:SetText("click a name")
	f.bOverhead = newButton(f, "EqVoicePanelOverhead", "Overhead icons: On", 132, 228, -500, function() Panel.SetOverhead(not Panel.OverheadOn()) end, 24)
	f.bBlock = newButton(f, "EqVoicePanelBlock", "Blocklist", 88, 368, -500, function() Panel.ToggleBlocklist() end, 24)
	f.rows, f.rowName, f.rowInfo = {}, {}, {}
	for i = 1, Panel.ROWS do
		local row = newRow(f, i, -532 - (i - 1) * 16)
		f.rows[i], f.rowName[i], f.rowInfo[i] = row, row.nameText, row.infoText
	end
	f.nobody = newText(f, "GameFontHighlightSmall", 24, -536, 432, 14)
	f.nobody2 = newText(f, "GameFontDisableSmall", 24, -554, 432, 12)
	f.muteNote = newText(f, "GameFontDisableSmall", 24, -616, 432, 12)
	f.hint = newText(f, "GameFontDisableSmall", 24, -614, 300, 12)
	f.bReport = newButton(f, "EqVoicePanelReport", "Export Logs", 100, 356, -611, function() Panel.Command("report", "1") end, 17)

	if UISpecialFrames then table.insert(UISpecialFrames, "EqVoicePanel") end
	f:SetScript("OnShow", function() Panel.dirty = true end)
	Panel.frame = f
	return f
end

function Panel.OverheadOn()
	return not (OV.db and OV.db.ui and OV.db.ui.overhead == false)
end

function Panel.SetOverhead(on)
	if OV.Icons and OV.Icons.SetOverhead then OV.Icons.SetOverhead(on) end
	Panel.dirty = true
end

-- ------------------------------------------------------------------ one player's settings

-- The window a click on a player's speaker (or name) opens: their own volume for you, mute, block. It follows the app's answers.
function Panel.BuildPlayer()
	if Panel.player then return Panel.player end
	if not CreateFrame or not UIParent then return nil end
	local f = CreateFrame("Frame", "EqVoicePlayer", UIParent)
	f:SetWidth(300)
	f:SetHeight(192)
	f:SetFrameStrata("DIALOG")
	f:SetFrameLevel(10)
	f:EnableMouse(true)
	f:SetMovable(true)
	f:SetClampedToScreen(true)
	f:RegisterForDrag("LeftButton")
	f:SetScript("OnDragStart", function() this:StartMoving() end)
	f:SetScript("OnDragStop", safe(function() this:StopMovingOrSizing(); savePos(Panel.player, "playerPos") end))
	paint(f, THEME.bg, THEME.edge)
	restorePos(f, "playerPos", "CENTER", 300, 40)
	f:Hide()
	f.icon = OV.Icons.New(f, 24)
	f.name = newText(f, "GameFontNormalLarge", 22, -14, 230, 26)
	f.sub = newText(f, "GameFontDisableSmall", 22, -42, 256, 12)
	closeButton(f, "EqVoicePlayerClose")
	f.sVol, f.volText = sliderRow(f, "PlayerVol", "Volume", -70, 0, 200, 1, 50,
		function(v) return string.format("%d%%", v) end, function(v) if Panel.playerWho then Panel.SetPlayerVolume(Panel.playerWho, v) end end,
		{ 22, 56, 82, 148, 236 })
	f.bReset = newButton(f, "EqVoicePlayerReset", "Reset to 100%", 110, 22, -100, function()
		if Panel.playerWho then Panel.SetPlayerVolume(Panel.playerWho, 100) end
	end, 22)
	f.bMute = newButton(f, "EqVoicePlayerMute", "Mute", 124, 22, -134, function()
		if Panel.playerWho then Panel.TogglePlayerMute(Panel.playerWho) end
	end, 28)
	buttonIcon(f.bMute, "MicSlash.tga")
	f.bBlock = newButton(f, "EqVoicePlayerBlock", "Block", 124, 154, -134, function()
		if Panel.playerWho then Panel.SetPlayerBlock(Panel.playerWho, not Panel.IsPlayerBlocked(Panel.playerWho, OV.Now())) end
	end, 28)
	f.note = newText(f, "GameFontDisableSmall", 22, -168, 256, 12)
	f.note:SetText("Mute: you stop hearing them. Block: they stop hearing you.")
	if UISpecialFrames then table.insert(UISpecialFrames, "EqVoicePlayer") end
	f:SetScript("OnShow", function() Panel.dirty = true end)
	Panel.player = f
	return f
end

function Panel.IsPlayerBlocked(name, now)
	local key = string.lower(name)
	local v = pending("pb:" .. key, now)
	if v ~= nil then return v end
	return OV.Status.blocked[key] and true or false
end

function Panel.OpenPlayer(name)
	if type(name) ~= "string" or name == "" then return end
	local ok, err = pcall(Panel.BuildPlayer)
	if not ok or not Panel.player then fail(err or "no player window"); OV.Print("The player settings could not be drawn."); return end
	local f = Panel.player
	if f:IsShown() and Panel.playerWho == string.lower(name) then f:Hide(); return end
	Panel.playerWho, Panel.playerName = string.lower(name), name
	f.sVol.pendingValue = nil
	f:Show()
	Panel.Refresh()
end

local function applyPlayer(f, m, now)
	local key = Panel.playerWho
	if not key then return end
	local row = OV.Status.byName[key]
	f.name:SetText(Panel.playerName or Panel.ShowName(key))
	local state = OV.Icons and OV.Icons.Resolve(Panel.playerName or key, now) or nil
	OV.Icons.Set(f.icon, state)
	OV.Icons.PlaceBeside(f.icon, f.name)
	local vol = pending("pv:" .. key, now)
	if vol == nil then vol = (row and row.vol) or 100 end
	if not f.sVol.Busy() then f.sVol.Set(vol); f.volText:SetText(string.format("%d%%", vol)) end
	local muted, blocked = Panel.IsPlayerMuted(key, now), Panel.IsPlayerBlocked(key, now)
	buttonText(f.bMute, muted and "Unmute" or "Mute")
	buttonText(f.bBlock, blocked and "Unblock" or "Block")
	setSelected(f.bMute, muted, true)
	setSelected(f.bBlock, blocked, true)
	if row then
		f.sub:SetText(string.format("%s%s", row.dist and (string.format("%d yd away", math.floor(row.dist + 0.5))) or "out of range", row.connected and "" or "  (connecting)"))
	else
		f.sub:SetText("not near you now: these settings are kept")
	end
	local alive = m.alive
	f.sVol.Enable(alive)
	setEnabled(f.bReset, alive); setEnabled(f.bMute, alive); setEnabled(f.bBlock, alive)
end

-- The pop-up that lists the microphones or the speakers beside the panel: one click picks one.
function Panel.BuildPicker()
	if Panel.picker then return Panel.picker end
	if not CreateFrame or not UIParent then return nil end
	local f = CreateFrame("Frame", "EqVoiceDevicePicker", UIParent)
	f:SetWidth(380)
	f:SetHeight(44 + (Panel.DEVICE_ROWS + 1) * 20 + 22)
	f:SetFrameStrata("DIALOG")
	f:SetFrameLevel(20)
	f:EnableMouse(true)
	f:SetClampedToScreen(true)
	paint(f, THEME.bg, THEME.edge)
	f:Hide()
	f.title = newText(f, "GameFontNormal", 18, -16, 300, 14)
	closeButton(f, "EqVoiceDevicePickerClose")
	local function picked(kind, label, index)
		Panel.SetDevice(kind, label, index)
		f:Hide()
	end
	f.lists = { ["in"] = Panel.NewDeviceList(f, "in", 18, -40, 344, picked), out = Panel.NewDeviceList(f, "out", 18, -40, 344, picked) }
	f.note = newText(f, "GameFontDisableSmall", 18, -44 - (Panel.DEVICE_ROWS + 1) * 20, 344, 12)
	f.note:SetText("Only the devices Windows lists are shown.")
	if UISpecialFrames then table.insert(UISpecialFrames, "EqVoiceDevicePicker") end
	f:SetScript("OnShow", function() Panel.dirty = true end)
	Panel.picker = f
	return f
end

function Panel.OpenPicker(kind)
	local ok, err = pcall(Panel.BuildPicker)
	if not ok or not Panel.picker then
		fail(err or "no picker")
		OV.Print("The device list could not be drawn.")
		return
	end
	local f = Panel.picker
	if f:IsShown() and f.kind == kind then f:Hide(); return end
	f.kind = kind
	f.title:SetText(kind == "in" and "Choose your microphone" or "Choose your speakers")
	f.lists["in"].Hide(); f.lists.out.Hide()
	f:ClearAllPoints()
	if Panel.frame then f:SetPoint("TOPLEFT", Panel.frame, "TOPRIGHT", 2, -150) else f:SetPoint("CENTER", UIParent, "CENTER", 0, 0) end
	f:Show()
	Panel.Refresh()
end

-- A frame over the whole screen that takes the next key press, and nothing else, while "change key" is waiting.
function Panel.BuildCapture()
	if Panel.capture then return Panel.capture end
	if not CreateFrame or not UIParent then return nil end
	local c = CreateFrame("Frame", "EqVoiceKeyCapture", UIParent)
	c:SetAllPoints(UIParent)
	c:SetFrameStrata("FULLSCREEN_DIALOG")
	c:EnableKeyboard(true)
	c:EnableMouse(true)
	c:SetScript("OnKeyDown", safe(function() Panel.OnCapturedKey(arg1) end))
	c:SetScript("OnMouseDown", safe(function() Panel.OnCapturedKey(arg1) end))
	c:Hide()
	Panel.capture = c
	return c
end

-- The setup overlay: what to do now, how long is left, and (at the end) how it went. It follows the app's report, so it also
-- shows when the setup was started from the desktop window.
function Panel.BuildSetup()
	if Panel.setup then return Panel.setup end
	if not CreateFrame or not UIParent then return nil end
	local f = CreateFrame("Frame", "EqVoiceSetup", UIParent)
	f:SetWidth(380)
	f:SetHeight(190)
	f:SetFrameStrata("FULLSCREEN_DIALOG")
	f:EnableMouse(true)
	f:SetMovable(true)
	f:SetClampedToScreen(true)
	f:RegisterForDrag("LeftButton")
	f:SetScript("OnDragStart", function() this:StartMoving() end)
	f:SetScript("OnDragStop", safe(function() this:StopMovingOrSizing(); savePos(Panel.setup, "setupPos") end))
	paint(f, THEME.bg, THEME.edge)
	restorePos(f, "setupPos", "TOP", 0, -150)
	f:Hide()
	f.step = newText(f, "GameFontNormalSmall", 20, -18, 340, 12, "CENTER")
	f.title = newText(f, "GameFontNormalLarge", 20, -36, 340, 18, "CENTER")
	f.text = newText(f, "GameFontHighlight", 28, -64, 324, 52, "CENTER")
	f.left = newText(f, "GameFontNormalLarge", 20, -118, 340, 18, "CENTER")
	f.bCancel = newButton(f, "EqVoiceSetupCancel", "Cancel", 100, 140, -150, function() Panel.EndSetup() end)
	f.bDone = newButton(f, "EqVoiceSetupDone", "Done", 100, 140, -150, function() Panel.EndSetup() end)
	Panel.setup = f
	return f
end

local function applySetup(m)
	local view = Panel.SetupView(m)
	if not view then
		if Panel.setup and Panel.setup:IsShown() then Panel.setup:Hide() end
		return
	end
	local ok, err = pcall(Panel.BuildSetup)
	if not ok or not Panel.setup then fail(err or "no setup frame"); return end
	local f = Panel.setup
	f.step:SetText(view.step_text or "")
	f.title:SetText(view.title or "")
	f.text:SetText(view.text or "")
	if view.kind == "step" then
		f.left:SetText(view.left and view.left > 0 and (view.left .. " s") or "")
		f.bCancel:Show(); f.bDone:Hide()
	else
		f.left:SetText("")
		f.bCancel:Hide(); f.bDone:Show()
	end
	if not f:IsShown() then f:Show() end
end

-- The blocklist window: everyone you have blocked, a page at a time, with a way to unblock each and a place to add a name.
function Panel.BuildBlocklist()
	if Panel.blocklist then return Panel.blocklist end
	if not CreateFrame or not UIParent then return nil end
	local f = CreateFrame("Frame", "EqVoiceBlocklist", UIParent)
	f:SetWidth(340)
	f:SetHeight(392)
	f:SetFrameStrata("DIALOG")
	f:EnableMouse(true)
	f:SetMovable(true)
	f:SetClampedToScreen(true)
	f:RegisterForDrag("LeftButton")
	f:SetScript("OnDragStart", function() this:StartMoving() end)
	f:SetScript("OnDragStop", safe(function() this:StopMovingOrSizing(); savePos(Panel.blocklist, "blocklistPos") end))
	paint(f, THEME.bg, THEME.edge)
	restorePos(f, "blocklistPos", "CENTER", 260, 40)
	f:Hide()
	local title = newText(f, "GameFontNormalLarge", 18, -16, 240, 18)
	title:SetText("Blocked players")
	closeButton(f, "EqVoiceBlocklistClose")
	f.info = newText(f, "GameFontHighlightSmall", 18, -40, 304, 28)
	f.info:SetText("You never hear these players, wherever they are. They can still hear you.")
	f.rows = {}
	for i = 1, Panel.BL_ROWS do
		local y = -76 - (i - 1) * 20
		local row = { name = newText(f, "GameFontHighlight", 22, y - 4, 200, 14) }
		row.btn = newButton(f, "EqVoiceBlocklistRow" .. i, "Unblock", 76, 246, y, function()
			if Panel.blWho and Panel.blWho[i] then Panel.SetPlayerMute(Panel.blWho[i], false) end
		end)
		f.rows[i] = row
	end
	f.empty = newText(f, "GameFontDisable", 22, -80, 296, 14)
	f.prev = newButton(f, "EqVoiceBlocklistPrev", "", 30, 22, -282, function() Panel.blPage = math.max(1, Panel.blPage - 1); Panel.dirty = true end)
	f.next = newButton(f, "EqVoiceBlocklistNext", "", 30, 58, -282, function() Panel.blPage = Panel.blPage + 1; Panel.dirty = true end)
	for _, side in ipairs({ { f.prev, "IconLeft.tga" }, { f.next, "IconRight.tga" } }) do        -- a solid triangle each way
		local g = side[1]:CreateTexture(nil, "OVERLAY")
		g:SetTexture(Panel.ART .. side[2])
		g:SetWidth(12)
		g:SetHeight(12)
		g:SetPoint("CENTER", side[1], "CENTER", 0, 0)
		g:SetVertexColor(THEME.text[1], THEME.text[2], THEME.text[3], 1)
		side[1].icon = g
	end
	f.page = newText(f, "GameFontHighlightSmall", 96, -287, 200, 12)
	local lab = newText(f, "GameFontNormalSmall", 22, -318, 280, 12)
	lab:SetText("Block a player")
	local eb = CreateFrame("EditBox", "EqVoiceBlocklistInput", f)
	eb:SetWidth(150)
	eb:SetHeight(22)
	eb:SetPoint("TOPLEFT", f, "TOPLEFT", 22, -336)
	eb:SetFontObject(GameFontHighlight)
	eb:SetAutoFocus(false)
	eb:SetMaxLetters(24)
	eb:SetTextInsets(6, 6, 0, 0)
	paint(eb, THEME.track, THEME.edge)
	if eb.SetFont then eb:SetFont(FONT, 13) end
	eb:SetScript("OnEscapePressed", function() this:ClearFocus() end)
	eb:SetScript("OnEnterPressed", safe(function() Panel.BlockTyped() end))
	f.input = eb
	f.bAdd = newButton(f, "EqVoiceBlocklistAdd", "Block", 64, 180, -336, function() Panel.BlockTyped() end)
	f.bTarget = newButton(f, "EqVoiceBlocklistTarget", "Block target", 82, 248, -336, function() Panel.BlockTarget() end)
	if UISpecialFrames then table.insert(UISpecialFrames, "EqVoiceBlocklist") end
	f:SetScript("OnShow", function() Panel.dirty = true end)
	Panel.blocklist = f
	return f
end

-- The name in the box goes on the list (a character name: letters, and a hyphen is allowed).
function Panel.BlockTyped()
	local f = Panel.blocklist
	if not (f and f.input) then return end
	local name = string.gsub(f.input:GetText() or "", "^%s*(.-)%s*$", "%1")
	if name == "" then return end
	if Panel.SetPlayerMute(name, true) then
		f.input:SetText("")
		f.input:ClearFocus()
		OV.Print("blocked " .. Panel.ShowName(string.lower(name)) .. ": you will not hear them")
	end
end

function Panel.BlockTarget()
	local name = UnitName and UnitName("target") or nil
	if type(name) ~= "string" or name == "" or (UnitIsPlayer and not UnitIsPlayer("target")) then
		OV.Print("Your target is not a player.")
		return
	end
	if Panel.SetPlayerMute(name, true) then OV.Print("blocked " .. name .. ": you will not hear them") end
end

function Panel.ToggleBlocklist()
	local ok, err = pcall(Panel.BuildBlocklist)
	if not ok or not Panel.blocklist then
		fail(err or "no blocklist frame")
		OV.Print("The blocklist could not be drawn. /eqvc muted lists it in the chat.")
		return
	end
	if Panel.blocklist:IsShown() then Panel.blocklist:Hide() else Panel.blocklist:Show(); Panel.Refresh() end
end

local function applyBlocklist(f, now)
	local names, n = Panel.BlockedNames(now)
	local pages = math.max(1, math.ceil(n / Panel.BL_ROWS))
	if Panel.blPage > pages then Panel.blPage = pages end
	if Panel.blPage < 1 then Panel.blPage = 1 end
	local first = (Panel.blPage - 1) * Panel.BL_ROWS
	Panel.blWho = {}
	for i = 1, Panel.BL_ROWS do
		local who = names[first + i]
		local row = f.rows[i]
		if who then
			Panel.blWho[i] = who
			row.name:SetText(Panel.ShowName(who))
			row.btn:Show()
		else
			row.name:SetText("")
			row.btn:Hide()
		end
	end
	f.empty:SetText(n == 0 and "Nobody is blocked." or "")
	f.page:SetText(string.format("%d player%s   page %d of %d", n, n == 1 and "" or "s", Panel.blPage, pages))
	setEnabled(f.prev, Panel.blPage > 1)
	setEnabled(f.next, Panel.blPage < pages)
	if OV.Status.Alive(now) then f.bAdd:Enable(); f.bTarget:Enable() else f.bAdd:Disable(); f.bTarget:Disable() end
end

-- What the line under the sensitivity bar says: whether your voice is being sent right now. Pure: the model in, words and a colour out.
function Panel.GateNote(m)
	if not m.alive then return "", THEME.muted end
	if m.deaf then return "Deafened: nothing is sent.", THEME.muted end
	if m.mute then return "Muted: nothing is sent.", THEME.muted end
	if m.gate == "off" then return "Sensitivity off: all sound is sent" .. (m.mode == "ptt" and (" while you hold " .. m.key .. ".") or "."), THEME.muted end
	if not m.ghas then return "", THEME.muted end
	if m.gopen then
		if m.tx then return "Gate open: your voice is being sent.", PC.ok end
		if m.mode == "ptt" then return "Voice heard. Hold " .. m.key .. " to talk.", PC.warn end
		return "Gate open.", PC.ok
	end
	return "Below the bar: nothing is sent.", THEME.muted
end

-- The part of the panel that moves with the microphone: the one bar and the line under it. Drawn on its own when the live reading arrives.
local function applyMeter(f, m)
	local thr = (m.gate == "manual") and m.gdb or (m.gthr or m.gdb)
	local live = m.glive                                                    -- an app that does not send the gate's level (an older one): its plain level, on the same track
	if not live and m.lvl > 0 then live = Panel.DB_MIN + m.lvl / 100 * (Panel.DB_MAX - Panel.DB_MIN) end
	f.sGate.Set(m.alive and live or nil, OV.clamp(thr, Panel.GATE_MIN, Panel.GATE_MAX), m.gate, m.alive and m.ghas and m.gopen, m.loud)
	local note, noteColor = Panel.GateNote(m)
	f.gateNote:SetText(note)
	setColor(f.gateNote, noteColor)
end

local function applyModel(f, m)
	f.status:SetText(m.text .. (m.hint and ("\n" .. m.hint) or ""))
	setColor(f.status, (m.level == "ok" and THEME.text) or PC[m.level] or THEME.text)
	local dc = PC[m.level] or PC.off
	f.dot:SetVertexColor(dc[1], dc[2], dc[3], 1)
	setSelected(f.bOpen, m.mode == "open")
	setSelected(f.bPtt, m.mode == "ptt")
	-- mute and deafen: a normal deafen closes the microphone too, so both buttons go red; the other deafen leaves the microphone as it was
	local micShut = m.mute or (m.deaf and not m.dkeep)
	buttonText(f.bMute, m.mute and "Unmute" or "Mute")
	buttonText(f.bDeaf, (m.deaf and not m.dkeep) and "Undeafen" or "Deafen")
	setSelected(f.bMute, micShut, true)
	setSelected(f.bDeaf, m.deaf and not m.dkeep, true)
	setSelected(f.bDeafKeep, m.deaf and m.dkeep, false)                           -- gold: deafened, still heard
	if m.deaf and m.dkeep then
		f.bDeafKeep.icon:SetVertexColor(THEME.dark[1], THEME.dark[2], THEME.dark[3], 1)
	else
		f.bDeafKeep.icon:SetVertexColor(THEME.text[1], THEME.text[2], THEME.text[3], 1)
	end
	-- the key: what it does depends on the mode (hold to talk, or press to mute and unmute)
	if m.capturing then
		buttonText(f.bKey, "Press a key...")
		setSelected(f.bKey, true)
	else
		buttonText(f.bKey, "Keybind: " .. m.key)
		setSelected(f.bKey, false)
	end
	-- the sliders (a slider being dragged is left alone: the player's hand is what it shows)
	if not f.sIn.Busy() then f.sIn.Set(m.input); f.inText:SetText(f.sIn.format(m.input)) end
	if not f.sVol.Busy() then f.sVol.Set(m.vol); f.volText:SetText(f.sVol.format(m.vol)) end
	-- the one bar, in the order Manual, Auto, Off
	applyMeter(f, m)
	f.sGate.Enable(m.alive and m.gate == "manual")
	setSelected(f.bGManual, m.gate == "manual")
	setSelected(f.bGAuto, m.gate == "auto")
	setSelected(f.bGOff, m.gate == "off")
	-- the microphone test: red and "Stop Testing" while it runs (it runs on when this window is closed)
	buttonText(f.bTestMic, m.mt and "Stop Testing" or "Test Microphone")
	setSelected(f.bTestMic, m.mt, true)
	-- where voices are heard from
	for mode, b in pairs(f.bSpatial) do setSelected(b, m.sp == mode) end
	local function short(s) return string.len(s) > 46 and (string.sub(s, 1, 45) .. "...") or s end
	buttonText(f.bMicDev, short(m.micName))
	buttonText(f.bSpkDev, short(m.spkName))
	buttonText(f.bOverhead, "Overhead icons: " .. (Panel.OverheadOn() and "On" or "Off"))
	setSelected(f.bOverhead, Panel.OverheadOn())
	local list = { f.bOpen, f.bPtt, f.bMute, f.bDeaf, f.bDeafKeep, f.bKey, f.bSetup, f.bGManual, f.bGAuto, f.bGOff, f.bMicDev, f.bSpkDev, f.bReport, f.bTestMic }
	for i = 1, table.getn(list) do setEnabled(list[i], m.alive) end
	for mode, b in pairs(f.bSpatial) do setEnabled(b, m.alive) end
	f.sIn.Enable(m.alive); f.sVol.Enable(m.alive)
	for i = 1, Panel.ROWS do
		local r = m.rows[i]
		local row = f.rows[i]
		if r then
			f.rowName[i]:SetText(r.name)
			setColor(f.rowName[i], (r.muted or r.blocked) and PC.bad or (r.live and THEME.text or THEME.muted))
			f.rowInfo[i]:SetText(r.info)
			setColor(f.rowInfo[i], (r.muted or r.blocked) and PC.bad or (r.speaking and PC.talk or THEME.muted))
			row.who, row.state, row.muted = r.name, r.state, r.muted
			OV.Icons.Set(row.icon, OV.Icons.Enabled() and r.state or nil)
			if row.icon.state then OV.Icons.PlaceAfter(row.icon, row.nameText, 120) end        -- the icon is to the right of the name
		else
			f.rowName[i]:SetText("")
			f.rowInfo[i]:SetText("")
			row.who, row.state, row.muted = nil, nil, nil
			OV.Icons.Set(row.icon, nil)
		end
	end
	f.nobody2:SetText("")
	if m.nrows == 0 then
		f.nobody:SetText(m.alive and "Nobody in range is using EqVoice." or "")
		f.nobody2:SetText(m.alive and "Players near you who have EqVoice appear here." or "")
	elseif m.extra > 0 then
		f.nobody:SetText("")
		f.rowInfo[Panel.ROWS]:SetText(m.rows[Panel.ROWS].info .. "  (+" .. m.extra .. " more)")
	else
		f.nobody:SetText("")
	end
	buttonText(f.bBlock, m.nmuted > 0 and string.format("Blocklist (%d)", m.nmuted) or "Blocklist")
	f.muteNote:SetText("")
	if m.rpt then
		f.hint:SetText("Saved " .. m.rpt .. ". Drag it into your chat.")
		setColor(f.hint, PC.ok)
	else
		setColor(f.hint, THEME.muted)
		if m.alive and not m.cald then
			f.hint:SetText("Tip: press Calibrate once, so the sensitivity fits your voice.")
		else
			f.hint:SetText("Noise suppression and echo cancellation are always on.")
		end
	end
end

-- The indicator: a microphone button drawn like the client's micro-bar buttons. It sits to the right of the micro bar (wherever
-- the player or a UI addon put that bar) and shows the state by the colour of the microphone: green = live, cyan = talking,
-- yellow = needs a look, red = muted or deafened or broken (a slash over it for muted and deafened), grey = the app is not running.
-- If the micro bar is hidden it floats at the top of the screen instead. Shift-drag moves it for good; /eqvc reset docks it again.
local function glyphTexture(b, file, layer)
	local t = b:CreateTexture(nil, layer)
	t:SetTexture(Panel.ART .. file)
	t:SetWidth(20)
	t:SetHeight(20)
	t:SetPoint("CENTER", b, "CENTER", 0, -9)           -- the middle of the 40-pixel body, not of the 58-pixel frame
	return t
end

function Panel.BuildIndicator()
	if Panel.indicator then return Panel.indicator end
	if not CreateFrame or not UIParent then return nil end
	local b = CreateFrame("Button", "EqVoiceIndicator", UIParent)
	b:SetWidth(Panel.BTN_W)
	b:SetHeight(Panel.BTN_H)
	b:SetFrameStrata("MEDIUM")
	b:EnableMouse(true)
	b:SetMovable(true)
	b:SetClampedToScreen(true)
	if b.SetHitRectInsets then b:SetHitRectInsets(0, 0, 18, 0) end     -- only the visible body takes clicks, as with the real ones
	b:RegisterForClicks("LeftButtonUp", "RightButtonUp")
	b:RegisterForDrag("LeftButton")
	b:SetNormalTexture(Panel.ART .. "MicroUp.tga")
	b:SetPushedTexture(Panel.ART .. "MicroDown.tga")
	b:SetHighlightTexture("Interface\\Buttons\\ButtonHilight-Square")
	local hl = b:GetHighlightTexture()
	if hl and hl.SetBlendMode then hl:SetBlendMode("ADD") end
	b.glyph = glyphTexture(b, "MicGlyph.tga", "OVERLAY")
	b.slash = glyphTexture(b, "MicSlash.tga", "OVERLAY")
	b.slash:Hide()
	b.badgeText = ""
	local s = OV.db and OV.db.ui and OV.db.ui.indicatorPos
	if s and s.point then restorePos(b, "indicatorPos", "TOP", 0, -64) end        -- otherwise Panel.Dock places it
	b:SetScript("OnClick", safe(function()
		if arg1 == "RightButton" then Panel.ToggleMute() else Panel.Toggle() end
	end))
	b:SetScript("OnMouseDown", safe(function() this.glyph:SetPoint("CENTER", this, "CENTER", 1, -10); this.slash:SetPoint("CENTER", this, "CENTER", 1, -10) end))
	b:SetScript("OnMouseUp", safe(function() this.glyph:SetPoint("CENTER", this, "CENTER", 0, -9); this.slash:SetPoint("CENTER", this, "CENTER", 0, -9) end))
	-- Shift is needed to drag: in a row of buttons a stray drag would be a nuisance
	b:SetScript("OnDragStart", function()
		if IsShiftKeyDown and IsShiftKeyDown() then Panel.dragging = true; this:StartMoving() end
	end)
	b:SetScript("OnDragStop", safe(function()
		if not Panel.dragging then return end
		Panel.dragging = false
		this:StopMovingOrSizing()
		savePos(Panel.indicator, "indicatorPos")
	end))
	Panel.indicator = b
	return b
end

-- The right-most micro button on screen in the same row as the first one, or nil if the micro bar is not showing.
-- Looked up every time: UI addons gather the micro buttons into a bar of their own, move it, scale it or hide it.
function Panel.MicroTarget()
	local first = getglobal(Panel.MICRO[1])
	if not (first and first.IsVisible and first:IsVisible() and first.GetBottom and first.GetEffectiveScale) then return nil end
	local fb = first:GetBottom()
	if not fb then return nil end
	fb = fb * first:GetEffectiveScale()
	local best, bestRight
	for i = 1, table.getn(Panel.MICRO) do
		local b = getglobal(Panel.MICRO[i])
		if b and b.IsVisible and b:IsVisible() and b.GetRight and b.GetBottom and b.GetEffectiveScale then
			local r, bot = b:GetRight(), b:GetBottom()
			if r and bot then
				local sc = b:GetEffectiveScale()
				if math.abs(bot * sc - fb) < 12 and (not bestRight or r * sc > bestRight) then best, bestRight = b, r * sc end
			end
		end
	end
	return best
end

-- Put the indicator where it belongs: where the player dragged it, else beside the micro bar, else at the top of the screen.
function Panel.Dock()
	local b = Panel.indicator
	if not b then return end
	local s = OV.db and OV.db.ui and OV.db.ui.indicatorPos
	if s and s.point then return end
	local t = Panel.MicroTarget()
	local scale = 1
	if t and UIParent.GetEffectiveScale then scale = t:GetEffectiveScale() / UIParent:GetEffectiveScale() end
	if t == nil then t = false end
	if t == Panel.dockedTo and scale == Panel.dockedScale then return end
	Panel.dockedTo, Panel.dockedScale = t, scale
	b:ClearAllPoints()
	b:SetScale(scale)
	if t then
		b:SetPoint("BOTTOMLEFT", t, "BOTTOMRIGHT", Panel.DOCK_GAP, 0)
	else
		b:SetPoint("TOP", UIParent, "TOP", 0, -64)
	end
end

local function applyIndicator(b, m)
	b.badgeText = m.badge
	b.glyph:SetVertexColor(m.color[1], m.color[2], m.color[3])
	if m.alive and (m.mute or m.deaf) then
		b.slash:SetVertexColor(COLORS.bad[1], COLORS.bad[2], COLORS.bad[3])
		b.slash:Show()
	else
		b.slash:Hide()
	end
end

-- Redraw whatever is on screen from the current status.
-- The fast path: while a meter is on screen the status file is read twenty times a second (Status.POLL) and, when it brings news, only the
-- meter is redrawn (the whole panel is redrawn at the slower tick).
Panel.LIVE_S = 0.05

function Panel.Live(now)
	if not Panel.watching then return end
	if not OV.Status.Poll(now) then return end
	local m = Panel.Model(now)
	Panel.model = m
	if Panel.frame and Panel.frame:IsShown() then applyMeter(Panel.frame, m) end
	if OV.Wizard and OV.Wizard.Refresh then
		local ok, err = pcall(OV.Wizard.Refresh, m)
		if not ok then fail(err) end
	end
end

function Panel.BuildLive()
	if Panel.live or not CreateFrame or not UIParent then return end
	local f = CreateFrame("Frame", "EqVoicePanelLive", UIParent)
	local acc = 0
	f:SetScript("OnUpdate", safe(function()
		acc = acc + (arg1 or 0)
		if acc < Panel.LIVE_S then return end
		acc = 0
		Panel.Live(OV.Now())
	end))
	Panel.live = f
end

function Panel.Refresh()
	local now = OV.Now()
	Panel.lastRefresh, Panel.dirty = now, false
	Panel.seenVersion = OV.Status.version
	local m = Panel.Model(now)
	Panel.model = m
	Panel.TrackSetup(m)
	if Panel.indicator then applyIndicator(Panel.indicator, m) end
	if Panel.frame and Panel.frame:IsShown() then applyModel(Panel.frame, m) end
	if Panel.player and Panel.player:IsShown() then applyPlayer(Panel.player, m, now) end
	if Panel.blocklist and Panel.blocklist:IsShown() then applyBlocklist(Panel.blocklist, now) end
	if Panel.picker and Panel.picker:IsShown() and Panel.picker.kind then
		Panel.picker.lists[Panel.picker.kind].Refresh(m)
	end
	if m.alive or Panel.setup then
		local ok, err = pcall(applySetup, m)
		if not ok then fail(err) end
	end
	if OV.Wizard and OV.Wizard.Refresh then
		local ok, err = pcall(OV.Wizard.Refresh, m)
		if not ok then fail(err) end
	end
end

function Panel.Toggle()
	local ok, err = pcall(Panel.Build)
	if not ok or not Panel.frame then
		fail(err or "no frame")
		OV.Print("The panel could not be drawn. Use /eqvc help for the text commands.")
		return
	end
	if Panel.frame:IsShown() then Panel.frame:Hide() else Panel.frame:Show(); Panel.Refresh() end
end

-- Called once the character is known (Core.Start).
function Panel.Init()
	if not (OV.db and OV.db.ui) then return end
	local okLive, errLive = pcall(Panel.BuildLive)
	if not okLive then fail(errLive) end
	if OV.db.ui.hideIndicator then return end
	local ok, err = pcall(Panel.BuildIndicator)
	if not ok then fail(err) end
	ok, err = pcall(Panel.Dock)
	if not ok then fail(err) end
	ok, err = pcall(Panel.Refresh)          -- never show a bare button while waiting for the first tick
	if not ok then fail(err) end
	Panel.dirty = true
end

-- Called every Core tick (0.25 s): keeps the screen in step with the status, and the pending clicks honest.
function Panel.Tick(now)
	if now - Panel.lastDock >= Panel.DOCK_S then
		Panel.lastDock = now
		local ok, err = pcall(Panel.Dock)       -- the micro bar can be moved or hidden at any time
		if not ok then fail(err) end
	end
	if Panel.capturing and now >= Panel.captureEnds then Panel.StopCapture("key not changed (no key was pressed)") end
	for i = 1, table.getn(Panel.sliders) do                       -- a slider that has come to rest sends its value
		local ok, err = pcall(Panel.sliders[i].Flush, now)
		if not ok then fail(err) end
	end
	for k, v in pairs(Panel.pending) do
		if now >= v.untilT then Panel.pending[k] = nil end         -- an answer that never came: back to what the app says
	end
	local alive = OV.Status.Alive(now)
	if alive and not Panel.wasAlive then Panel.StartReplay(now) end         -- the app has just appeared: the game's settings are the ones in force
	Panel.wasAlive = alive
	Panel.StepReplay(now)
	-- a meter on screen asks the app for the microphone's level (it answers for a few seconds, then goes quiet by itself)
	local needMeter = alive and ((Panel.frame and Panel.frame:IsShown()) or (OV.Wizard and OV.Wizard.NeedsMeter and OV.Wizard.NeedsMeter()))
	OV.Status.POLL = needMeter and Panel.LIVE_S or 0.5                          -- a meter on screen reads the file at once, otherwise twice a second
	if needMeter and now - Panel.lastWatch >= Panel.WATCH_S then
		Panel.lastWatch, Panel.watching = now, true
		OV.Control.Send("watch", "1")
	elseif Panel.watching and not needMeter then
		Panel.watching = false
		if alive then OV.Control.Send("watch", "0") end
	end
	if OV.Wizard and OV.Wizard.Tick then
		local ok, err = pcall(OV.Wizard.Tick, now)
		if not ok then fail(err) end
	end
	local hasPending = false
	for _ in pairs(Panel.pending) do hasPending = true; break end
	if Panel.dirty or hasPending or OV.Status.version ~= Panel.seenVersion or now - Panel.lastRefresh >= 1 then
		local ok, err = pcall(Panel.Refresh)
		if not ok then fail(err) end
	end
end

function Panel.SetIndicator(on)
	OV.db.ui.hideIndicator = (not on) or nil
	if on then
		pcall(Panel.BuildIndicator)
		pcall(Panel.Dock)
		if Panel.indicator then Panel.indicator:Show() end
	elseif Panel.indicator then
		Panel.indicator:Hide()
	end
	Panel.dirty = true
end

function Panel.ResetPositions()
	if OV.db and OV.db.ui then OV.db.ui.panelPos, OV.db.ui.indicatorPos, OV.db.ui.setupPos, OV.db.ui.blocklistPos = nil, nil, nil, nil end
	if Panel.frame then restorePos(Panel.frame, "panelPos", "CENTER", 0, 40) end
	if Panel.setup then restorePos(Panel.setup, "setupPos", "TOP", 0, -150) end
	if OV.Wizard and OV.Wizard.ResetPosition then OV.Wizard.ResetPosition() end
	if Panel.blocklist then restorePos(Panel.blocklist, "blocklistPos", "CENTER", 260, 40) end
	Panel.dockedTo, Panel.dockedScale = nil, nil            -- forces Dock to place it again
	pcall(Panel.Dock)
end

-- ------------------------------------------------------------------- commands

local function helpText()
	OV.Print("/vc  /eqvc  /eqvoice   open or close the voice panel")
	OV.Print("/eqvc open     open mic: you transmit all the time (the key then mutes and unmutes)        /eqvc ptt   push to talk (hold the key)")
	OV.Print("/eqvc mute, unmute, deafen, deafen mic, undeafen      /eqvc vol 0-100   how loud everyone is      /eqvc input 0-200   microphone boost")
	OV.Print("/eqvc setup   the whole first-time setup again      /eqvc calibrate   only the microphone calibration (silent, talk, shout)")
	OV.Print("/eqvc mictest   hear your own microphone (nobody else does) until you run it again      /eqvc key   change the key")
	OV.Print("/eqvc sound mono, speakers or headphones   where voices are heard from      /eqvc overhead on or off   icons over players' heads")
	OV.Print("/eqvc mute <name>, unmute <name>, mute target   add or take someone off your blocklist      /eqvc muted   list it      /eqvc blocklist   open it")
	OV.Print("/eqvc gate auto, manual or off   the noise gate      /eqvc gate -50   the manual bar in dB (-80 very sensitive .. -20)")
	OV.Print("/eqvc icons on or off   the little voice icons beside player names (click one on a unit frame to open this panel)")
	OV.Print("/eqvc status   what the app says          /eqvc show or hide   the small indicator      /eqvc reset   put the windows back")
end

local function printStatus()
	local now = OV.Now()
	local m = Panel.Model(now)
	OV.Print(m.text)
	if m.alive then
		OV.Print(string.format("mic: %s (%s)   volume %d%%   boost %d%%   sound %s   noise gate %s%s   near %d, connected %d%s%s", m.mode == "open" and "open (key " .. m.key .. " mutes)" or ("push to talk, key " .. m.key),
			m.mic, m.vol, m.input, m.sp, m.gate, m.gate == "manual" and string.format(" (%d dB)", m.gdb) or "", m.near, m.conn, m.mute and "   MUTED" or "", m.deaf and "   DEAFENED" or ""))
	elseif m.hint then
		OV.Print(m.hint)
	end
end

-- The player a /eqvc mute or unmute is about: a name, or "target".
local function whoIs(word, original)
	if word == "target" then
		local name = UnitName and UnitName("target") or nil
		if type(name) == "string" and name ~= "" and (not UnitIsPlayer or UnitIsPlayer("target")) then return name end
		return nil, "your target is not a player"
	end
	return original
end

local function printMuted()
	local names, n = Panel.BlockedNames(OV.Now())
	if n == 0 then OV.Print("Your blocklist is empty. /eqvc mute <name> adds someone.")
	else
		for i = 1, n do names[i] = Panel.ShowName(names[i]) end
		OV.Print("Blocked players (" .. n .. "): " .. table.concat(names, ", "))
	end
end

function Panel.Slash(msg)
	if not OV.started or not OV.db then
		OV.Print("not ready yet (log in to the world first)")
		return
	end
	local raw = msg or ""
	msg = string.lower(raw)
	local w, n = OV.split(msg, " ")
	local rw = OV.split(raw, " ")
	local cmd = w[1] or ""
	if cmd == "" then
		Panel.Toggle()
	elseif cmd == "open" or cmd == "openmic" or cmd == "open-mic" then
		if Panel.SetMode("open") then OV.Print("open mic: your microphone is live whenever you are not muted") end
	elseif cmd == "ptt" or cmd == "push" then
		local key = OV.Status.fields.key or "F9"
		if Panel.SetMode("ptt") then OV.Print("push to talk: hold " .. string.gsub(key, "_", " ") .. " to speak") end
	elseif cmd == "mute" and w[2] then
		local who, why = whoIs(w[2], rw[2])
		if who then
			if Panel.SetPlayerMute(who, true) then OV.Print("muted " .. who .. ": you will not hear them (/eqvc unmute " .. who .. " undoes it)") end
		else OV.Print(why) end
	elseif cmd == "unmute" and w[2] then
		local who, why = whoIs(w[2], rw[2])
		if who then
			if Panel.SetPlayerMute(who, false) then OV.Print("unmuted " .. who) end
		else OV.Print(why) end
	elseif cmd == "mute" then
		if Panel.SetMute(true) then OV.Print("muted") end
	elseif cmd == "unmute" then
		if Panel.SetMute(false) then OV.Print("unmuted") end
	elseif cmd == "muted" or cmd == "mutelist" then
		printMuted()
	elseif cmd == "blocklist" or cmd == "blocked" or cmd == "block" then
		Panel.ToggleBlocklist()
	elseif cmd == "deafen" and w[2] == "mic" then
		if Panel.SetDeafKeep(true) then OV.Print("deafened, microphone open: you hear nobody; they still hear you") end
	elseif cmd == "deafen" then
		if Panel.SetDeaf(true) then OV.Print("deafened: you hear nobody and your microphone is closed") end
	elseif cmd == "undeafen" then
		if Panel.SetDeaf(false) then OV.Print("undeafened") end
	elseif cmd == "key" then
		Panel.CaptureKey()
	elseif cmd == "setup" or cmd == "welcome" or cmd == "wizard" or cmd == "firstrun" then
		if OV.Wizard then OV.Wizard.Show(1) else OV.Print("The setup box could not be drawn.") end       -- the whole first-time setup again, from its first screen
	elseif cmd == "calibrate" then
		if Panel.StartSetup() then OV.Print("calibrating: stay silent, then talk normally, then shout. About 17 seconds.") end
	elseif cmd == "mictest" or cmd == "testmic" then
		local m = Panel.Model(OV.Now())
		if Panel.SetMicTest(not m.mt) then OV.Print(m.mt and "microphone test stopped" or "testing your microphone: you hear yourself, nobody else does. /eqvc mictest stops it") end
	elseif cmd == "sound" or cmd == "spatial" then
		if w[2] and Panel.SPATIAL_LABEL[w[2]] then
			if Panel.SetSpatial(w[2]) then OV.Print("sound: " .. Panel.SPATIAL_LABEL[w[2]]) end
		else OV.Print("usage: /eqvc sound mono, speakers or headphones   (now " .. Panel.Model(OV.Now()).sp .. ")") end
	elseif cmd == "overhead" then
		if w[2] == "on" or w[2] == "off" then Panel.SetOverhead(w[2] == "on"); OV.Print("icons over players' heads: " .. w[2])
		else OV.Print("usage: /eqvc overhead on or off   (now " .. (Panel.OverheadOn() and "on" or "off") .. ")") end
	elseif cmd == "vol" and w[2] and w[3] and false then
		return
	elseif cmd == "vol" or cmd == "volume" then
		local v = tonumber(w[2] or "")
		if v then
			if Panel.SetVolume(v) then OV.Print("volume " .. string.format("%d", OV.clamp(math.floor(v + 0.5), 0, 100)) .. "%") end
		else
			OV.Print("usage: /eqvc vol 0-100   (now " .. string.format("%d", Panel.Model(OV.Now()).vol) .. "%)")
		end
	elseif cmd == "input" or cmd == "mic" then
		local v = tonumber(w[2] or "")
		if v then
			if Panel.SetInput(v) then OV.Print("microphone boost " .. string.format("%d", OV.clamp(math.floor(v + 0.5), 0, 200)) .. "%") end
		else
			OV.Print("usage: /eqvc input 0-200   (now " .. string.format("%d", Panel.Model(OV.Now()).input) .. "%, 100 = as your microphone gives it)")
		end
	elseif cmd == "gate" then
		local want = w[2]
		local db = tonumber(want or "")
		if want and Panel.GATE_LABEL[want] then
			if Panel.SetGate(want) then OV.Print("noise gate: " .. Panel.GATE_LABEL[want]) end
		elseif db then
			if Panel.SetGate("manual") and Panel.SetGateDb(db) then OV.Print("noise gate: manual, " .. string.format("%d", OV.clamp(math.floor(db / Panel.GATE_STEP + 0.5) * Panel.GATE_STEP, Panel.GATE_MIN, Panel.GATE_MAX)) .. " dB") end
		else
			local m = Panel.Model(OV.Now())
			OV.Print("usage: /eqvc gate auto, manual or off, or /eqvc gate -50   (now " .. m.gate .. (m.gate == "manual" and string.format(", %d dB", m.gdb) or "") .. ")")
		end
	elseif cmd == "icons" then
		if w[2] == "on" or w[2] == "off" then
			OV.Icons.SetEnabled(w[2] == "on")
			OV.Print("voice icons beside names: " .. w[2])
		else
			OV.Print("usage: /eqvc icons on or off   (now " .. (OV.Icons.Enabled() and "on" or "off") .. ")")
		end
	elseif cmd == "status" then
		printStatus()
	elseif cmd == "show" then
		Panel.SetIndicator(true)
	elseif cmd == "hide" then
		Panel.SetIndicator(false)
		OV.Print("indicator hidden; /eqvc show brings it back")
	elseif cmd == "reset" then
		Panel.ResetPositions()
		OV.Print("windows put back")
	else
		helpText()
	end
end
