-- EqVoice / Icons.lua
-- The little voice icon to the right of a player's name, for every player who has EqVoice: on their nameplate, on the target, player and
-- party frames, and in the panel's list. It shows what that player's EqVoice is doing right now:
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
-- The pictures are the ones Ollie chose (tools/art/voice-icons/*.png), made into 16 x 16 textures by tools/make_micro_art.py.
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
-- The icons on the unit frames are buttons: a click opens the EqVoice panel. (Nameplates are only drawn on, so a click there still
-- targets the player.)
--
-- Written for the 1.12 client like Panel.lua: Lua 5.0, nothing newer. Every drawing step is inside a pcall, and a surface that
-- keeps failing is switched off for the session: an icon must never be able to stop the voice. /eqvc icons off removes them.

local OV = EqVoice
local Icons = {}
OV.Icons = Icons

Icons.ART    = "Interface\\AddOns\\EqVoice\\"
Icons.SIZE   = 16
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

-- An icon is one texture on `parent`; with `clickable` it is a small button holding that texture, and a click opens the panel.
function Icons.New(parent, size, clickable)
	size = size or Icons.SIZE
	local ic = { state = nil, size = size }
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
			local ok, err = pcall(function() if OV.Panel then OV.Panel.Toggle() end end)
			if not ok then OV.Trace("icon click error: " .. tostring(err)) end
		end)
		b:SetScript("OnEnter", function()
			pcall(function()
				GameTooltip:SetOwner(ic.btn, "ANCHOR_RIGHT")
				GameTooltip:SetText("EqVoice", 1, 1, 1)
				local word = Icons.Describe(ic.state)
				if word then GameTooltip:AddLine(word, 0.85, 0.85, 0.85, 1) end
				GameTooltip:AddLine("Click: open the EqVoice settings.", 0.6, 0.6, 0.6, 1)
				GameTooltip:Show()
			end)
		end)
		b:SetScript("OnLeave", function() GameTooltip:Hide() end)
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

-- Is this text on screen? (A text whose frame is hidden, or that has itself been hidden, is not a place for an icon.)
local function showing(fs)
	if fs.IsVisible then
		local ok, v = pcall(fs.IsVisible, fs)
		if ok and not v then return false end
	end
	local parent = fs.GetParent and fs:GetParent() or nil
	if parent and parent.IsVisible then
		local ok, v = pcall(parent.IsVisible, parent)
		if ok and not v then return false end
	end
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
				local state = on and showing(fs) and Icons.Resolve(fs:GetText(), now) or nil
				Icons.Set(s.icon, state)
				if state then Icons.PlaceBeside(s.icon, fs) end
			end
		end
	end
end

-- A nameplate is a child of WorldFrame whose first region is the border picture and whose third is the name.
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
					Icons.plates[table.getn(Icons.plates) + 1] = { frame = f, name = regions[3] }
				end
			end
		end
		i = i + 1
	end
end

-- The name text of a plate that the player sees: ECO's own when ECO styles this plate (it hides the client's), else the client's.
local function plateText(p)
	local np = eco("nameplates")
	local plate = np and np.plates and np.plates[p.frame] or nil
	if plate and plate.name and plate.name.GetText then return plate.name end
	return p.name
end

local function plates(now, on)
	findPlates()
	for i = 1, table.getn(Icons.plates) do
		local p = Icons.plates[i]
		local visible = p.frame.IsVisible and p.frame:IsVisible()
		local fs = plateText(p)
		local state = nil
		if on and visible then state = Icons.Resolve(fs:GetText(), now) end
		if state and not p.icon then p.icon = Icons.New(p.frame) end
		if p.icon then
			Icons.Set(p.icon, state)
			if state then Icons.PlaceBeside(p.icon, fs) end
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
