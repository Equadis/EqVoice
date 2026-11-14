-- A small stand-in for the 1.12 client, just enough for EqVoice.
-- The suite runs on LuaJIT (Lua 5.1); the game is Lua 5.0. This file cannot
-- catch 5.1-only syntax; tools/lint_lua50.py does that.

local Stub = {}

Stub.root = nil           -- set by run.lua: path to eqvoice-addon/EqVoice
Stub.files = { "Util", "Proto", "Nearby", "Frame", "Scan", "Inbox", "Lookup", "Direction", "Bridge", "Comm", "Status", "Control", "Panel", "Wizard", "Icons", "Diag", "Calibrate", "Core" }

local function reset()
	Stub.now = 1000
	Stub.playerName = "Me"
	Stub.inGuild, Stub.raid, Stub.party = false, 0, 0
	Stub.sent = {}
	Stub.said = {}                -- what the addon spoke aloud with SendChatMessage: { text, chan, t }
	Stub.sayError = nil
	Stub.chat = {}
	Stub.frames = {}
	Stub.sendError = nil
	Stub.rejectTypes = {}
	Stub.loopback = {}            -- chan -> true: deliver own sends back as CHAT_MSG_ADDON
	Stub.mapOpen = false
	Stub.pos = { c = 2, z = 7, x = 0.4123, y = 0.5870 }
	Stub.zoneText = "Elwynn Forest"
	Stub.fileCalls = {}
	Stub.fileApi = nil            -- "WriteCustomFile" | "ExportFile" | nil
	Stub.fileError = nil
	Stub.fileLast = nil
	Stub.fs = {}                  -- fake game-folder files, by name
	Stub.shift = false            -- is Shift held (IsShiftKeyDown)
	Stub.noSlider = nil           -- true: CreateFrame("Slider") fails, as on a client without one
end

Stub.MICRO_NAMES = { "CharacterMicroButton", "SpellbookMicroButton", "TalentMicroButton", "QuestLogMicroButton", "SocialsMicroButton",
	"WorldMapMicroButton", "MainMenuMicroButton", "HelpMicroButton" }

local function newTexture()
	local t = { r = 0, g = 0, b = 0, a = 0, shown = true }
	function t:SetTexture(path) self.path = path end
	function t:SetVertexColor(r, g, b, a) self.r, self.g, self.b, self.a = r, g, b, a end
	function t:SetAlpha(a) self.alpha = a end
	function t:SetTexCoord(l, r, tp, b) self.tc = { l, r, tp, b } end
	function t:SetWidth(w) self.w = w end
	function t:SetHeight(h) self.h = h end
	function t:SetPoint(p, rel, rp, x, y) self.pt = { p, rel, rp, x, y } end
	function t:ClearAllPoints() self.pt = nil end
	function t:SetAllPoints() end
	function t:SetBlendMode(m) self.blend = m end
	function t:Show() self.shown = true end
	function t:Hide() self.shown = false end
	function t:IsShown() return self.shown end
	return t
end

-- Only what the 1.12 client has: an unknown method here is a nil call, as in the game.
local function newFontString(font)
	local t = { text = "", r = 1, g = 1, b = 1, shown = true, font = font }
	function t:SetText(s) self.text = s end
	function t:GetText() return self.text end
	function t:GetParent() return self.parent end
	function t:SetTextColor(r, g, b) self.r, self.g, self.b = r, g, b end
	function t:SetJustifyH(j) self.jh = j end
	function t:GetJustifyH() return self.jh or "CENTER" end
	function t:SetJustifyV(j) self.jv = j end
	function t:SetPoint(p, rel, rp, x, y) self.pt = { p, rel, rp, x, y } end
	function t:ClearAllPoints() self.pt = nil end
	function t:SetWidth(w) self.w = w end
	function t:SetHeight(h) self.h = h end
	function t:Show() self.shown = true end
	function t:Hide() self.shown = false end
	function t:SetFontObject(f) self.font = f end
	function t:SetAlpha(a) self.alpha = a end
	function t:GetFont() return self.fontFile, self.fontSize end
	function t:SetShadowOffset(x, y) self.shadow = { x, y } end
	function t:SetShadowColor(r, g, b, a) end
	function t:SetFont(path, size, flags) self.fontFile, self.fontSize = path, size end
	function t:GetStringWidth() return string.len(self.text) * 6 end
	function t:GetWidth() return self.w or (string.len(self.text) * 6) end        -- a text with no width of its own is as wide as its words
	function t:IsVisible() return self.shown end
	return t
