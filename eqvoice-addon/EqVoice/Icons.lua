-- EqVoice / Icons.lua
-- The voice icon for every player who has EqVoice: big, centred ABOVE the name on their nameplate; small, beside the name on the target, player
-- and party frames and in the panel's list. It shows what that player's EqVoice is doing right now:
--
--   o  idle                  vol_low   a plain speaker: they are on the voice channel and could talk, but no voice is coming from them   VoiceKnown.tga
--   w  speaking quietly      a speaker with one bubble: a quiet voice (it carries less far)                                       VoiceOpen.tga
--   s  speaking              a speaker with two bubbles: an ordinary voice                                                         VoiceSpeaking.tga
--   l  yelling               the same two bubbles, in RED: a raised voice (it carries further)
--   m  muted                 a microphone with a slash. Also push to talk while the key is not held: the microphone is closed
--                            either way, and from outside it looks the same   VoiceMuted.tga
--   b  muted by you          the same, a little dimmer: you put them on your blocklist
--   d  deafened              headphones with a slash (deafened also means muted)   VoiceDeafened.tga
--   D  deafened and muted    both icons, side by side;  p n q r: deafened with the microphone open: the deaf icon and the idle, quiet, speaking or yelling icon
--   (muted and deafened are drawn red)
--   i  has EqVoice           the plain speaker, dimmed: they are known to have it, but nothing more is known (not connected to them yet,
--                            or their app is not running)
--
-- Only your own icon also has these, drawn in red because something needs a look:
--   x  not connected         a red X: your EqVoice program is not running, or it cannot reach the voice server (after a few seconds of trying)
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
-- Overhead voice textures follow a native projected head-name point exposed by the ClassicAPI extension.
-- No nameplate frame is enumerated, modified, enabled or required. Unit-frame icons remain unchanged.
--
-- Written for the 1.12 client like Panel.lua: Lua 5.0, nothing newer. Every drawing step is inside a pcall, and a surface that
-- keeps failing is switched off for the session: an icon must never be able to stop the voice. /eqvc icons off removes them.

local OV = EqVoice
local Icons = {}
OV.Icons = Icons

Icons.ART    = "Interface\\AddOns\\EqVoice\\"
Icons.SIZE   = 16
Icons.PLATE_SIZE = 16          -- overhead icon size
Icons.PLATE_GAP  = 4           -- pixels between the icon and the top of the name: a fixed distance above it
Icons.GAP    = 3               -- pixels between the end of a name and its icon
Icons.EVERY  = 0.25            -- seconds between looks at the screen
Icons.MAXERR = 3               -- a surface that fails this many times is switched off
Icons.RED = { 0.95, 0.35, 0.35 }
Icons.LOOK = {
	o = { file = "VoiceKnown.tga",    word = "using EqVoice, not talking" },                    -- a speaker, no bubbles
	w = { file = "VoiceOpen.tga",     word = "speaking quietly" },                                         -- one bubble: a quiet voice carries less far
	s = { file = "VoiceSpeaking.tga", word = "speaking" },                                                 -- two bubbles: an ordinary voice
	l = { file = "VoiceSpeaking.tga", word = "yelling", tint = Icons.RED },                                -- two bubbles, red: a raised voice carries further
	m = { file = "VoiceMuted.tga",    word = "muted", tint = Icons.RED },
	b = { file = "VoiceMuted.tga",    word = "muted by you", dim = 0.7, tint = Icons.RED },
	d = { file = "VoiceDeafened.tga", word = "deafened", tint = Icons.RED },
	-- two icons side by side: deafened and muted (D), or deafened with the microphone open: the deaf icon and the voice icon (quiet p, speaking q, loud r)
	D = { file = "VoiceDeafened.tga", word = "deafened and muted", tint = Icons.RED, also = "VoiceMuted.tga", alsoTint = Icons.RED },
	p = { file = "VoiceDeafened.tga", word = "deafened, microphone open", tint = Icons.RED, also = "VoiceKnown.tga" },
	n = { file = "VoiceDeafened.tga", word = "deafened, speaking quietly", tint = Icons.RED, also = "VoiceOpen.tga" },
	q = { file = "VoiceDeafened.tga", word = "deafened, speaking", tint = Icons.RED, also = "VoiceSpeaking.tga" },
	r = { file = "VoiceDeafened.tga", word = "deafened, yelling", tint = Icons.RED, also = "VoiceSpeaking.tga", alsoTint = Icons.RED },
	i = { file = "VoiceKnown.tga",    word = "has EqVoice", dim = 0.45 },
	x = { file = "VoiceX.tga",        word = "EqVoice is not connected", tint = Icons.RED },
	e = { file = "VoiceMuted.tga",    word = "your microphone cannot be used", tint = Icons.RED },
}

