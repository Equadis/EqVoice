-- EqVoice / Direction.lua
-- Where each nearby EqVoice player is, relative to the way YOUR character faces, so that their voice can come from that side.
--
-- Ten times a second, for every player the last scan found, the game is asked where they are (UnitPosition) and which way you face
-- (GetPlayerFacing); the answer is one number per player: degrees clockwise from straight ahead. +90 is directly to your right, -90 to
-- your left, 180 behind you. Those numbers go to the EqVoice program in a small file of their own (eqvoice-dir*.txt), written only when
-- one of them moved by a few degrees (and once a second otherwise, so the program can tell the game is still there). Nothing but these
-- angles is written: no coordinates leave the game, and nothing at all leaves this PC.
--
--   OVD1 <seq>
--   <name> <degrees>
--
-- The game's axes: the first number UnitPosition gives grows to the north, the second to the west; facing is 0 looking north and grows
-- counter-clockwise (a quarter turn to the left is +pi/2). If a client ever answers mirrored, `/ev dir flip` swaps left and right.

local OV = EqVoice
local Dir = {}
OV.Direction = Dir

Dir.NAME      = "eqvoice-dir"
Dir.EVERY     = 0.1          -- seconds between looks
Dir.KEEPALIVE = 1.0          -- seconds between writes when nothing moved
Dir.STEP      = 3            -- degrees: a direction that moved less than this is not worth a write
Dir.seq       = 0
Dir.lastBody  = nil
Dir.lastWrite = -100
Dir.last      = {}           -- name -> degrees, as last written
Dir.stats     = { writes = 0, errors = 0 }

-- Which way you face, in radians: 0 north, growing counter-clockwise. nil when the client cannot say.
function Dir.Facing()
	if type(GetPlayerFacing) == "function" then
		local ok, v = pcall(GetPlayerFacing)
		if ok and type(v) == "number" then return v end
	end
	if type(UnitFacing) == "function" then
		local ok, v = pcall(UnitFacing, "player")
		if ok and type(v) == "number" then return v end
	end
	return nil
end

-- (north, west) of a unit in yards, or nil.
function Dir.Position(unit)
	if type(UnitPosition) ~= "function" then return nil end
	local ok, a, b = pcall(UnitPosition, unit)
	if ok and type(a) == "number" and type(b) == "number" then return a, b end
	return nil
end

-- Pure. You stand at (pn, pw) facing `facing`; they stand at (tn, tw). Degrees clockwise from straight ahead, -180 < d <= 180;
-- nil when you stand on the same spot.
function Dir.Azimuth(pn, pw, facing, tn, tw, flip)
	local dn, dw = tn - pn, tw - pw
	if dn == 0 and dw == 0 then return nil end
	local bearing = math.atan2(dw, dn)                 -- counter-clockwise from north: due west is +90 degrees
	local deg = -(bearing - facing) * 180 / math.pi     -- clockwise from where you face: to your right is positive
	while deg > 180 do deg = deg - 360 end
	while deg <= -180 do deg = deg + 360 end
	if flip then deg = -deg end
	return deg
end

-- The direction of every player the last scan found: name -> degrees. Empty when the client lacks the two functions.
function Dir.Compute()
	local out, n = {}, 0
	local tokens = OV.Lookup and OV.Lookup.tokens
	if not tokens then return out, 0 end
	local facing = Dir.Facing()
	local pn, pw = Dir.Position("player")
	if not (facing and pn) then return out, 0 end
	local flip = OV.db and OV.db.dirFlip
	for name, token in pairs(tokens) do
		local tn, tw = Dir.Position(token)
		if tn then
			local d = Dir.Azimuth(pn, pw, facing, tn, tw, flip)
			if d then out[name] = d; n = n + 1 end
		end
	end
	return out, n
end

local function write(content)
	local B = OV.Bridge
	local api = B and B.FileApi and B.FileApi()
	if not api then return false end
	local ok = B.WriteNamed(api, Dir.NAME, content)
	local who = OV.FileSafeName and OV.FileSafeName()
	if who then
		local okC = B.WriteNamed(api, Dir.NAME .. "-" .. who, content)
		ok = ok or okC
	end
	if ok then Dir.stats.writes = Dir.stats.writes + 1 else Dir.stats.errors = Dir.stats.errors + 1 end
	return ok
end

function Dir.Update(now)
	if not OV.started then return end
	local dirs, n = Dir.Compute()
	local moved = false
	local names, k = {}, 0
	for name, d in pairs(dirs) do
		k = k + 1
		names[k] = name
		local was = Dir.last[name]
		if was == nil then moved = true
		else
			local diff = math.abs(d - was)
			if diff > 180 then diff = 360 - diff end
			if diff >= Dir.STEP then moved = true end
		end
	end
	for name in pairs(Dir.last) do
		if dirs[name] == nil then moved = true end
	end
	if n == 0 and (Dir.lastBody == "" or Dir.lastBody == nil) then return end       -- nobody near: nothing to say (an old file is ignored by its age)
	if not moved and now - Dir.lastWrite < Dir.KEEPALIVE then return end
	table.sort(names)
	local body = ""
	for i = 1, k do body = body .. names[i] .. " " .. string.format("%d", math.floor(dirs[names[i]] + 0.5)) .. "\n" end
	Dir.seq = Dir.seq + 1
	if write("OVD1 " .. Dir.seq .. "\n" .. body) then
		Dir.last, Dir.lastBody, Dir.lastWrite = dirs, body, now
	end
end

local function install()
	if not CreateFrame then return end
	local f = CreateFrame("Frame", "EqVoiceDirectionFrame")
	local acc = 0
	f:SetScript("OnUpdate", function()
		acc = acc + (arg1 or 0)
		if acc < Dir.EVERY then return end
		acc = 0
		local ok, err = pcall(Dir.Update, OV.Now())
		if not ok and Dir.error ~= tostring(err) then
			Dir.error = tostring(err)
			OV.Trace("direction error: " .. tostring(err))
		end
	end)
	Dir.frame = f
end

install()