end

local function addButton(f)
	f.text, f.enabled, f.highlight, f.clicks = "", true, false, {}
	function f:SetText(s) self.text = s; if self.fs then self.fs.text = s end end
	function f:GetText() return self.text end
	function f:SetFontString(fs) self.fs = fs; fs.text = self.text end
	function f:GetFontString()
		if not self.fs then self.fs = newFontString("GameFontNormal"); self.fs.text = self.text end
		return self.fs
	end
	function f:RegisterForClicks(...) self.clicks = { ... } end
	function f:Enable() self.enabled = true end
	function f:Disable() self.enabled = false end
	function f:IsEnabled() return self.enabled and 1 or nil end
	function f:LockHighlight() self.highlight = true end
	function f:UnlockHighlight() self.highlight = false end
	function f:SetNormalTexture(p) self.normalTex = p end
	function f:SetPushedTexture(p) self.pushedTex = p end
	function f:SetHighlightTexture(p) self.hl = newTexture(); self.hl:SetTexture(p) end
	function f:GetHighlightTexture() return self.hl end
end

local function addSlider(f)
	f.min, f.max, f.step, f.value = 0, 1, 1, 0
	function f:SetOrientation(o) self.orientation = o end
	function f:SetMinMaxValues(a, b) self.min, self.max = a, b end
	function f:GetMinMaxValues() return self.min, self.max end
	function f:SetValueStep(s) self.step = s end
	function f:GetValue() return self.value end
	function f:SetThumbTexture(p) self.thumb = newTexture(); self.thumb:SetTexture(p) end
	function f:GetThumbTexture() return self.thumb end
	-- as in the game: a value outside the range is brought inside, one between steps is rounded, and a change calls OnValueChanged at once
	function f:SetValue(v)
		if v < self.min then v = self.min end
		if v > self.max then v = self.max end
		v = self.min + math.floor((v - self.min) / self.step + 0.5) * self.step
		if v == self.value then return end
		self.value = v
		if self.scripts.OnValueChanged then
			local pt, pa = _G.this, _G.arg1
			_G.this, _G.arg1 = self, v
			self.scripts.OnValueChanged()
			_G.this, _G.arg1 = pt, pa
		end
	end
end

local function addEditBox(f)
	f.text = ""
	function f:SetText(s) self.text = s or "" end
	function f:GetText() return self.text end
	function f:SetAutoFocus(on) self.autoFocus = on end
	function f:SetFontObject(o) self.fontObject = o end
	function f:SetMaxLetters(n) self.maxLetters = n end
	function f:SetTextInsets(a, b, c, d) self.insets = { a, b, c, d } end
	function f:ClearFocus() self.focused = false end
	function f:SetFocus() self.focused = true end
end