-- The names of the client's own unit-frame name texts that get an icon (the target, you, your party).
Icons.UNIT_NAMES = { "TargetName", "PlayerName", "PartyMemberFrame1Name", "PartyMemberFrame2Name",
	"PartyMemberFrame3Name", "PartyMemberFrame4Name" }
Icons.ECO_PARTY = 4            -- ECO's party frames draw their own names: one more surface for each
Icons.surf    = {}             -- the unit-frame names: index -> { icon }
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
	local closed = m.mute or (m.mode == "ptt" and not m.tx)
	local voice = m.voice
	if m.wh and (voice > 0 or m.talk) then voice = 1 end
	if m.deaf then                                                               -- deafened: with the microphone shut, both icons; open, the deaf one and the voice one
		if closed then return "D" end
		if voice >= 3 then return "r" end
		if voice == 2 or (voice == 0 and m.talk) then return "q" end
		if voice == 1 then return "n" end
		return "p"
	end
	if closed then return "m" end
	if voice >= 3 then return "l" end
	if voice == 2 or (voice == 0 and m.talk) then return "s" end
	if voice == 1 then return "w" end
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
		if hasBit(fl, OV.Proto.FLAG_DEAF) then return hasBit(fl, OV.Proto.FLAG_MUTED) and "D" or "p" end
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
	local ic = { state = nil, size = size, who = nil, two = false }
	if clickable then
		local b = CreateFrame("Button", nil, parent)
		b:SetWidth(size)
		b:SetHeight(size)
		if b.SetFrameLevel and parent.GetFrameLevel then b:SetFrameLevel((parent:GetFrameLevel() or 1) + 3) end
		b:EnableMouse(true)
		b:RegisterForClicks("LeftButtonUp")
		ic.btn = b
		ic.tex = b:CreateTexture(nil, "OVERLAY")
		ic.tex:SetWidth(size)
		ic.tex:SetHeight(size)
		ic.tex:SetPoint("LEFT", b, "LEFT", 0, 0)
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
		if ic.tex2 then ic.tex2:Hide() end
		if ic.btn then ic.btn:Hide() end
		return
	end
	ic.tex:SetTexture(Icons.ART .. look.file)
	if look.tint then ic.tex:SetVertexColor(look.tint[1], look.tint[2], look.tint[3]) else ic.tex:SetVertexColor(1, 1, 1) end
	if ic.tex.SetAlpha then ic.tex:SetAlpha(look.dim or 1) end
	-- the second icon of a pair, to the right of the first
	local two = look.also ~= nil
	if two then
		if not ic.tex2 then
			local owner = ic.btn or ic.tex:GetParent()
			ic.tex2 = owner:CreateTexture(nil, "OVERLAY")
			ic.tex2:SetWidth(ic.size)
			ic.tex2:SetHeight(ic.size)
			ic.tex2:SetPoint("LEFT", ic.tex, "RIGHT", 1, 0)
		end
		ic.tex2:SetTexture(Icons.ART .. look.also)
		local c = look.alsoTint
		if c then ic.tex2:SetVertexColor(c[1], c[2], c[3]) else ic.tex2:SetVertexColor(1, 1, 1) end
		ic.tex2:Show()
	elseif ic.tex2 then
		ic.tex2:Hide()
	end
	if ic.two ~= two then
		ic.two = two
		ic.placedFor = nil                                                       -- (a pair is placed differently from one icon)
		if ic.btn then ic.btn:SetWidth(two and (ic.size * 2 + 1) or ic.size) end
	end
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
	anchor(ic, "BOTTOM", fs, "TOP", ic.two and -((ic.size + 1) / 2) or 0, Icons.PLATE_GAP)        -- a pair is centred as a pair
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

