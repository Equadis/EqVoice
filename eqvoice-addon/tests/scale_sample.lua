-- Timings of the addon's own work at several roster sizes, for tools/scale_bench.py.
--   luajit tests/scale_sample.lua 50 100 250 500 1000
-- Prints one line per size:  <n> <parse ms> <scan ms> <frame ms> <poll of a changed file ms> <poll of a heartbeat ms>
-- These are LuaJIT times on the stub: only the relative growth with the roster means anything. The game runs Lua 5.0, several times
-- slower, and the cost of the game-side lookups (UnitTokenFromName / UnitDistanceSquared) is measured in the game: /ev diag scale.

local here = (string.gsub(arg[0], "[/\\][^/\\]*$", ""))
package.path = here .. "/?.lua;" .. package.path
local Stub = require("wow_stub")
Stub.root = here .. "/../EqVoice"

local function roster(n)
	local t = {}
	for i = 1, n do t[i] = string.format("Pl%04d", i) end
	return t
end

local function inbox(OV, names, checks, seq)
	local body = "C interval=0.5 checks=" .. checks .. " report=60\n"
	local i = 1
	while i <= table.getn(names) do
		local chunk = {}
		for j = i, math.min(i + 24, table.getn(names)) do chunk[table.getn(chunk) + 1] = names[j] end
		body = body .. "R " .. table.concat(chunk, ",") .. "\n"
		i = i + 25
	end
	return string.format("OVI1 %d 1760000000 %04X Me\n", seq or 1, OV.Frame.CRC16(body)) .. body
end

local function timeit(reps, fn)
	local t0 = os.clock()
	for _ = 1, reps do fn() end
	return (os.clock() - t0) / reps * 1000
end

for a = 1, table.getn(arg) do
	local n = tonumber(arg[a])
	local units = {}
	local OV = Stub.Login({ classic = true, fileApi = "WriteCustomFile", units = units })
	local names = roster(n)
	local checks = math.max(80, math.min(200, math.ceil(n * 0.5 / 3)))
	local text = inbox(OV, names, checks)
	local parseMs = timeit(30, function() OV.Inbox.Parse(text) end)
	local t = OV.Inbox.Parse(text)
	OV.Lookup.Configure(t.config)
	OV.Lookup.SetRoster(t.names, t.count)
	local now = 1000
	local scanMs = timeit(200, function() now = now + 0.5; OV.Lookup.Scan(now) end)
	local results = {}
	for i = 1, 200 do results[i] = { name = string.format("Pl%04d", i), dist = 10 + i * 0.2, flags = 1 } end
	local st = { counter = 1, flags = 0x0F, loginId = 1, wall = 1700000000, ack = 1, rosterSize = n, checked = checks, loaded = 200, misses = 0,
		intervalMs = 500, cost = 0, character = "Me", realm = "Testrealm", results = results }
	local frameMs = timeit(100, function() OV.Frame.Encode(st) end)
	-- what the addon pays once a second for the inbound file: a roster that changed (full check) and a heartbeat (header only)
	local changed, beats = {}, {}
	for i = 1, 30 do changed[i] = inbox(OV, names, checks + i, 1000 + i) end         -- a different config line each time: the body differs
	for i = 1, 200 do beats[i] = inbox(OV, names, checks, 2000 + i) end              -- identical body, only the sequence number moves
	local k = 0
	local pollChanged = timeit(30, function() k = k + 1; Stub.fs["eqvoice-in-Me.txt"] = changed[k]; OV.Inbox.Poll(now) end)
	Stub.fs["eqvoice-in-Me.txt"] = beats[1]; OV.Inbox.Poll(now)
	k = 1
	local pollBeat = timeit(199, function() k = k + 1; Stub.fs["eqvoice-in-Me.txt"] = beats[k]; OV.Inbox.Poll(now) end)
	print(string.format("%d %.3f %.3f %.3f %.3f %.3f", n, parseMs, scanMs, frameMs, pollChanged, pollBeat))
end