local function newFrame(name, kind, parent, template)
	local f = { name = name, kind = kind or "Frame", parent = parent, template = template, events = {}, scripts = {},
		textures = {}, fontStrings = {}, shown = true, alpha = 1, level = 1 }
	if name then _G[name] = f end
	if f.kind == "Button" then addButton(f) end
	if f.kind == "Slider" then addSlider(f) end
	if f.kind == "EditBox" then addEditBox(f) end
	function f:EnableMouse(on) self.mouse = on end
	function f:EnableKeyboard(on) self.keyboard = on end
	function f:SetMovable(on) self.movable = on end
	function f:SetClampedToScreen(on) self.clamped = on end
	function f:RegisterForDrag(...) self.drag = { ... } end
	function f:StartMoving() self.moving = true end
	function f:StopMovingOrSizing() self.moving = false end
	function f:SetBackdrop(b) self.backdrop = b end
	function f:SetBackdropColor(r, g, b, a) self.bd = { r, g, b, a } end
	function f:SetBackdropBorderColor(r, g, b, a) self.bdBorder = { r, g, b, a } end
	function f:SetParent(p) self.parent = p end
	function f:GetParent() return self.parent end
	function f:SetFrameLevel(l) self.level = l end
	function f:GetFrameLevel() return self.level end
	function f:SetAlpha(a) self.alpha = a end
	function f:IsShown() return self.shown end
	function f:GetName() return self.name end
	function f:GetPoint()
		local p = self.pt
		if not p then return nil end
		return p[1], p[2], p[3], p[4], p[5]
	end
	function f:CreateFontString(n, layer, font)
		local fs = newFontString(font)
		fs.parent = self
		if n then _G[n] = fs end                          -- a named region is a global in the game
		self.fontStrings[table.getn(self.fontStrings) + 1] = fs
		return fs
	end
	function f:RegisterEvent(e) self.events[e] = true end
	function f:SetScript(k, fn) self.scripts[k] = fn end
	function f:SetFrameStrata(s) self.strata = s end
	function f:SetWidth(w) self.w = w end
	function f:SetHeight(h) self.h = h end
	function f:GetWidth() return self.w end
	function f:GetHeight() return self.h end
	function f:SetPoint(p, rel, rp, x, y) self.pt = { p, rel, rp, x, y } end
	function f:ClearAllPoints() self.pt = nil end
	function f:Show() self.shown = true end
	function f:Hide() self.shown = false end
	function f:IsVisible() return self.shown end
	function f:SetAllPoints() end
	function f:SetScale(s) self.scale = s end
	function f:GetScale() return self.scale or 1 end
	function f:GetEffectiveScale() return self.eff or self.scale or 1 end
	-- screen layout is set by the test (f.layout = { left, bottom, right } in pixels); the client answers in the frame's own scale
	function f:GetRight() return self.layout and (self.layout[3] / self:GetEffectiveScale()) or nil end
	function f:GetLeft() return self.layout and (self.layout[1] / self:GetEffectiveScale()) or nil end
	function f:GetBottom() return self.layout and (self.layout[2] / self:GetEffectiveScale()) or nil end
	function f:CreateTexture()
		local t = newTexture()
		self.textures[table.getn(self.textures) + 1] = t
		return t
	end
	Stub.frames[table.getn(Stub.frames) + 1] = f
	return f
end

-- a hand on a slider: press, move, let go (a slider only takes values from a hand)
function Stub.Drag(frame, v)
	if frame.scripts.OnMouseDown then frame.scripts.OnMouseDown() end
	frame:SetValue(v)
	if frame.scripts.OnMouseUp then frame.scripts.OnMouseUp() end
end

-- the client's own switches for friendly nameplates (Shift-V sets FRIENDNAMEPLATES_ON; an addon may call the two functions directly)
function ShowFriendNameplates() Stub.friendPlates = true; Stub.friendPlateCalls = (Stub.friendPlateCalls or 0) + 1 end
function HideFriendNameplates() Stub.friendPlates = false end

function GetCursorPosition() return Stub.cursorX or 0, Stub.cursorY or 0 end
function IsMouseButtonDown(b) return Stub.mouseDown and 1 or nil end

function Stub.FireEvent(ev, a1, a2, a3, a4)
	for i = 1, table.getn(Stub.frames) do
		local f = Stub.frames[i]
		if f.events[ev] and f.scripts.OnEvent then
			_G.event, _G.arg1, _G.arg2, _G.arg3, _G.arg4 = ev, a1, a2, a3, a4
			f.scripts.OnEvent()
		end
	end
end

-- Advance the clock in 0.25 s steps, running OnUpdate each step.
function Stub.Advance(seconds)
	local steps = math.floor(seconds / 0.25 + 0.5)
	for s = 1, steps do
		Stub.now = Stub.now + 0.25
		for i = 1, table.getn(Stub.frames) do
			local f = Stub.frames[i]
			if f.scripts.OnUpdate then
				_G.arg1 = 0.25
				f.scripts.OnUpdate()
			end
		end
	end
end

