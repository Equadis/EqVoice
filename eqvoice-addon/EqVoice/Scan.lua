-- EqVoice / Scan.lua
-- Who is near me right now? Answered from what the client already knows, with nothing sent
-- anywhere. Three methods, best first; every call is guarded because each depends on a client
-- mod that may or may not be installed.
--
--   classicapi  C_NamePlate.GetNamePlateGUIDs() lists every unit that has a nameplate; GUID
--               strings work as unit tokens, so UnitIsPlayer / UnitName / UnitDistanceSquared
--               answer for any of them. No grouping involved. (ClassicAPI)
--   superwow    the nameplate frames are children of WorldFrame and frame:GetName(1) returns the
--               unit's GUID. Distance from UnitXP("distanceBetween", ...) if present. (SuperWoW)
--   frames      the bare client: nameplate frames carry the unit's name as text and nothing
--               else. Names only, no distance, and NPCs cannot be told from players.
--
-- All three need a nameplate for the unit, so friendly nameplates have to be switched on
-- (the "Show Friendly Name Plates" key binding) for friendly players to appear. Their reach is
-- the nameplate distance (SuperWoW's NameplateRange or Nampower's NP_NameplateDistance, 10-80 yd).
--
-- Result entries: { name, guid, dist (yards or nil), enemy (true/false/nil), player (true/false/nil) }.

local OV = EqVoice
local Scan = {}
OV.Scan = Scan

local function try(f, a, b, c)
	if type(f) ~= "function" then return false end
	local ok, r1, r2, r3, r4 = pcall(f, a, b, c)
	if not ok then return false end
	return true, r1, r2, r3, r4
end

-- What this client can do. Cheap; safe to call at any time.
function Scan.Capabilities()
	local c = {}
	c.classicNameplates = (type(C_NamePlate) == "table" and type(C_NamePlate.GetNamePlateGUIDs) == "function") and true or false
	c.distanceSquared = (type(UnitDistanceSquared) == "function") and true or false
	c.tokenFromName = (type(UnitTokenFromName) == "function") and true or false
	c.unitPosition = (type(UnitPosition) == "function") and true or false
	c.superWoWPlates = (type(UnitNameplate) == "function") and true or false
	c.unitXP = (type(UnitXP) == "function") and true or false
	c.worldFrame = (WorldFrame and WorldFrame.GetChildren) and true or false
	c.method = "none"
	if c.classicNameplates and c.distanceSquared then c.method = "classicapi"
	elseif c.superWoWPlates and c.worldFrame then c.method = "superwow"
	elseif c.worldFrame then c.method = "frames" end
	return c
end

local function distanceTo(unit, caps)
	if caps.distanceSquared then
		local ok, d2, checked = try(UnitDistanceSquared, unit)
		if ok and checked and type(d2) == "number" and d2 >= 0 then return math.sqrt(d2) end
	end
	if caps.unitXP then
		local ok, d = try(UnitXP, "distanceBetween", "player", unit)
		if ok and type(d) == "number" then return d end
	end
	return nil
end

Scan.stats = { guids = 0, notPlayer = 0, noName = 0, self = 0, errors = 0 }

local function describe(guid, caps, me)
	local st = Scan.stats
	st.guids = st.guids + 1
	local okP, isPlayer = try(UnitIsPlayer, guid)
	if not okP then st.errors = st.errors + 1 return nil end
	if not isPlayer then st.notPlayer = st.notPlayer + 1 return nil end
	local okN, name = try(UnitName, guid)
	if not okN then st.errors = st.errors + 1 return nil end
	if type(name) ~= "string" or name == "" then st.noName = st.noName + 1 return nil end
	if name == me then st.self = st.self + 1 return nil end
	local okE, enemy = try(UnitIsEnemy, "player", guid)
	return { name = name, guid = guid, dist = distanceTo(guid, caps), enemy = (okE and enemy) and true or false, player = true }
end

