-- EqVoice / Icons.lua
-- The voice icon for every player who has EqVoice: big, centred ABOVE the name on their nameplate; small, beside the name on the target, player
-- and party frames and in the panel's list. It shows what that player's EqVoice is doing right now:
--
--   o  idle                  vol_low   a plain speaker: they are on the voice channel and could talk, but no voice is coming from them   VoiceKnown.tga
--   s  speaking              vol_mid   a speaker with one wave: ordinary conversational speech                                       VoiceOpen.tga
--   l  loud                  vol_high  a speaker with waves: a raised voice (it also carries further)                               VoiceSpeaking.tga
--   m  muted                 a microphone with a slash. Also push to talk while the key is not held: the microphone is closed
--                            either way, and from outside it looks the same   VoiceMuted.tga
--   b  muted by you          the same, a little dimmer: you put them on your blocklist
--   d  deafened              headphones with a slash (deafened also means muted)   VoiceDeafened.tga
--   i  has EqVoice           the plain speaker, dimmed: they are known to have it, but nothing more is known (not connected to them yet,
--                            or their app is not running)
--
-- Only your own icon also has these, drawn in red because something needs a look:
--   x  not connected         your EqVoice program is not running, or it cannot reach the voice server (after a few seconds of trying)
--   e  microphone problem    your microphone cannot be used (Windows says no, or there is none)
--
-- The pictures are the ones Ollie chose (tools/art/voice-icons/*.png), made into 32 x 32 textures by tools/make_micro_art.py.
--
-- Where the state comes from, best first:
--   - the app's status file (Status.byName): the other player's own EqVoice told ours, over their voice connection, so it is
--     exact and quick, and it is the only source for "speaking" (heard as they talk);
--   - the presence heartbeat the addon already sends (Comm.lua): its flags say muted / deafened / app running. It is slow (a few
--     seconds, quicker right after a change) and has nothing about speaking, but it works for anyone with the addon in range;
--   - for yourself, the panel's own model (a click shows at once).
--
-- Where the name is drawn: the client's own unit frames keep their name in `PlayerName`, `TargetName` and `PartyMemberFrameNName`,
-- but the Equadis overhaul (ECO) hides the client's nameplate texts and draws its own, and gives its party frames a left-aligned name
-- of their own. The icon goes beside the text the player can SEE: ECO's when ECO has one for that plate or frame, else the client's.
-- Whatever the text's alignment (centred, left, right), the icon goes just after the end of the words.
--
-- The icons on the unit frames are buttons: a click on another player's icon opens that player's settings (volume, mute, block); a click
-- on your own opens the EqVoice panel. (Nameplates are only drawn on, so a click there still targets the player.)
--
-- Over a player's head. The names the game floats over characters are drawn in the 3D world: no addon can draw on them or learn where
-- on the screen they are (this client and its mods have no way to turn a place in the world into a place on the screen). The one thing
-- that does sit over a character's head and can be drawn on is a nameplate. So while a voice player is near and "overhead icons" is on,
-- EqVoice switches friendly nameplates on itself, and makes the ones it caused QUIET: no health bar, no border, no level, not clickable,
-- only the name, with the voice icon above it. A player who has friendly nameplates on already (Shift-V) keeps them exactly as they
-- are and simply gets the icon. Switching "overhead icons" off (the panel, or /eqvc overhead off) puts everything back.
--
-- Written for the 1.12 client like Panel.lua: Lua 5.0, nothing newer. Every drawing step is inside a pcall, and a surface that
-- keeps failing is switched off for the session: an icon must never be able to stop the voice. /eqvc icons off removes them.

local OV = EqVoice
local Icons = {}
OV.Icons = Icons

Icons.ART    = "Interface\\AddOns\\EqVoice\\"
Icons.SIZE   = 16
Icons.PLATE_SIZE = 32          -- over a nameplate: big enough to read at a glance from across the screen
Icons.PLATE_GAP  = 2           -- pixels between the icon and the top of the name
Icons.GAP    = 3               -- pixels between the end of a name and its icon
Icons.EVERY  = 0.25            -- seconds between looks at the screen
Icons.MAXERR = 3               -- a surface that fails this many times is switched off
Icons.RED = { 0.95, 0.35, 0.35 }
Icons.LOOK = {
	o = { file = "VoiceKnown.tga",    word = "using EqVoice, not talking" },                    -- vol_low
	s = { file = "VoiceOpen.tga",     word = "speaking" },                                                 -- vol_mid
	l = { file = "VoiceSpeaking.tga", word = "speaking loudly" },                                          -- vol_high
	m = { file = "VoiceMuted.tga",    word = "muted" },
	b = { file = "VoiceMuted.tga",    word = "muted by you", dim = 0.7 },
	d = { file = "VoiceDeafened.tga", word = "deafened" },
	i = { file = "VoiceKnown.tga",    word = "has EqVoice", dim = 0.45 },
	x = { file = "VoiceKnown.tga",    word = "EqVoice is not connected", tint = Icons.RED },
	e = { file = "VoiceMuted.tga",    word = "your microphone cannot be used", tint = Icons.RED },
}

-- The names of the client's own unit-frame name texts that get an icon (the target, you, your party).
Icons.UNIT_NAMES = { "TargetName", "PlayerName", "PartyMemberFrame1Name", "PartyMemberFrame2Name",
	"PartyMemberFrame3Name", "PartyMemberFrame4Name" }
Icons.ECO_PARTY = 4            -- ECO's party frames draw their own names: one more surface for each
local PLATE_BORDER = "Interface\\Tooltips\\Nameplate-Border"

Icons.forced  = false          -- friendly nameplates are on because EqVoice switched them on (and only then are plates made quiet)
Icons.assertAt = -100          -- when they were last switched on (it is repeated: another addon may switch them off)
Icons.needUntil = -100         -- overhead icons are wanted until then (a voice player was near a moment ago)
Icons.NEED_HOLD = 10           -- seconds the plates stay after the last voice player left (no flicker at the edge of range)
Icons.REASSERT = 5
Icons.surf    = {}             -- the unit-frame names: index -> { icon }
Icons.plates  = {}             -- nameplates found so far: { frame, name (the client's own FontString), icon }
Icons.known   = {}             -- WorldFrame children already looked at
Icons.nKids   = nil
Icons.lastRun = -100
Icons.errors  = { units = 0, plates = 0 }
Icons.error   = nil

-- ------------------------------------------------------------------ what to show (pure: no frames)

local function hasBit(v, bit)
	return OV.mod(math.floor((v or 0) / bit), 2) == 1
end

-- Your own state, from the panel's model, so that a click shows at once. What needs a look comes first: not connected, then a microphone
-- that cannot be used; then what you chose (deafened, muted, push to talk with the key up); then how you sound.
function Icons.SelfState(now)
	local m = OV.Panel and OV.Panel.Model and OV.Panel.Model(now) or nil
	if not m then return nil end
	if not m.alive or m.disconnected then return "x" end
	if m.micBad then return "e" end
	if m.deaf then return "d" end
	if m.mute then return "m" end
	if m.mode == "ptt" and not m.tx then return "m" end
	if m.voice >= 2 then return "l" end
	if m.voice == 1 or m.talk then return "s" end
	return "o"
end

-- The state letter to show beside `name`, or nil when nothing is known (not an EqVoice player as far as we can tell).
function Icons.Resolve(name, now)
	if type(name) ~= "string" or name == "" then return nil end
	if name == OV.character then return Icons.SelfState(now) end
	if OV.Panel and OV.Panel.IsPlayerMuted and OV.Panel.IsPlayerMuted(name, now) then return "b" end       -- whatever they are doing, you are not hearing it
	local S = OV.Status
	local key = string.lower(name)
	local alive = S.Alive(now)
	local row = alive and S.byName[key] or nil
	if row and row.state ~= "-" then return row.state end
	-- no word from their own EqVoice: what their heartbeat says, if we have heard one
	local nb = OV.nearby
	local sid = nb and nb.bySender[name] or nil
	local e = sid and nb.entries[sid] or nil
	if e then
		local fl = e.flags or 0
		if hasBit(fl, OV.Proto.FLAG_DEAF) then return "d" end
		if hasBit(fl, OV.Proto.FLAG_MUTED) then return "m" end
		if hasBit(fl, OV.Proto.FLAG_APP) then return "o" end
		return "i"
	end
	if row then return "i" end
	return nil
end

function Icons.Describe(state)
	local look = state and Icons.LOOK[state]
	return look and look.word or nil
end

function Icons.Enabled()
	return not (OV.db and OV.db.ui and OV.db.ui.icons == false)
end

-- ----------------------------------------------------------------------- drawing

-- An icon is one texture on `parent`; with `clickable` it is a small button holding that texture. A click on another player's icon opens
-- that player's settings; on your own (or one with no name) it opens the panel.
function Icons.New(parent, size, clickable)
	size = size or Icons.SIZE
	local ic = { state = nil, size = size, who = nil }
	if clickable then
		local b = CreateFrame("Button", nil, parent)
		b:SetWidth(size)
		b:SetHeight(size)
		if b.SetFrameLevel and parent.GetFrameLevel then b:SetFrameLevel((parent:GetFrameLevel() or 1) + 3) end
		b:EnableMouse(true)
		b:RegisterForClicks("LeftButtonUp")
		ic.btn = b
		ic.tex = b:CreateTexture(nil, "OVERLAY")
		ic.tex:SetAllPoints(b)
		b:SetScript("OnClick", function()
			local ok, err = pcall(function()
				if not OV.Panel then return end
				if ic.who and ic.who ~= OV.character and OV.Panel.OpenPlayer then OV.Panel.OpenPlayer(ic.who) else OV.Panel.Toggle() end
			end)
			if not ok then OV.Trace("icon click error: " .. tostring(err)) end
		end)
		b:Hide()
	else
		ic.tex = parent:CreateTexture(nil, "OVERLAY")
		ic.tex:SetWidth(size)
		ic.tex:SetHeight(size)
	end
	ic.tex:Hide()
	return ic
end

function Icons.Set(ic, state)
	if ic.state == state then return end
	ic.state = state
	local look = state and Icons.LOOK[state]
	if not look then
		ic.tex:Hide()
		if ic.btn then ic.btn:Hide() end
		return
	end
	ic.tex:SetTexture(Icons.ART .. look.file)
	if look.tint then ic.tex:SetVertexColor(look.tint[1], look.tint[2], look.tint[3]) else ic.tex:SetVertexColor(1, 1, 1) end
	if ic.tex.SetAlpha then ic.tex:SetAlpha(look.dim or 1) end
	if ic.btn then ic.btn:Show() end
	ic.tex:Show()
end

local function textWidth(fs)
	local w = 0
	if fs.GetStringWidth then
		local ok, v = pcall(fs.GetStringWidth, fs)
		if ok and type(v) == "number" then w = v end
	end
	return w
end

-- How the words sit in their box: "LEFT", "RIGHT" or "CENTER" (what the client does when nothing was set).
local function justifyOf(fs)
	if fs.GetJustifyH then
		local ok, v = pcall(fs.GetJustifyH, fs)
		if ok and (v == "LEFT" or v == "RIGHT" or v == "CENTER") then return v end
	end
	return "CENTER"
end

local function anchor(ic, point, rel, relPoint, x, y)
	local o = ic.btn or ic.tex
	if o.ClearAllPoints then o:ClearAllPoints() end
	o:SetPoint(point, rel, relPoint, x, y)
end

-- Just after the last letter of the name in `fs`, wherever the words sit in their box. `maxWidth` stops a long name pushing the
-- icon past the end of a box that cuts it off (the panel's rows); a box with a width of its own does the same.
function Icons.PlaceBeside(ic, fs, maxWidth)
	local w = textWidth(fs)
	if maxWidth and w > maxWidth then w = maxWidth end
	if fs.GetWidth then
		local ok, bw = pcall(fs.GetWidth, fs)
		if ok and type(bw) == "number" and bw > 0 and w > bw then w = bw end
	end
	local j = justifyOf(fs)
	if ic.placedFor == fs and ic.placedW == w and ic.placedJ == j then return end
	ic.placedFor, ic.placedW, ic.placedJ = fs, w, j
	if j == "LEFT" then
		anchor(ic, "LEFT", fs, "LEFT", w + Icons.GAP, 0)
	elseif j == "RIGHT" then
		anchor(ic, "LEFT", fs, "RIGHT", Icons.GAP, 0)
	else
		anchor(ic, "LEFT", fs, "CENTER", (w / 2) + Icons.GAP, 0)
	end
end

-- Centred over the name, its bottom edge just above the top of the words (the nameplates).
function Icons.PlaceAbove(ic, fs)
	if ic.placedFor == fs and ic.placedJ == "ABOVE" then return end
	ic.placedFor, ic.placedW, ic.placedJ = fs, nil, "ABOVE"
	anchor(ic, "BOTTOM", fs, "TOP", 0, Icons.PLATE_GAP)
end
Icons.PlaceRightOf = Icons.PlaceBeside
Icons.PlaceAfter   = Icons.PlaceBeside

local function fail(group, err)
	Icons.errors[group] = (Icons.errors[group] or 0) + 1
	if Icons.error ~= tostring(err) then
		Icons.error = tostring(err)
		OV.Trace("icons error (" .. group .. "): " .. tostring(err))
	end
end

-- One of ECO's modules ("nameplates", "partyframes"), or nil when ECO is not installed.
local function eco(id)
	local OB = getglobal and getglobal("EquadisClassicOverhaul") or nil
	local mods = OB and OB.modules or nil
	return mods and mods[id] or nil
end

local function visible(w)
	if w and w.IsVisible then
		local ok, v = pcall(w.IsVisible, w)
		if ok and not v then return false end
	end
	return w ~= nil
end

-- Is this text on screen? (A text whose frame is hidden, or that has itself been hidden, is not a place for an icon.)
local function showing(fs)
	if not visible(fs) then return false end
	local parent = fs.GetParent and fs:GetParent() or nil
	if parent and not visible(parent) then return false end
	return true
end

-- Surface number i: the client's own name texts first, then the names ECO's party frames draw.
local function surfaceText(i)
	local n = table.getn(Icons.UNIT_NAMES)
	if i <= n then return getglobal(Icons.UNIT_NAMES[i]) end
	local pf = eco("partyframes")
	local f = pf and pf.frames and pf.frames[i - n] or nil
	return f and f.nameText or nil
end

local function unitFrames(now, on)
	local total = table.getn(Icons.UNIT_NAMES) + Icons.ECO_PARTY
	for i = 1, total do
		local fs = surfaceText(i)
		if fs and fs.GetText then
			local s = Icons.surf[i]
			if not s then
				local parent = fs.GetParent and fs:GetParent() or nil
				if parent and parent.CreateTexture then
					s = { icon = Icons.New(parent, nil, true) }
					Icons.surf[i] = s
				end
			end
			if s then
				local name = fs:GetText()
				local state = on and showing(fs) and Icons.Resolve(name, now) or nil
				s.icon.who = state and name or nil                 -- whose icon this is now: a click opens that player's settings
				Icons.Set(s.icon, state)
				if state then Icons.PlaceBeside(s.icon, fs) end
			end
		end
	end
end

-- ----------------------------------------------------------------------- over a player's head

function Icons.OverheadOn()
	return not (OV.db and OV.db.ui and OV.db.ui.overhead == false)
end

-- A nameplate is a child of WorldFrame whose first region is the border picture and whose third is the name.
-- (The six regions, in order: border, glow, name, level, the skull that stands for a level, the raid mark. Its first child is the health bar.)
local function findPlates()
	if not (WorldFrame and WorldFrame.GetChildren) then return end
	local n = nil
	if WorldFrame.GetNumChildren then n = WorldFrame:GetNumChildren() end
	if n ~= nil and n == Icons.nKids then return end            -- nothing new since last time
	Icons.nKids = n
	local kids = { WorldFrame:GetChildren() }
	local i = 1
	while kids[i] do
		local f = kids[i]
		if not Icons.known[f] then
			Icons.known[f] = true
			local ok, regions = pcall(function() return { f:GetRegions() } end)
			if ok and regions[1] and regions[3] and regions[1].GetTexture and regions[3].GetText then
				local okT, tex = pcall(regions[1].GetTexture, regions[1])
				if okT and tex == PLATE_BORDER then
					Icons.plates[table.getn(Icons.plates) + 1] = { frame = f, name = regions[3], regions = regions }
				end
			end
		end
		i = i + 1
	end
end

local function plateBar(p)
	if p.bar == nil then
		p.bar = false
		if p.frame.GetChildren then
			local ok, kid = pcall(p.frame.GetChildren, p.frame)
			if ok and kid and kid.GetStatusBarColor then p.bar = kid end
		end
	end
	return p.bar or nil
end

-- "player", "npc" or nil: a friendly unit's plate has a blue (player) or green (NPC) health bar; hostile is red, neutral yellow.
local function friendKind(p)
	local bar = plateBar(p)
	if not bar then return nil end
	local ok, r, g, b = pcall(bar.GetStatusBarColor, bar)
	if not ok or type(r) ~= "number" then return nil end
	if r < 0.2 and g < 0.2 and b > 0.9 then return "player" end
	if r < 0.2 and g > 0.9 and b < 0.2 then return "npc" end
	return nil
end

local QUIET = { 1, 2, 4, 5 }       -- border, glow, level, skull: what a quiet plate does not show (the name, 3, and the raid mark, 6, stay)

-- A quiet plate: nothing but the name. The client can put the art back when it reuses a plate, so it is said again every half second.
local function setQuiet(p, on, now)
	if (p.quiet or false) == on and (not on or now - (p.quietAt or -100) < 0.5) then return end
	p.quiet, p.quietAt = on, now
	local a = on and 0 or 1
	for k = 1, table.getn(QUIET) do
		local r = p.regions and p.regions[QUIET[k]]
		if r and r.SetAlpha then r:SetAlpha(a) end
	end
	local bar = plateBar(p)
	if bar and bar.SetAlpha then bar:SetAlpha(a) end
	if p.frame.EnableMouse then p.frame:EnableMouse(not on) end        -- a quiet plate is not in the way of a click on the world
end

-- A name of our own on a plate whose own name another addon (ECO) has taken away without drawing one.
local function setLabel(p, text, kind)
	if not text or text == "" then
		if p.label then p.label:Hide(); p.labelOn = false end
		return
	end
	if not p.label then
		p.label = p.frame:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
		local ui = OV.Panel and OV.Panel.UI
		if ui and ui.styleText then ui.styleText(p.label, 12, ui.FONT_SEMI) end
		p.label:SetPoint("CENTER", p.frame, "CENTER", 0, 0)
	end
	if kind == "npc" then p.label:SetTextColor(0.6, 1.0, 0.4) else p.label:SetTextColor(0.5, 0.66, 1.0) end
	p.label:SetText(text)
	p.label:Show()
	p.labelOn = true
end

-- Every plate on screen, while EqVoice is the reason friendly plates are on: the friendly ones are made quiet. ECO draws plates itself:
-- its plates are left to it, and one it chose not to draw (friendly players switched off in ECO) gets a name from here so nobody goes nameless.
local function quietPass(now)
	findPlates()
	local np = eco("nameplates")
	for i = 1, table.getn(Icons.plates) do
		local p = Icons.plates[i]
		if visible(p.frame) then
			local kind = Icons.forced and friendKind(p) or nil
			local ep = np and np.plates and np.plates[p.frame] or nil
			if ep then
				setQuiet(p, false, now)
				local undrawn = kind and ep.overlay and not visible(ep.overlay)
				setLabel(p, undrawn and p.name:GetText() or nil, kind)
			else
				setQuiet(p, kind ~= nil, now)
				setLabel(p, nil)
			end
		end
	end
end
Icons.QuietPass = quietPass

local function release(now)
	Icons.forced = false
	for i = 1, table.getn(Icons.plates) do
		setQuiet(Icons.plates[i], false, now)
		setLabel(Icons.plates[i], nil)
	end
	if not FRIENDNAMEPLATES_ON and type(HideFriendNameplates) == "function" then HideFriendNameplates() end
end

local function buildDriver()
	if Icons.driver or not CreateFrame then return end
	local f = CreateFrame("Frame", "EqVoicePlateDriver")
	f:SetScript("OnUpdate", function()                      -- every frame while plates are ours: a plate that appears is quiet at once, not a quarter second later
		if not Icons.forced then return end
		local ok, err = pcall(quietPass, OV.Now())
		if not ok then fail("plates", err) end
	end)
	Icons.driver = f
end

-- Friendly nameplates on or off, as the overhead icons need them. The player's own switch (Shift-V sets FRIENDNAMEPLATES_ON) always wins:
-- while it is on, nothing is forced and nothing is made quiet.
local function ensurePlates(now, on)
	local alive = OV.Status.Alive(now)
	if alive and (OV.Status.npeers or 0) > 0 then Icons.needUntil = now + Icons.NEED_HOLD end
	local want = on and Icons.OverheadOn() and alive and now < Icons.needUntil
	if FRIENDNAMEPLATES_ON then
		if Icons.forced then                                  -- the player switched their own on: hand the plates back as they are
			Icons.forced = false
			for i = 1, table.getn(Icons.plates) do
				setQuiet(Icons.plates[i], false, now)
				setLabel(Icons.plates[i], nil)
			end
		end
		return
	end
	if want then
		if type(ShowFriendNameplates) == "function" and (not Icons.forced or now - Icons.assertAt >= Icons.REASSERT) then
			ShowFriendNameplates()
			Icons.forced, Icons.assertAt = true, now
			buildDriver()
		end
	elseif Icons.forced then
		release(now)
	end
end

-- The name text of a plate that the player sees: our own label when we had to draw one, ECO's when ECO styles this plate (it hides
-- the client's), else the client's.
local function plateText(p)
	if p.labelOn and p.label then return p.label end
	local np = eco("nameplates")
	local plate = np and np.plates and np.plates[p.frame] or nil
	if plate and plate.name and plate.name.GetText then return plate.name end
	return p.name
end

local function plates(now, on)
	ensurePlates(now, on)
	findPlates()
	local show = on and Icons.OverheadOn()
	for i = 1, table.getn(Icons.plates) do
		local p = Icons.plates[i]
		local fs = plateText(p)
		local state = nil
		if show and visible(p.frame) then state = Icons.Resolve(fs:GetText(), now) end
		if state and not p.icon then p.icon = Icons.New(p.frame, Icons.PLATE_SIZE) end
		if p.icon then
			Icons.Set(p.icon, state)
			if state then Icons.PlaceAbove(p.icon, fs) end
		end
	end
end

-- Called from the addon's tick (every 0.25 s); looks at the screen every Icons.EVERY seconds.
function Icons.Tick(now)
	if now - Icons.lastRun < Icons.EVERY then return end
	Icons.lastRun = now
	local on = Icons.Enabled()
	if Icons.errors.units < Icons.MAXERR then
		local ok, err = pcall(unitFrames, now, on)
		if not ok then fail("units", err) end
	end
	if Icons.errors.plates < Icons.MAXERR then
		local ok, err = pcall(plates, now, on)
		if not ok then fail("plates", err) end
	end
end

function Icons.SetEnabled(on)
	if on then OV.db.ui.icons = nil else OV.db.ui.icons = false end
	Icons.lastRun = -100
	Icons.Tick(OV.Now())
end

-- The icons over players' heads, on or off (kept between sessions). Off also hands friendly nameplates back at once.
function Icons.SetOverhead(on)
	if on then OV.db.ui.overhead = nil else OV.db.ui.overhead = false end
	Icons.lastRun = -100
	Icons.Tick(OV.Now())
end