function Stub.Boot(opts)
	opts = opts or {}
	reset()
	for k, v in pairs(opts) do Stub[k] = v end
	_G.EqVoice, _G.EqVoiceDB = nil, opts.db
	_G.SLASH_EQVOICE1, _G.SLASH_EQVOICE2 = nil, nil
	_G.SLASH_EQVOICEUI1, _G.SLASH_EQVOICEUI2 = nil, nil
	_G.EqVoiceIndicator, _G.EqVoicePanel, _G.EqVoiceKeyCapture = nil, nil, nil      -- named frames are globals in the game
	_G.TargetName, _G.PlayerName, _G.EquadisClassicOverhaul = nil, nil, nil
	_G.EqVoiceSetup, _G.EqVoiceBlocklist = nil, nil
	_G.EqVoiceWizard, _G.EqVoiceDevicePicker = nil, nil
	for i = 1, 4 do _G["PartyMemberFrame" .. i .. "Name"] = nil end
	for i = 1, table.getn(Stub.MICRO_NAMES) do _G[Stub.MICRO_NAMES[i]] = nil end
	_G.LFGMicroButton, _G.PVPMicroButton, _G.ShopMicroButton = nil, nil, nil
	_G.IsShiftKeyDown = function() return Stub.shift and 1 or nil end
	_G.SlashCmdList = {}
	_G.UIParent = newFrame("UIParent")
	_G.WorldMapFrame = newFrame("WorldMapFrame")
	_G.WorldMapFrame.shown = false
	_G.DEFAULT_CHAT_FRAME = { AddMessage = function(self, m) Stub.chat[table.getn(Stub.chat) + 1] = m end }

	_G.GetTime = function() return Stub.now end
	_G.time = function() return 1700000000 + math.floor(Stub.now) end
	Stub.units, Stub.plates = {}, {}
	if opts.units then Stub.units = opts.units end
	if opts.plates then Stub.plates = opts.plates end
	_G.UnitName = function(u)
		if u == "player" then return Stub.playerName end
		local x = Stub.units[u]
		return x and x.name or nil
	end
	_G.UnitIsPlayer = function(u)
		if u == "player" then return 1 end
		local x = Stub.units[u]
		return (x and x.player) and 1 or nil
	end
	_G.UnitIsEnemy = function(a, u)
		local x = Stub.units[u]
		return (x and x.enemy) and 1 or nil
	end
	-- What the ClassicAPI mod adds (docs: C_NamePlate, UnitDistanceSquared, UnitTokenFromName, UnitPosition)
	_G.C_NamePlate, _G.UnitDistanceSquared, _G.UnitTokenFromName, _G.UnitPosition = nil, nil, nil, nil
	if opts.classic then
		_G.C_NamePlate = { GetNamePlateGUIDs = function()
			if Stub.nameplateError then error(Stub.nameplateError) end
			local t = {}
			for i = 1, table.getn(Stub.plates) do t[i] = Stub.plates[i] end
			return t
		end }
		_G.UnitDistanceSquared = function(u)
			if Stub.distError then error(Stub.distError) end
			if u == "player" then return 0, true end
			local x = Stub.units[u]
			if x and x.dist then return x.dist * x.dist, true end
			return 0, false
		end
		_G.UnitTokenFromName = function(name, exact)
			for tok, x in pairs(Stub.units) do
				if x.name == name then return tok end
			end
			return nil
		end
		_G.UnitPosition = function(u) return 1234.5, 678.9, 12.0, 1 end
	end
	-- SuperWoW: nameplate frames answer GetName(1) with the unit's GUID; UnitXP measures distance
	_G.UnitNameplate, _G.UnitXP = nil, nil
	if opts.superwow then
		_G.UnitNameplate = function() return nil end
		_G.UnitXP = function(cmd, a, u)
			if cmd == "distanceBetween" then
				local x = Stub.units[u]
				return x and x.dist or nil
			end
			return nil
		end
	end
	-- WorldFrame carries the nameplate frames
	local kids = {}
	local function mkPlate(name, guid)
		local f = {}
		function f.GetName(self, arg) if arg == 1 then return guid end return nil end
		function f.GetRegions(self)
			return { GetTexture = function() return "Interface\\Tooltips\\Nameplate-Border" end },
				{ GetTexture = function() return "x" end },
				{ GetText = function() return name end }
		end
		return f
	end
	if opts.plateFrames then
		for i = 1, table.getn(opts.plateFrames) do
			local pf = opts.plateFrames[i]
			kids[table.getn(kids) + 1] = mkPlate(pf.name, pf.guid)
		end
		kids[table.getn(kids) + 1] = { GetName = function() return "SomeOtherFrame" end,
			GetRegions = function() return { GetTexture = function() return "other" end } end }
	end
	_G.WorldFrame = { GetChildren = function(self) return unpack(kids) end, GetNumChildren = function(self) return table.getn(kids) end }
	Stub.kids = kids
	Stub.friendPlates, Stub.friendPlateCalls = false, 0
	_G.FRIENDNAMEPLATES_ON = nil
	_G.IsInGuild = function() return Stub.inGuild end
	_G.GetNumRaidMembers = function() return Stub.raid end
	_G.GetNumPartyMembers = function() return Stub.party end
	_G.GetBuildInfo = function() return "1.12.1", "5875", "Sep 19 2006" end
	_G.GetLocale = function() return "enUS" end
	_G.GetCVar = function(n) return "x" end
	_G.getglobal = function(n) return _G[n] end
	_G.CreateFrame = function(kind, name, parent, template)
		if kind == "Slider" and Stub.noSlider then error("Couldn't create a Slider") end            -- a client that will not make one
		return newFrame(name, kind, parent, template)
	end
	_G.UISpecialFrames = {}
	Stub.tip = { lines = {}, shown = false }
	_G.GameTooltip = {
		SetOwner = function(self, owner, anchor) Stub.tip.owner = owner; Stub.tip.lines = {} end,
		SetText = function(self, text) Stub.tip.lines = { text } end,
		AddLine = function(self, text) Stub.tip.lines[table.getn(Stub.tip.lines) + 1] = text end,
		Show = function(self) Stub.tip.shown = true end,
		Hide = function(self) Stub.tip.shown = false end,
	}
	_G.SetMapToCurrentZone = function() end
	_G.GetCurrentMapContinent = function() return Stub.pos.c end
	_G.GetCurrentMapZone = function() return Stub.pos.z end
	_G.GetPlayerMapPosition = function() return Stub.pos.x, Stub.pos.y end
	_G.GetRealZoneText = function() return Stub.zoneText end
	_G.GetRealmName = function() return "Testrealm" end
	_G.WorldMapFrame.IsVisible = function() return Stub.mapOpen end

	Stub.channels, Stub.noJoin = {}, opts.noJoin or false
	_G.JoinChannelByName = function(name)
		if not Stub.noJoin and not Stub.channels[name] then Stub.channels[name] = 5 end
	end
	_G.LeaveChannelByName = function(name) Stub.channels[name] = nil end
	_G.GetChannelName = function(name) return Stub.channels[name] or 0, name end

	_G.SendAddonMessage = function(prefix, text, chan, target)
		if Stub.sendError then error(Stub.sendError) end
		-- the real 1.12 client says exactly this for a type it does not know (seen in game: SAY, YELL)
		if Stub.rejectTypes[chan] then error("Interface\\AddOns\\aux-addon\\libs\\ChatThrottleLib.lua:261: Unknown addon chat type") end
		if chan == "CHANNEL" and type(target) ~= "number" then error("Usage: SendAddonMessage(\"prefix\", \"text\", \"CHANNEL\", channelNumber)") end
		if chan == "WHISPER" and type(target) ~= "string" then error("Usage: SendAddonMessage(\"prefix\", \"text\", \"WHISPER\", \"player\")") end
		-- What the real 1.12 client does (seen in game, 2026-10-15): a "|" that is not a valid
		-- escape code makes the call fail with this error, so every message must be free of them.
		local at = 1
		while true do
			local i = string.find(text, "|", at, true)
			if not i then break end
			local nxt = string.sub(text, i + 1, i + 1)
			if not string.find("cCrRHhTtnN|", nxt, 1, true) or nxt == "" then
				error("Interface\\AddOns\\aux-addon\\libs\\ChatThrottleLib.lua:261: Invalid escape code in chat message")
			end
			at = i + 2
		end
		if string.len(prefix) + string.len(text) > 254 then error("message too long") end
		Stub.sent[table.getn(Stub.sent) + 1] = { prefix = prefix, text = text, chan = chan, target = target, t = Stub.now }
		if Stub.loopback[chan] then
			Stub.FireEvent("CHAT_MSG_ADDON", prefix, text, chan, Stub.playerName)
		end
	end

	_G.SendChatMessage = function(text, chan)
		if Stub.sayError then error(Stub.sayError) end
		Stub.said[table.getn(Stub.said) + 1] = { text = text, chan = chan, t = Stub.now }
	end

	_G.WriteCustomFile, _G.ExportFile, _G.ReadCustomFile, _G.ImportFile = nil, nil, nil, nil
	if Stub.fileApi == "WriteCustomFile" then
		_G.WriteCustomFile = function(name, content, mode)
			if Stub.fileError then error(Stub.fileError) end
			Stub.fileCalls[table.getn(Stub.fileCalls) + 1] = { api = "WriteCustomFile", name = name, content = content, mode = mode }
			Stub.fileLast = content
			Stub.fs[name] = content
		end
		_G.ReadCustomFile = function(name) return Stub.fs[name] end
	elseif Stub.fileApi == "ExportFile" then
		_G.ExportFile = function(name, content)
			if Stub.fileError then error(Stub.fileError) end
			Stub.fileCalls[table.getn(Stub.fileCalls) + 1] = { api = "ExportFile", name = name, content = content }
			Stub.fileLast = content
			Stub.fs[name .. ".txt"] = content
		end
		_G.ImportFile = function(name) return Stub.fs[name .. ".txt"] end
	end

	for i = 1, table.getn(Stub.files) do
		local path = Stub.root .. "/" .. Stub.files[i] .. ".lua"
		local chunk, err = loadfile(path)
		if not chunk then error("load " .. path .. ": " .. tostring(err)) end
		chunk()
	end
	return _G.EqVoice
