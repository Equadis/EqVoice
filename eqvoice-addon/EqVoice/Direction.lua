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
-- The game's axes are not the same on every client: SuperWoW's UnitPosition answers (north, west, up) and ClassicAPI's answers (y, x, up,
-- map) in the other order, whichever of the two loaded last is the one in use, and either can be mirrored. So the addon does not trust a
-- convention: it learns the axes from you. Every few yards you run forward, the way you moved (in the position's own axes) is compared
-- with the way the character faces (GetPlayerFacing, 0 north and growing counter-clockwise), and that gives the turn (a quarter, a half
-- ...) and whether the axes are mirrored. After about 20 yards of running it is settled and kept (EqVoiceDB.dirCal); until then the shape of
-- the answer gives a first guess (four values: ClassicAPI's, west first; else SuperWoW's, north first).
-- If a client ever answers mirrored, `/ev dir flip` swaps left and right on top of that.

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
Dir.LEARN_YARDS = 20         -- yards of running forward before the axes are settled
Dir.learn     = { sx = { 0, 0 }, sy = { 0, 0 }, fx = 0, fy = 0, yards = 0 }

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

-- The axes, as learned or else guessed: `s` is +1 when the position's axes turn the way the facing does and -1 when they are mirrored, and
-- `c` (radians) is where straight ahead of a character facing 0 lies in them. SuperWoW's (north, west) is s 1, c 0; ClassicAPI's (west, north) is s -1, c 90 degrees.
function Dir.Axes()
	local cal = OV.db and OV.db.dirCal
	if type(cal) == "table" and cal.v == 2 and type(cal.s) == "number" and type(cal.c) == "number" then return cal.s, cal.c, true end       -- (v 2: learned with the turns check; older ones could be wrong)
	if type(UnitPosition) == "function" then
		local ok, a, b, c, d = pcall(UnitPosition, "player")
		if ok and d ~= nil then return -1, math.pi / 2, false end         -- four values: ClassicAPI's (y, x, z, map)
	end
	return 1, 0, false
end

local function wrap(r)
	while r > math.pi do r = r - 2 * math.pi end
	while r <= -math.pi do r = r + 2 * math.pi end
	return r
end

-- Pure. You stand at (pn, pw) facing `facing`; they stand at (tn, tw). Degrees clockwise from straight ahead, -180 < d <= 180;
-- nil when you stand on the same spot. (s, c): the axes, see Dir.Axes; (1, 0) when not given.
function Dir.Azimuth(pn, pw, facing, tn, tw, flip, s, c)
	local dn, dw = tn - pn, tw - pw
	if dn == 0 and dw == 0 then return nil end
	s, c = s or 1, c or 0
	local bearing = math.atan2(dw, dn)                 -- in the position's axes: with (north, west), counter-clockwise from north
	local deg = -s * (bearing - (s * facing + c)) * 180 / math.pi     -- clockwise from where you face: to your right is positive
	while deg > 180 do deg = deg - 360 end
	while deg <= -180 do deg = deg + 360 end
	if flip then deg = -deg end
	return deg
end

-- Learn the axes from your own running: called with where you stand and which way you face, ten times a second. A step of 0.3 to 3 yards
-- with the facing steady is you running (or riding) straight ahead; the way you went, against the way you face, says how the axes lie.
-- Backing up or sidestepping is a minority and the sum is dominated by running forward.
function Dir.Learn(pa, pb, facing)
	local L = Dir.learn
	if L.pa then
		local da, db = pa - L.pa, pb - L.pb
		local d = math.sqrt(da * da + db * db)
		local df = math.abs(wrap(facing - L.f))
		if d >= 0.3 and d <= 3 and df < 0.05 then
			local h = math.atan2(db, da)
			for k = 1, 2 do
				local s = (k == 1) and 1 or -1
				local e = h - s * facing
				L.sx[k] = L.sx[k] + d * math.cos(e)
				L.sy[k] = L.sy[k] + d * math.sin(e)
			end
			L.fx, L.fy = L.fx + d * math.cos(facing), L.fy + d * math.sin(facing)
			L.yards = L.yards + d
		end
	end
	L.pa, L.pb, L.f = pa, pb, facing
	if L.yards >= Dir.LEARN_YARDS then
		-- Running in ONE direction cannot tell a mirrored set of axes from a plain one (both give a steady angle), so the run has to have turned:
		-- the way you faced must have varied (spread < 0.85) before it counts. A straight run is kept and added to, not decided on.
		local spread = math.sqrt(L.fx * L.fx + L.fy * L.fy) / L.yards
		local r1 = math.sqrt(L.sx[1] * L.sx[1] + L.sy[1] * L.sy[1])
		local r2 = math.sqrt(L.sx[2] * L.sx[2] + L.sy[2] * L.sy[2])
		local k = (r1 >= r2) and 1 or 2
		local r, other = (k == 1) and r1 or r2, (k == 1) and r2 or r1
		if spread <= 0.85 then
			if r / L.yards >= 0.85 and other / L.yards <= 0.75 and OV.db then
				local c = math.atan2(L.sy[k], L.sx[k])
				local q = (math.pi / 2) * math.floor(c / (math.pi / 2) + 0.5)            -- the axes are a quarter-turn apart: a reading near one is that one
				if math.abs(wrap(c - q)) < 0.3 then c = wrap(q) end
				OV.db.dirCal = { s = (k == 1) and 1 or -1, c = c, v = 2 }
				Dir.settled = (Dir.settled or 0) + 1
			end
			L.sx, L.sy, L.fx, L.fy, L.yards = { 0, 0 }, { 0, 0 }, 0, 0, 0         -- and look again over the next 20 yards (a patch can change the axes)
		elseif L.yards >= Dir.LEARN_YARDS * 4 then
			L.sx, L.sy, L.fx, L.fy, L.yards = { 0, 0 }, { 0, 0 }, 0, 0, 0         -- 80 yards without a turn: start again rather than grow forever
		end
	end
end

-- The direction of every player the last scan found: name -> degrees. Empty when the client lacks the two functions.
function Dir.Compute()
	local out, n = {}, 0
	local tokens = OV.Lookup and OV.Lookup.tokens
	if not tokens then return out, 0 end
	local facing = Dir.Facing()
	local pn, pw = Dir.Position("player")
	if not (facing and pn) then return out, 0 end
	Dir.Learn(pn, pw, facing)
	local flip = OV.db and OV.db.dirFlip
	local s, c = Dir.Axes()
	for name, token in pairs(tokens) do
		local tn, tw = Dir.Position(token)
		if tn then
			local d = Dir.Azimuth(pn, pw, facing, tn, tw, flip, s, c)
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