-- Native client projection: no plate enumeration, visibility checks or settings.
Icons.overhead = {}
local function hideOverhead(row)
	if row.host then row.host:Hide() end
end

local function updateOverhead()
	for name, row in pairs(Icons.overhead) do
		if row.state and Icons.Enabled() and Icons.OverheadOn() then
			local token = row.token
			local x, y = nil, nil
			if token and UnitName(token) == name and UnitIsPlayer(token) then
				local good, a, b = pcall(EqVoice_GetOverheadPoint, token)
				if good then x, y = a, b end
			end
			if type(x) == "number" and type(y) == "number" and WorldFrame then
				local scale = row.host:GetEffectiveScale() or 1
				if scale <= 0 then scale = 1 end
				row.host:ClearAllPoints()
				row.host:SetPoint("BOTTOM", WorldFrame, "BOTTOMLEFT", x / scale, y / scale + Icons.PLATE_GAP)
				row.host:Show()
			else hideOverhead(row) end
		else hideOverhead(row) end
	end
end

local function overhead(now, on)
	local live = {}
	if on and Icons.OverheadOn() and OV.Status.Alive(now) and WorldFrame then
		for i = 1, OV.Status.npeers or 0 do
			local peer = OV.Status.peers[i]
			local name = peer and peer.name
			local state = name and name ~= OV.character and Icons.Resolve(name, now) or nil
			if state then
				live[name] = true
				local row = Icons.overhead[name]
				if not row then
					-- Reuse inactive hosts instead of allocating for every player ever seen.
					for old, other in pairs(Icons.overhead) do
						if not live[old] and not OV.Status.byName[string.lower(old)] then
							row = other; Icons.overhead[old] = nil; break
						end
					end
					if not row then
						local host = CreateFrame("Frame", nil, WorldFrame)
						host:SetWidth(1); host:SetHeight(1); host:Hide()
						if host.SetFrameStrata then host:SetFrameStrata("MEDIUM") end
						row = { host = host, icon = Icons.New(host, Icons.PLATE_SIZE) }
					end
					Icons.overhead[name] = row
				end
				row.state = state
				local ok, token = pcall(UnitTokenFromName, name, true)
				row.token = ok and token or nil
				Icons.Set(row.icon, state)
				anchor(row.icon, "BOTTOM", row.host, "BOTTOM", row.icon.two and -((row.icon.size + 1) / 2) or 0, 0)
			end
		end
	end
	for name, row in pairs(Icons.overhead) do
		if not live[name] then row.state, row.token = nil, nil; Icons.Set(row.icon, nil); hideOverhead(row) end
	end
	if not Icons.overheadDriver then
		local driver = CreateFrame("Frame", "EqVoiceOverheadIcons", UIParent)
		driver:SetScript("OnUpdate", function() pcall(updateOverhead) end)
		Icons.overheadDriver = driver
	end
	updateOverhead()
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
		local ok, err = pcall(overhead, now, on)
		if not ok then fail("plates", err) end
	end
end

function Icons.SetEnabled(on)
	if on then OV.db.ui.icons = nil else OV.db.ui.icons = false end
	Icons.lastRun = -100
	Icons.Tick(OV.Now())
end

-- The icons over players' heads, on or off (kept between sessions).
function Icons.SetOverhead(on)
	if on then OV.db.ui.overhead = nil else OV.db.ui.overhead = false end
	Icons.lastRun = -100
	Icons.Tick(OV.Now())
end