end

-- A nameplate the way the default client builds one: a button under WorldFrame whose regions are the border picture, the glow and the name.
function Stub.AddPlate(name, kind)
	local f = newFrame(nil, "Button", UIParent)
	local border = newTexture(); border:SetTexture("Interface\\Tooltips\\Nameplate-Border")
	local glow = newTexture(); glow:SetTexture("x")
	function border:GetTexture() return self.path end
	function glow:GetTexture() return self.path end
	local fs = newFontString("GameFontNormal"); fs.text = name; fs.parent = f
	local level = newFontString("GameFontNormal"); level.text = "60"
	local skull = newTexture()
	function f:GetRegions() return border, glow, fs, level, skull end
	-- the health bar: blue for a friendly player, green for a friendly NPC, red for an enemy
	local colors = { player = { 0, 0, 1 }, npc = { 0, 1, 0 }, enemy = { 1, 0, 0 } }
	local bar = { color = colors[kind or "player"], alpha = 1 }
	function bar:GetStatusBarColor() return self.color[1], self.color[2], self.color[3] end
	function bar:SetAlpha(a) self.alpha = a end
	function f:GetChildren() return bar end
	f.nameText, f.bar, f.border, f.level = fs, bar, border, level
	Stub.kids[table.getn(Stub.kids) + 1] = f
	return f