local function scanClassicApi(caps, me)
	local out, n = {}, 0
	local ok, guids = try(C_NamePlate.GetNamePlateGUIDs)
	if not ok or type(guids) ~= "table" then return out, 0 end
	local i = 1
	while guids[i] do
		local e = describe(guids[i], caps, me)
		if e then n = n + 1; out[n] = e end
		i = i + 1
	end
	return out, n
end

local function frameGuid(frame)
	local ok, g = try(frame.GetName, frame, 1)
	if ok and type(g) == "string" and string.find(g, "^0x%x+$") then return g end
	return nil
end

local function scanSuperWoW(caps, me)
	local out, n = {}, 0
	local kids = { WorldFrame:GetChildren() }
	local i = 1
	while kids[i] do
		local g = frameGuid(kids[i])
		if g then
			local e = describe(g, caps, me)
			if e then n = n + 1; out[n] = e end
		end
		i = i + 1
	end
	return out, n
end

local PLATE_BORDER = "Interface\\Tooltips\\Nameplate-Border"

local function scanFrames(caps, me)
	local out, n = {}, 0
	local kids = { WorldFrame:GetChildren() }
	local i = 1
	while kids[i] do
		local f = kids[i]
		local ok, regions = pcall(function() return { f:GetRegions() } end)
		if ok and regions[1] and regions[3] then
			local okT, tex = try(regions[1].GetTexture, regions[1])
			local okX, text = try(regions[3].GetText, regions[3])
			if okT and tex == PLATE_BORDER and okX and type(text) == "string" and text ~= "" and text ~= me then
				n = n + 1
				out[n] = { name = text, guid = nil, dist = nil, enemy = nil, player = nil }
			end
		end
		i = i + 1
	end
	return out, n
end

-- Nearby players, nearest first (unknown distances last). maxDist (yards) filters when a distance is known.
-- Returns list, count, method.
function Scan.Players(maxDist)
	local caps = Scan.Capabilities()
	local me = UnitName and UnitName("player") or nil
	local out, n
	Scan.stats = { guids = 0, notPlayer = 0, noName = 0, self = 0, errors = 0 }
	if caps.method == "classicapi" then out, n = scanClassicApi(caps, me)
	elseif caps.method == "superwow" then out, n = scanSuperWoW(caps, me)
	elseif caps.method == "frames" then out, n = scanFrames(caps, me)
	else return {}, 0, "none" end
	if maxDist then
		local kept, k = {}, 0
		for i = 1, n do
			if not out[i].dist or out[i].dist <= maxDist then k = k + 1; kept[k] = out[i] end
		end
		out, n = kept, k
	end
	table.sort(out, function(a, b)
		if a.dist and b.dist then return a.dist < b.dist end
		if a.dist then return true end
		if b.dist then return false end
		return a.name < b.name
	end)
	return out, n, caps.method
end

-- Look one player up by name, the way a directory-driven scan would: no nameplate needed.
-- Returns { name, token, dist, player } or nil plus a reason.
function Scan.Lookup(name)
	local caps = Scan.Capabilities()
	if not caps.tokenFromName then return nil, "UnitTokenFromName missing" end
	local ok, token = try(UnitTokenFromName, name, true)
	if not ok then return nil, "UnitTokenFromName raised" end
	if not token then return nil, "nobody by that name is loaded around you" end
	local _, isPlayer = try(UnitIsPlayer, token)
	local _, nm = try(UnitName, token)
	return { name = nm, token = token, dist = distanceTo(token, caps), player = isPlayer and true or false }
end

-- The shape of UnitPosition on this client: ClassicAPI returns y, x, z, instanceId; SuperWoW's
-- version returns a different shape, and whichever registered last is live.
function Scan.PositionShape()
	if type(UnitPosition) ~= "function" then return nil end
	local ok, a, b, c, d = pcall(UnitPosition, "player")
	if not ok then return nil, "raised" end
	local count = 0
	if a ~= nil then count = 1 end
	if b ~= nil then count = 2 end
	if c ~= nil then count = 3 end
	if d ~= nil then count = 4 end
	return count, a, b, c, d
