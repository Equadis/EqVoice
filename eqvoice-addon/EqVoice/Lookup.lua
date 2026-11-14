-- EqVoice / Lookup.lua
-- Distance to named players, from what the game client already knows. Given a roster of character
-- names (from the inbound file), it asks the client where each one is and keeps the ones that are
-- loaded nearby, with their distance in yards.
--
-- Verified on the running client: UnitTokenFromName(name, true) finds any loaded player by exact
-- name (no group, guild or nameplate needed) and UnitDistanceSquared(token) gives the centre to
-- centre distance, at least out to 86 yards.
--
-- Scheduling, so a large roster does not cost a frame every scan (strangers must still be found quickly):
--   1. names loaded in the last `recent` seconds are checked on every scan (their distance matters);
--   2. names never looked up yet (they just came online, or the roster was just handed over) come next, within the
--      budget: a newcomer is looked at in the very next scan, not after a sweep of the whole roster;
--   3. everyone else is checked round-robin, at most `maxChecks` per scan (the desktop client sizes that budget from the
--      roster: see `auto_checks` in app.py), so a roster of 1000 costs about `maxChecks` lookups per 0.5 s scan;
--   4. a name that keeps coming back "not loaded" is checked less often (every scan for the first two misses, then every
--      1 s, then every 2 s), except in a roster that fits in one scan's budget, where it is never worse than every 1 s:
--      the budget covers it anyway, and a stranger walking into view is then seen within about a second. (The cap is 2 s
--      because the budget already bounds the cost; a longer wait would only delay finding a stranger.)
-- Measured, not assumed: stats keep checked/loaded/missed per scan, totals, and scan timing, and
-- /ev diag lookupcost measures the frame-time effect of N lookups on this client.

local OV = EqVoice
local Lookup = {}
OV.Lookup = Lookup

Lookup.cfg = { interval = 0.5, maxChecks = 80, report = 60, resultTtl = 1.3, recent = 10 }
Lookup.info    = {}     -- name -> { misses, nextAt, dist, seenAt, loadedAt, enemy }
Lookup.order   = {}     -- roster names in order
Lookup.n       = 0
Lookup.cursor  = 1
Lookup.results = {}     -- nearest first: { name, dist, flags }
Lookup.nresults = 0
Lookup.stats = {
	scans = 0, checked = 0, loaded = 0, missed = 0,
	lastChecked = 0, lastLoaded = 0, lastMissed = 0, lastCost = 0,
	avgInterval = 0, lastScanAt = nil, errors = 0,
}

function Lookup.Available()
	return (type(UnitTokenFromName) == "function" and type(UnitDistanceSquared) == "function") and true or false
end

-- How long to wait before asking about a name again, by how many times in a row it was not loaded.
local function backoff(misses)
	if misses < 2 then return 0 end
	if misses < 5 or Lookup.n <= Lookup.cfg.maxChecks then return 1 end
	return 2
end

-- Replace the roster. Names that stay keep what is known about them.
function Lookup.SetRoster(names, count)
	local info = {}
	local order = {}
	for i = 1, count do
		local name = names[i]
		order[i] = name
		info[name] = Lookup.info[name] or { misses = 0, nextAt = 0 }
	end
	Lookup.order, Lookup.info, Lookup.n = order, info, count
	if Lookup.cursor > count then Lookup.cursor = 1 end
	if count == 0 then
		Lookup.results, Lookup.nresults = {}, 0
	end
end

function Lookup.Configure(cfg)
	if type(cfg) ~= "table" then return end
	local c = Lookup.cfg
	if cfg.interval then c.interval = OV.clamp(cfg.interval, 0.2, 5) end
	if cfg.checks then c.maxChecks = OV.clamp(math.floor(cfg.checks), 1, 2000) end
	if cfg.report then c.report = OV.clamp(cfg.report, 5, 200) end
end

Lookup.tokens = {}            -- name -> the unit token of each player the game has loaded near you (Direction.lua asks it where they are)

function Lookup.Clear()
	Lookup.results, Lookup.nresults = {}, 0
	Lookup.tokens = {}
end

-- One name. Returns the distance in yards, or nil plus the reason it is not available.
local function checkOne(name)
	local ok, token = pcall(UnitTokenFromName, name, true)
	if not ok then
		Lookup.stats.errors = Lookup.stats.errors + 1
		return nil, "UnitTokenFromName raised an error: " .. tostring(token)
	end
	if not token then Lookup.tokens[name] = nil; return nil, "no such player loaded near you" end
	local okN, nm = pcall(UnitName, token)
	local okP, isPlayer = pcall(UnitIsPlayer, token)
	if not (okN and nm == name) then return nil, "the unit is called " .. tostring(nm) end
	if not (okP and isPlayer) then return nil, "not a player" end
	local okD, d2, checked = pcall(UnitDistanceSquared, token)
	if not (okD and checked and type(d2) == "number" and d2 >= 0) then Lookup.tokens[name] = nil; return nil, "the game gave no distance" end
	Lookup.tokens[name] = token
	return math.sqrt(d2)