end

-- A click on a button or the indicator, as the client delivers it: `this` is the frame, arg1 the mouse button.
-- The client's micro bar: eight 28 x 58 buttons in a row starting at (x, y) on screen, `scale` times the size of UIParent's units.
-- Call after Boot (Boot forgets them). Returns the buttons by name.
function Stub.MicroBar(x, y, scale)
	scale = scale or 1
	local out = {}
	for i = 1, table.getn(Stub.MICRO_NAMES) do
		local f = newFrame(Stub.MICRO_NAMES[i], "Button", UIParent)
		local left = x + (i - 1) * 30 * scale
		f.layout = { left, y, left + 28 * scale }
		f.eff = scale
		out[Stub.MICRO_NAMES[i]] = f
	end
	return out
end

function Stub.Click(f, button)
	_G.this, _G.arg1 = f, button or "LeftButton"
	if f.scripts.OnClick then f.scripts.OnClick() end
end

-- Boot, log in and return the addon table.
function Stub.Login(opts)
	local OV = Stub.Boot(opts)
	Stub.FireEvent("VARIABLES_LOADED")
	Stub.FireEvent("PLAYER_ENTERING_WORLD")
	return OV
end

-- Deliver a heartbeat from another player as the server would.
function Stub.Hear(text, chan, sender)
	Stub.FireEvent("CHAT_MSG_ADDON", "OV1", text, chan, sender)
end

return Stub