end

-- Raw, unfiltered look at every source, for when the scan finds nobody and nobody knows why.
-- Returns an array of text lines.
function Scan.Probe()
	local lines, n = {}, 0
	local function add(t) n = n + 1; lines[n] = t end
	local function raw(f, a, b)
		local ok, r1, r2 = pcall(f, a, b)
		if not ok then return "ERR" end
		if r2 ~= nil then return tostring(r1) .. "/" .. tostring(r2) end
		return tostring(r1)
	end

	if type(C_NamePlate) == "table" and type(C_NamePlate.GetNamePlateGUIDs) == "function" then
		local ok, g = pcall(C_NamePlate.GetNamePlateGUIDs)
		if not ok then
			add("GetNamePlateGUIDs raised: " .. tostring(g))
		elseif type(g) ~= "table" then
			add("GetNamePlateGUIDs returned a " .. type(g))
		else
			local c = 0
			while g[c + 1] do c = c + 1 end
			add("GetNamePlateGUIDs: " .. c .. " units")
			local shown = c
			if shown > 3 then shown = 3 end
			for i = 1, shown do
				add("  " .. tostring(g[i]) .. " name=" .. raw(UnitName, g[i]) .. " isPlayer=" .. raw(UnitIsPlayer, g[i]) ..
					" exists=" .. raw(UnitExists, g[i]) .. " d2=" .. raw(UnitDistanceSquared, g[i]))
			end
		end
		if type(C_NamePlate.GetNamePlates) == "function" then
			local ok2, f = pcall(C_NamePlate.GetNamePlates)
			local fc = 0
			if ok2 and type(f) == "table" then
				while f[fc + 1] do fc = fc + 1 end
			end
			add("GetNamePlates: " .. (ok2 and (fc .. " frames") or "raised"))
		end
	else
		add("C_NamePlate.GetNamePlateGUIDs is not available")
	end

	local tokens, firstName, firstPlayer = 0, nil, nil
	for i = 1, 40 do
		local ok, ex = pcall(UnitExists, "nameplate" .. i)
		if ok and ex then
			tokens = tokens + 1
			if tokens == 1 then
				firstName = raw(UnitName, "nameplate" .. i)
				firstPlayer = raw(UnitIsPlayer, "nameplate" .. i)
			end
		end
	end
	add("nameplateN tokens that exist: " .. tokens .. (firstName and ("  first: " .. firstName .. " isPlayer=" .. firstPlayer) or ""))

	if WorldFrame and WorldFrame.GetChildren then
		local kids = { WorldFrame:GetChildren() }
		local total, withGuid, bordered, listed = 0, 0, 0, 0
		local i = 1
		while kids[i] do
			total = total + 1
			local okG, gid = pcall(kids[i].GetName, kids[i], 1)
			if okG and type(gid) == "string" and string.find(gid, "^0x%x+$") then
				withGuid = withGuid + 1
				if listed < 6 then
					listed = listed + 1
					add("  child " .. i .. " " .. gid .. " name=" .. raw(UnitName, gid) .. " isPlayer=" .. raw(UnitIsPlayer, gid) ..
						" d2=" .. raw(UnitDistanceSquared, gid) .. " type=" .. raw(kids[i].GetObjectType, kids[i]))
				end
			end
			local okR, regions = pcall(function() return { kids[i]:GetRegions() } end)
			if okR and regions[1] and regions[1].GetTexture then
				local okT, tex = pcall(regions[1].GetTexture, regions[1])
				if okT and tex == PLATE_BORDER then bordered = bordered + 1 end
			end
			i = i + 1
		end
		add("WorldFrame children: " .. total .. ", with a GUID from GetName(1): " .. withGuid .. ", default-style plates: " .. bordered)
	else
		add("WorldFrame is not available")
	end
	return lines, n
end