end

Lookup.CheckOne = checkOne

-- Run one scan. Returns the number of results.
function Lookup.Scan(now)
	local st, cfg = Lookup.stats, Lookup.cfg
	local me = OV.character
	local checked, loaded, missed = 0, 0, 0
	local done = {}

	local function visit(name)
		local inf = Lookup.info[name]
		done[name] = true
		if name == me then
			inf.nextAt = now + 3600          -- never look yourself up
			return
		end
		checked = checked + 1
		inf.looked = true
		local d, why = checkOne(name)
		if d then
			inf.dist, inf.seenAt, inf.loadedAt, inf.misses, inf.nextAt = d, now, now, 0, 0
			loaded = loaded + 1
			-- narrate changes only: first sight, then a new figure when it moved 3 yd and 2 s have passed
			if inf.state ~= "loaded" then
				OV.Trace(string.format("lookup %s: FOUND by exact name, %.1f yd", name, d))
				inf.state, inf.tracedDist, inf.tracedAt = "loaded", d, now
			elseif math.abs(d - inf.tracedDist) >= 3 and now - inf.tracedAt >= 2 then
				OV.Trace(string.format("lookup %s: %.1f yd", name, d))
				inf.tracedDist, inf.tracedAt = d, now
			end
		else
			inf.misses = inf.misses + 1
			inf.nextAt = now + backoff(inf.misses)
			missed = missed + 1
			if inf.state ~= "missing" then
				-- a roster of hundreds would flood the chat with first misses; only say it for small rosters
				if inf.state == "loaded" or Lookup.n <= 10 then
					OV.Trace("lookup " .. name .. ": not available (" .. tostring(why) .. ")")
				end
				inf.state = "missing"
			end
		end
	end

	-- 1. everyone seen recently, every scan
	for i = 1, Lookup.n do
		local name = Lookup.order[i]
		local inf = Lookup.info[name]
		if inf.loadedAt and now - inf.loadedAt <= cfg.recent then visit(name) end
	end
	-- 2. names never looked up yet, within the budget
	for i = 1, Lookup.n do
		if checked >= cfg.maxChecks then break end
		local name = Lookup.order[i]
		if not done[name] and not Lookup.info[name].looked then visit(name) end
	end
	-- 3. the rest, round robin, within the budget
	local idx, looked = Lookup.cursor, 0
	while looked < Lookup.n and checked < cfg.maxChecks do
		local name = Lookup.order[idx]
		if not done[name] and now >= Lookup.info[name].nextAt then visit(name) end
		idx = OV.mod(idx, Lookup.n) + 1
		looked = looked + 1
	end
	Lookup.cursor = idx

	-- results: everyone whose last good lookup is recent enough and inside the report radius
	local res, nres = {}, 0
	for i = 1, Lookup.n do
		local name = Lookup.order[i]
		local inf = Lookup.info[name]
		if inf.seenAt and now - inf.seenAt <= cfg.resultTtl and inf.dist <= cfg.report then
			nres = nres + 1
			res[nres] = { name = name, dist = inf.dist, flags = OV.Frame.R_PLAYER }
		end
	end
	table.sort(res, function(a, b)
		if a.dist ~= b.dist then return a.dist < b.dist end
		return a.name < b.name
	end)
	Lookup.results, Lookup.nresults = res, nres

	st.scans = st.scans + 1
	st.checked, st.loaded, st.missed = st.checked + checked, st.loaded + loaded, st.missed + missed
	st.lastChecked, st.lastLoaded, st.lastMissed = checked, loaded, missed
	if st.lastScanAt then
		local gap = now - st.lastScanAt
		st.avgInterval = (st.avgInterval == 0) and gap or (st.avgInterval * 0.8 + gap * 0.2)
	end
	st.lastScanAt = now
	st.lastCost = OV.Now() - now          -- usually 0: the client clock only moves once a frame
	if Lookup.n > 0 and now - (Lookup.tracedScanAt or -100) >= 10 then
		Lookup.tracedScanAt = now
		OV.Trace(string.format("scan: roster %d, last scan checked %d, found %d, not available %d, %d reported",
			Lookup.n, checked, loaded, missed, nres))
	end
	return nres
end
