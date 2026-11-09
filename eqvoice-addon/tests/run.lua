-- EqVoice addon test suite.   luajit eqvoice-addon/tests/run.lua
-- Runs on LuaJIT (5.1). Lua 5.0 compliance is checked separately by
-- tools/lint_lua50.py. Exit code is the number of failed checks (0 = green).

local here = arg and arg[0] and string.gsub(arg[0], "[/\\][^/\\]*$", "") or "."
package.path = here .. "/?.lua;" .. package.path
local Stub = require("wow_stub")
Stub.root = here .. "/../EqVoice"

local passed, failed, section = 0, 0, ""
local function check(cond, label)
	if cond then passed = passed + 1
	else
		failed = failed + 1
		print("  FAIL [" .. section .. "] " .. label)
	end
end
local function eq(a, b, label)
	if a == b then passed = passed + 1
	else
		failed = failed + 1
		print("  FAIL [" .. section .. "] " .. label .. "  got " .. tostring(a) .. "  want " .. tostring(b))
	end
end
local function sec(name) section = name; print("-- " .. name) end

local function sentOn(chan, prefix)
	local n = 0
	for i = 1, table.getn(Stub.sent) do
		local s = Stub.sent[i]
		if s.chan == chan and (not prefix or s.prefix == prefix) then n = n + 1 end
	end
	return n
end

-- ===================================================================== Util
sec("util")
do
	local OV = Stub.Boot()
	eq(OV.mod(-1, 5), 4, "mod of a negative number")
	eq(OV.mod(65537, 65536), 1, "mod wraps")
	eq(OV.xor(0xF0F0, 0x0FF0), 0xFF00, "xor 16 bit")
	eq(OV.xor(0, 0), 0, "xor zero")
	eq(OV.xor(255, 255), 0, "xor self")
	local parts, n = OV.split("a:b::c", ":")
	eq(n, 4, "split count"); eq(parts[3], "", "split keeps empty field"); eq(parts[4], "c", "split last")
	local _, n1 = OV.split("", ":")
	eq(n1, 1, "split empty string is one field")
	eq(OV.tohex("\0\255A"), "00FF41", "tohex")
	eq(OV.fromhex("00FF41"), "\0\255A", "fromhex")
	eq(OV.fromhex("0"), nil, "fromhex odd length")
	eq(OV.fromhex("ZZ"), nil, "fromhex junk")
	local sid = OV.NewSid()
	eq(string.len(sid), 8, "sid length")
	check(not string.find(sid, "[^0-9A-HJKMNP-TV-Z]"), "sid uses the safe alphabet")
	eq(OV.BytesToSid(OV.SidToBytes(sid)), sid, "sid <-> bytes round trip")
	eq(OV.SidToBytes("0000000I"), nil, "sid rejects I")
	eq(OV.SidToBytes("short"), nil, "sid rejects wrong length")
	eq(OV.SidToBytes("abcdefgh"), nil, "sid rejects lower case")
	eq(OV.BytesToSid(OV.SidToBytes("ZZZZZZZZ")), "ZZZZZZZZ", "sid max value")
	eq(OV.BytesToSid(OV.SidToBytes("00000000")), "00000000", "sid min value")
	eq(OV.Hash4("Elwynn Forest"), OV.Hash4("Elwynn Forest"), "hash deterministic")
	check(OV.Hash4("a") ~= OV.Hash4("b"), "hash differs")
	eq(string.len(OV.Hash4("")), 4, "hash width")
	-- log ring
	for i = 1, 200 do OV.Log("line " .. i) end
	local lines, k = OV.LogTail(5)
	eq(k, 5, "log tail count"); check(string.find(lines[5], "line 200", 1, true), "log tail newest last")
end

-- ===================================================================== Proto
sec("proto")
do
	local OV = Stub.Boot()
	local P = OV.Proto
	local t = P.EncodeHeartbeat("K7QX92MD", 1042, 4, 1)
	eq(t, "H:K7QX92MD:1042:4:1", "heartbeat text")
	check(string.len(t) <= 24, "heartbeat is tiny")
	local m = P.Decode(t)
	eq(m.sid, "K7QX92MD", "decode sid"); eq(m.seq, 1042, "decode seq"); eq(m.iv, 4, "decode iv")
	eq(m.flags, 1, "decode flags"); eq(m.loc, nil, "no loc")
	local tl = P.EncodeHeartbeat("K7QX92MD", 7, 4, 1, { zone = "2.7", x = 4123, y = 5870 })
	eq(tl, "H:K7QX92MD:7:4:1:2.7:4123:5870", "heartbeat with loc")
	local ml = P.Decode(tl)
	eq(ml.loc.zone, "2.7", "loc zone"); eq(ml.loc.x, 4123, "loc x"); eq(ml.loc.y, 5870, "loc y")
	local mh = P.Decode("H:K7QX92MD:7:4:1:hAB12:0:0")
	eq(mh.loc.zone, "hAB12", "hashed zone")
	local b = P.Decode(P.EncodeBye("K7QX92MD", 9))
	eq(b.t, "B", "bye kind"); eq(b.seq, 9, "bye seq")
	local bad = {
		{ "", "kind" }, { "X:K7QX92MD:1:4:1", "kind" }, { "H:K7QX92MD:1:4", "fields" },
		{ "H:k7qx92md:1:4:1", "sid" },
		{ "H:K7QX92MD:70000:4:1", "seq" }, { "H:K7QX92MD:-1:4:1", "charset" },
		{ "H:K7QX92MD:1:1:1", "iv" }, { "H:K7QX92MD:1:61:1", "iv" }, { "H:K7QX92MD:1:4:ZZ", "flags" },
		{ "H:K7QX92MD:1:4:1:2.7:4123", "fields" }, { "H:K7QX92MD:1:4:1:2.7:10000:5", "loc" },
		{ "H:K7QX92MD:1:4:1:x:1:1", "loc" }, { "H:K7QX92MD:1:4:1 ", "charset" },
		{ "H:K7QX92MD:1:4:1:2.7:1:1:extra", "fields" }, { "H:K7QX92MDX:1:4:1", "sid" },
		{ "B:K7QX92MD", "fields" }, { "H:K7QX92MD:1:4:\1", "charset" },
		{ "H|K7QX92MD|1|4|1", "charset" },
		{ string.rep("H", 81), "long" },
	}
	for i = 1, table.getn(bad) do
		local r, why = P.Decode(bad[i][1])
		eq(r, nil, "rejects " .. string.format("%q", bad[i][1]))
	end
	eq(P.Decode(nil), nil, "rejects nil")
	check(P.SeqNewer(5, 4), "seq newer"); check(not P.SeqNewer(4, 4), "seq equal is not newer")
	check(not P.SeqNewer(3, 4), "seq older"); check(P.SeqNewer(2, 65535), "seq newer across the wrap")
	check(not P.SeqNewer(65535, 2), "seq older across the wrap")
end

-- ==================================================================== Nearby
sec("nearby")
do
	local OV = Stub.Boot()
	local N, P = OV.Nearby, OV.Proto
	local function hb(sid, seq, iv, loc) return P.Decode(P.EncodeHeartbeat(sid, seq, iv or 4, 1, loc)) end
	local nb = N.New({ selfName = "Me", selfSid = "ME00ME00", radius = 300, maxEntries = 3 })

	eq(nb:Hear(hb("AAAAAAAA", 1), "Alice", "SAY", 100), "new", "first heartbeat is new")
	eq(nb.count, 1, "count 1")
	eq(nb:Hear(hb("AAAAAAAA", 2), "Alice", "SAY", 104), "refresh", "second refreshes")
	eq(nb:Hear(hb("AAAAAAAA", 2), "Alice", "SAY", 105), "replay", "same seq is a replay")
	eq(nb:Hear(hb("AAAAAAAA", 1), "Alice", "SAY", 106), "replay", "older seq is a replay")
	eq(nb:Hear(hb("ME00ME00", 1), "Mallory", "SAY", 106), "self", "own sid ignored")
	eq(nb:Hear(hb("BBBBBBBB", 1), "Me", "SAY", 106), "self", "own name ignored")
	eq(nb:Hear(hb("AAAAAAAA", 3), "Bob", "SAY", 107), "conflict", "second sender for a sid is a conflict")
	local list, n = nb:List(108)
	eq(n, 0, "contested sid is withheld from the list")
	local _, n2 = nb:List(108 + 61)
	eq(n2, 1, "conflict lapses after a minute")

	-- expiry: 3*iv + 1 = 13 s after last
	nb:Clear()
	nb:Hear(hb("AAAAAAAA", 1, 4), "Alice", "SAY", 100)
	local _, ex = nb:Expire(112.9)
	eq(ex, 0, "still alive before 3*iv+1")
	local gone, ex2 = nb:Expire(113.1)
	eq(ex2, 1, "expired after 3*iv+1"); eq(gone[1].sid, "AAAAAAAA", "expired entry returned")
	eq(nb.count, 0, "count back to zero")
	-- iv carried in the message sets the window
	nb:Hear(hb("AAAAAAAA", 5, 12), "Alice", "SAY", 200)
	local _, ex3 = nb:Expire(230)
	eq(ex3, 0, "a slow sender (iv 12) stays alive for 37 s")

	-- one sender, two sids
	nb:Clear()
	eq(nb:Hear(hb("AAAAAAAA", 1), "Alice", "SAY", 300), "new", "alice a")
	eq(nb:Hear(hb("CCCCCCCC", 1), "Alice", "SAY", 301), "dupe_sender", "second live sid from the same sender dropped")
	eq(nb:Hear(hb("CCCCCCCC", 2), "Alice", "SAY", 320), "new", "after the first lapses the new sid replaces it")
	eq(nb.count, 1, "only one entry for alice")

	-- bye
	eq(nb:Hear(P.Decode(P.EncodeBye("CCCCCCCC", 3)), "Mallory", "SAY", 321), "bye_unknown", "bye from the wrong sender ignored")
	eq(nb.count, 1, "still there")
	eq(nb:Hear(P.Decode(P.EncodeBye("CCCCCCCC", 3)), "Alice", "SAY", 321), "bye", "bye from the owner removes")
	eq(nb.count, 0, "gone")

	-- capacity
	nb:Clear()
	nb:Hear(hb("AAAAAAAA", 1), "A1", "SAY", 400); nb:Hear(hb("BBBBBBBB", 1), "A2", "SAY", 400)
	nb:Hear(hb("CCCCCCCC", 1), "A3", "SAY", 400)
	eq(nb:Hear(hb("DDDDDDDD", 1), "A4", "SAY", 400), "full", "table is capped")

	-- flood: >8 messages from one sender in 12 s
	nb:Clear()
	nb.maxEntries = 40
	local last
	for i = 1, 12 do last = nb:Hear(hb("AAAAAAAA", i), "Spammer", "SAY", 500 + i * 0.1) end
	eq(last, "flood", "flooding sender is cut off")

	-- ordering is by first heard, not by sid
	nb:Clear()
	nb:Hear(hb("ZZZZZZZZ", 1), "Z", "SAY", 600); nb:Hear(hb("AAAAAAAA", 1), "A", "SAY", 601)
	local l2 = nb:List(602)
	eq(l2[1].sid, "ZZZZZZZZ", "oldest first")
	local l3, c3 = nb:List(602, 1)
	eq(c3, 1, "list limit")

	-- group channels need a position
	nb:Clear()
	local mine = { zone = "2.7", x = 4000, y = 5000 }
	eq(nb:Hear(hb("AAAAAAAA", 1), "G1", "GUILD", 700, mine), "noloc", "guild heartbeat without loc is unusable")
	eq(nb:Hear(hb("AAAAAAAA", 1, 4, { zone = "2.7", x = 4100, y = 5100 }), "G1", "GUILD", 700, mine), "new", "close guildmate is nearby")
	eq(nb:Hear(hb("AAAAAAAA", 2, 4, { zone = "2.7", x = 6000, y = 5100 }), "G1", "GUILD", 704, mine), "left", "guildmate walks out of radius")
	eq(nb.count, 0, "removed on leaving radius")
	eq(nb:Hear(hb("BBBBBBBB", 1, 4, { zone = "2.8", x = 4000, y = 5000 }), "G2", "GUILD", 705, mine), "far", "other zone is far")
	eq(nb:Hear(hb("BBBBBBBB", 2, 4, { zone = "hAB12", x = 0, y = 0 }), "G2", "RAID", 706, { zone = "hAB12", x = 0, y = 0 }), "new", "instance: same named zone, no positions")
	eq(nb:Hear(hb("CCCCCCCC", 1, 4, { zone = "hAB12", x = 0, y = 0 }), "G3", "RAID", 706, mine), "far", "different zone")
	check(N.NearEnough({ zone = "a", x = 10, y = 10 }, { zone = "a", x = 10, y = 310 }, 300), "exactly on the radius counts")
	check(not N.NearEnough({ zone = "a", x = 10, y = 10 }, { zone = "a", x = 10, y = 311 }, 300), "just outside does not")
	check(not N.NearEnough({ zone = "a", x = 0, y = 0 }, { zone = "a", x = 5, y = 5 }, 300), "positionless vs positioned is not nearby")
end

-- ===================================================================== Frame
sec("frame (BF2)")
do
	local OV = Stub.Boot()
	local F = OV.Frame
	eq(F.CRC16("123456789"), 0x29B1, "CRC-16/CCITT-FALSE check value")
	eq(F.CRC16(""), 0xFFFF, "CRC of empty")
	local st = {
		counter = 513, flags = 1 + 2 + 8 + 32, loginId = 3000000000, wall = 1760000000, ack = 40000,
		rosterSize = 321, checked = 80, loaded = 3, misses = 77, intervalMs = 503, cost = 12,
		character = "Alice", realm = "Turtle",
		results = { { name = "Bob", dist = 18.4, flags = 1 }, { name = "Carol", dist = 41, flags = 3 }, { name = "Dave", dist = 200, flags = 1 } },
	}
	local bytes = F.Encode(st)
	check(bytes ~= nil, "encodes")
	eq(string.byte(bytes, 1), 2, "version 2")
	local back, why = F.Decode(bytes)
	check(back ~= nil, "decodes " .. tostring(why))
	eq(back.counter, 513, "counter is 16 bits"); eq(back.flags, 43, "flags"); eq(back.loginId, 3000000000, "login id is 32 bits")
	eq(back.wall, 1760000000, "wall clock"); eq(back.ack, 40000, "roster ack"); eq(back.rosterSize, 321, "roster size")
	eq(back.checked, 80, "checked"); eq(back.loaded, 3, "loaded"); eq(back.misses, 77, "misses")
	eq(back.intervalMs, 503, "scan interval"); eq(back.cost, 12, "scan cost")
	eq(back.character, "Alice", "character"); eq(back.realm, "Turtle", "realm")
	eq(table.getn(back.results), 3, "three results")
	eq(back.results[1].name, "Bob", "name"); eq(back.results[1].dist, 18.5, "distance rounds to the nearest half yard")
	eq(back.results[2].flags, 3, "result flags"); eq(back.results[3].dist, 127, "distance caps at 127 yards")
	-- cells
	local cells = F.ToCells(bytes)
	eq(table.getn(cells), 320, "320 cells")
	for i = 1, 8 do eq(cells[i], F.ANCHOR[i], "anchor cell " .. i) end
	local allSmall = true
	for i = 1, 320 do if cells[i] < 0 or cells[i] > 63 or cells[i] ~= math.floor(cells[i]) then allSmall = false end end
	check(allSmall, "every cell is an integer 0..63")
	eq(F.FromCells(cells), bytes, "cells -> bytes round trip")
	local r, g, b = F.CellRGB(63); eq(r, 1, "white r"); eq(g, 1, "white g"); eq(b, 1, "white b")
	r, g, b = F.CellRGB(0); eq(r + g + b, 0, "black")
	r, g, b = F.CellRGB(48); eq(r, 1, "red r"); eq(g, 0, "red g")
	r, g, b = F.CellRGB(27); eq(string.format("%.4f", r), string.format("%.4f", 85 / 255), "mid levels are 85/255")
	-- damage
	local bad = string.sub(bytes, 1, 8) .. string.char((string.byte(bytes, 9) + 1) % 256) .. string.sub(bytes, 10)
	eq(F.Decode(bad), nil, "a flipped byte fails the CRC")
	eq(F.Decode(string.sub(bytes, 1, 6)), nil, "a truncated frame is rejected")
	eq(F.Decode(string.char(1) .. string.sub(bytes, 2)), nil, "wrong version rejected")
	-- names
	local clean = F.Decode(F.Encode({ counter = 0, flags = 0, character = "Al ice", realm = "R", results = { { name = "A\1B C,D", dist = 1 }, { name = "", dist = 2 } } }))
	eq(clean.character, "Alice", "spaces stripped from the character name"); eq(clean.results[1].name, "ABCD", "control characters, spaces and commas stripped")
	eq(table.getn(clean.results), 1, "empty names are skipped")
	check(string.len(F.Decode(F.Encode({ counter = 0, flags = 0, character = string.rep("x", 40), realm = "R", results = {} })).character) == 24, "names clip to 24 bytes")
	-- the optical copy is the same state cut to fit 234 bytes, nearest first
	local many = {}
	for i = 1, 60 do many[i] = { name = "Player" .. i, dist = i * 0.5, flags = 1 } end
	local full = F.Encode({ counter = 1, flags = 1, loginId = 1, wall = 1, character = "Me", realm = "R", results = many })
	eq(table.getn(F.Decode(full).results), 60, "the file frame carries all 60")
	local small = F.Encode({ counter = 1, flags = 1, loginId = 1, wall = 1, character = "Me", realm = "R", results = many }, F.OPTICAL_MAX)
	check(string.len(small) <= 234, "optical frame fits 234 bytes (" .. string.len(small) .. ")")
	local sd = F.Decode(small)
	check(table.getn(sd.results) > 5 and table.getn(sd.results) < 60, "some results dropped: " .. table.getn(sd.results))
	eq(sd.results[1].name, "Player1", "the nearest are kept"); eq(math.floor(sd.flags / 128) % 2, 1, "truncated flag set")
	eq(math.floor(F.Decode(full).flags / 128) % 2, 0, "and not set on the full frame")
	local sc = F.ToCells(small)
	check(sc ~= nil, "the truncated frame fits the grid")
	eq(F.Decode(F.FromCells(sc)).results[1].name, "Player1", "and survives the cells")
	eq(table.getn(F.Decode(F.Encode({ counter = 0, flags = 0, character = "Me", realm = "R", results = {} })).results), 0, "empty result list")
	local huge = {}
	for i = 1, 300 do huge[i] = { name = "N" .. i, dist = 1 } end
	eq(table.getn(F.Decode(F.Encode({ counter = 0, flags = 0, character = "Me", realm = "R", results = huge })).results), 200, "never more than 200 results")
end

-- ===================================================================== Comm
sec("comm")
do
	local OV = Stub.Boot()
	local C = OV.Comm
	local b = C.NewBucket(3, 0.5, 0)
	check(C.Take(b, 0, 30) and C.Take(b, 0, 30) and C.Take(b, 0, 30), "bucket starts full (3)")
	check(not C.Take(b, 0, 30), "fourth in the same instant is refused")
	check(not C.Take(b, 1, 30), "half a token is not enough")
	check(C.Take(b, 2, 30), "one token back after 2 s")
	-- per-minute ceiling
	local b2 = C.NewBucket(1000, 1000, 0)
	local ok = 0
	for i = 1, 40 do if C.Take(b2, i * 0.5, 30) then ok = ok + 1 end end
	eq(ok, 30, "30 per minute ceiling")
	check(C.Take(b2, 61, 30), "window slides")
	eq(C.Interval(0), 4, "interval alone"); eq(C.Interval(10), 6, "interval with 10 nearby")
	eq(C.Interval(500), 12, "interval is capped")
	local env = { guild = true, raid = false, party = true }
	local ch, n = C.Channels("GROUP", env, {})
	eq(n, 2, "group = guild + party"); eq(ch[1], "GUILD", "guild first"); eq(ch[2], "PARTY", "party")
	ch, n = C.Channels("GROUP", { guild = false, raid = true, party = true }, {})
	eq(n, 1, "raid replaces party"); eq(ch[1], "RAID", "raid")
	ch, n = C.Channels("AUTO", env, {})
	eq(ch[1], "GUILD", "AUTO without proof uses group channels")
	ch, n = C.Channels("AUTO", env, { say = { ok = true } })
	eq(n, 1, "AUTO after a proven SAY uses SAY only"); eq(ch[1], "SAY", "say")
	ch, n = C.Channels("AUTO", env, { say = { ok = false } })
	eq(ch[1], "GUILD", "a failed SAY test does not enable SAY")
	ch, n = C.Channels("SAY", { guild = false, raid = false, party = false }, nil)
	eq(ch[1], "SAY", "forced SAY")
	ch, n = C.Channels("GROUP", { guild = false, raid = false, party = false }, nil)
	eq(n, 0, "no group, no channel")
end

-- ================================================================ integration
sec("integration: group mode")
do
	local OV = Stub.Login({ inGuild = true, db = { transport = "AUTO" } })
	local P = OV.Proto
	eq(OV.db.enabled, true, "defaults applied")
	check(OV.sid and string.len(OV.sid) == 8, "session id created")
	Stub.Advance(10)
	local hb = sentOn("GUILD", "OV1")
	check(hb >= 2 and hb <= 4, "about one heartbeat per 4 s on GUILD (got " .. hb .. ")")
	eq(OV.Comm.stats.errors, 0, "the client accepted every heartbeat (no bad escape codes, length ok)")
	local longest = 0
	for i = 1, table.getn(Stub.sent) do
		if string.find(Stub.sent[i].text, "|", 1, true) then longest = -1 end
	end
	eq(longest, 0, "no message contains the | escape character")
	local first = Stub.sent[1]
	local m = P.Decode(first.text)
	check(m ~= nil, "heartbeat parses"); eq(m.sid, OV.sid, "carries the session id")
	check(m.loc ~= nil, "group heartbeat carries a location")
	eq(m.loc.zone, "2.7", "zone from continent.zone"); eq(m.loc.x, 4123, "x"); eq(m.loc.y, 5870, "y")
	-- never more than 30 per minute even over a long time
	Stub.sent = {}
	Stub.Advance(300)
	check(table.getn(Stub.sent) <= 30 * 5, "rate ceiling over 5 minutes (sent " .. table.getn(Stub.sent) .. ")")
	check(table.getn(Stub.sent) >= 60, "and it keeps going (sent " .. table.getn(Stub.sent) .. ")")

	-- a guildmate nearby appears, then disappears
	local t0 = Stub.now
	Stub.Hear("H:AAAAAAAA:1:4:1:2.7:4200:5800", "GUILD", "Alice")
	eq(OV.nearby.count, 1, "alice is nearby")
	Stub.Advance(1)
	-- far guildmate
	Stub.Hear("H:BBBBBBBB:1:4:1:2.7:9000:100", "GUILD", "Bob")
	eq(OV.nearby.count, 1, "bob is in the guild but far away")
	-- Alice stops talking; after 3*iv+1 she is dropped
	Stub.Advance(14)
	eq(OV.nearby.count, 0, "alice expired")
	-- liveness: the bridge counter keeps advancing while nothing changes
	local c1 = OV.Frame.Decode(OV.lastBytes).counter
	Stub.Advance(3)
	check(OV.Frame.Decode(OV.lastBytes).counter ~= c1, "frames keep flowing while idle")
	-- bye
	Stub.Hear("H:CCCCCCCC:1:4:1:2.7:4150:5850", "GUILD", "Carol")
	eq(OV.nearby.count, 1, "carol nearby")
	Stub.Hear("B:CCCCCCCC:2", "GUILD", "Carol")
	eq(OV.nearby.count, 0, "carol said goodbye")
	-- garbage is counted, not fatal
	local before = OV.Comm.stats.malformed
	Stub.Hear("H:nonsense", "GUILD", "Dave")
	Stub.Hear(string.rep("x", 300), "GUILD", "Dave")
	eq(OV.Comm.stats.malformed, before + 2, "malformed messages counted")
	-- someone else's prefix is not ours
	local recv = OV.Comm.stats.recv
	Stub.FireEvent("CHAT_MSG_ADDON", "BigWigs", "x", "RAID", "Eve")
	eq(OV.Comm.stats.recv, recv, "other prefixes ignored")
	-- off sends a goodbye and empties the frame
	Stub.sent = {}
	OV.Slash("off")
	check(sentOn("GUILD", "OV1") >= 1, "off sends a goodbye")
	check(string.find(Stub.sent[1].text, "^B:"), "and it is a B message")
	eq(OV.Frame.Decode(OV.lastBytes).flags % 2, 0, "the bridge frame says the addon is off")
	Stub.sent = {}
	Stub.Advance(20)
	eq(table.getn(Stub.sent), 0, "silent while off")
	OV.Slash("on")
	Stub.Advance(3)
	check(table.getn(Stub.sent) >= 1, "heartbeats resume on 'on'")
end

sec("integration: say mode and early reply")
do
	local OV = Stub.Login({})
	OV.Slash("transport say")
	Stub.Advance(6)
	check(sentOn("SAY", "OV1") >= 1, "SAY heartbeats sent")
	eq(OV.Comm.stats.errors, 0, "SAY heartbeats pass the client's checks")
	local m = OV.Proto.Decode(Stub.sent[1].text)
	eq(m.loc, nil, "no location on a local channel")
	-- a stranger appears via SAY: no position needed
	Stub.sent = {}
	OV.Comm.lastSend = Stub.now - 3          -- we have not spoken for a while
	OV.Comm.nextHb = Stub.now + 10           -- and the next one is far off
	Stub.Hear("H:AAAAAAAA:1:4:1", "SAY", "Stranger")
	eq(OV.nearby.count, 1, "stranger heard over SAY is nearby")
	check(OV.Comm.nextHb - Stub.now <= 1.6, "a new neighbour pulls our next heartbeat forward")
	Stub.Advance(2)
	check(sentOn("SAY", "OV1") >= 1, "and we answer within two seconds")
	-- but a burst of new neighbours cannot drag us into a storm
	local sentBefore = sentOn("SAY", "OV1")
	for i = 1, 6 do Stub.Hear("H:" .. string.rep(string.char(64 + i), 8) .. ":1:4:1", "SAY", "Crowd" .. i) end
	Stub.Advance(1)
	check(sentOn("SAY", "OV1") - sentBefore <= 1, "a crowd arriving does not multiply our heartbeats")
	-- yell too
	local c0 = OV.nearby.count
	Stub.Hear("H:GGGGGGGG:1:4:1", "YELL", "Shouter")
	eq(OV.nearby.count, c0 + 1, "YELL counts as local")
end

sec("integration: error handling")
do
	local OV = Stub.Login({ inGuild = true, db = { transport = "AUTO" } })
	Stub.sendError = "boom"
	Stub.Advance(10)
	check(OV.Comm.stats.errors >= 1, "send errors are counted")
	check(OV.Comm.stats.errors <= 2, "a refused channel is not hammered (errors in 10 s: " .. OV.Comm.stats.errors .. ")")
	check(OV.Comm.lastError and string.find(OV.Comm.lastError, "boom", 1, true), "last error kept")
	Stub.sendError = nil
	Stub.Advance(10)
	check(OV.Comm.stats.sent >= 1, "recovers when sending works again")
	-- not in a guild, not in a group, SAY unproven: nothing to send on
	local OV2 = Stub.Login({})
	Stub.Advance(10)
	eq(table.getn(Stub.sent), 0, "no channel -> nothing sent")
	-- map open: no SetMapToCurrentZone, location falls back to a zone hash
	local OV3 = Stub.Login({ inGuild = true, mapOpen = true, db = { transport = "AUTO" } })
	Stub.Advance(6)
	local m = OV3.Proto.Decode(Stub.sent[1].text)
	check(string.find(m.loc.zone, "^h%x%x%x%x$") ~= nil, "map open -> hashed zone, no position")
	eq(m.loc.x, 0, "no x")
end

-- helper: a roster file as the desktop client writes it
local function inboxText(OV, seq, character, names, cfg)
	local body = ""
	if cfg then body = body .. "C " .. cfg .. "\n" end
	if names then body = body .. "R " .. table.concat(names, ",") .. "\n" end
	return string.format("OVI1 %d %d %04X %s\n", seq, 1760000000 + seq, OV.Frame.CRC16(body), character) .. body
end

sec("bridge sinks")
do
	local units = { ["0xB"] = { name = "Bob", player = true, dist = 18.4 } }
	local OV = Stub.Login({ classic = true, fileApi = "WriteCustomFile", units = units })
	-- nothing is painted in a corner of the screen unless asked for (it was the default once: a block of coloured squares)
	Stub.Advance(1)
	eq(OV.db.optical, false, "the optical block is off by default")
	for i = 1, table.getn(Stub.frames) do check(table.getn(Stub.frames[i].textures) ~= 321, "and no frame of 320 cells is built") end
	OV.db.optical, OV.db.opticalChosen = true, true                          -- this test is about the optical copy: ask for it
	OV.Bridge.optical = true
	Stub.fs["eqvoice-in.txt"] = inboxText(OV, 1, "Me", { "Bob", "Carol" })
	Stub.Advance(3)
	local calls = table.getn(Stub.fileCalls)
	check(calls >= 2, "file bridge writes through WriteCustomFile")
	local last
	for i = calls, 1, -1 do if Stub.fileCalls[i].name == "eqvoice.txt" then last = Stub.fileCalls[i] break end end
	eq(last.name, "eqvoice.txt", "file name"); eq(last.mode, "w", "overwrite mode")
	local hex = string.match and string.match(last.content, "^OVB2 (%x+)")
	check(hex ~= nil, "content is 'OVB2 <hex>'")
	local st = OV.Frame.Decode(OV.fromhex(hex))
	check(st ~= nil, "file content decodes")
	eq(st.character, "Me", "frame carries the character"); eq(st.results[1].name, "Bob", "and the lookup result")
	eq(st.results[1].dist, 18.5, "with its distance")
	-- optical: find the bridge frame and compare painted colours with the cells
	local bf
	for i = 1, table.getn(Stub.frames) do
		if table.getn(Stub.frames[i].textures) == 321 then bf = Stub.frames[i] end
	end
	check(bf ~= nil, "bridge frame built (1 backing + 320 cells)")
	eq(bf.strata, "TOOLTIP", "on top of everything")
	eq(bf.w, 32 * 6, "frame width = 32 cells"); eq(bf.h, 10 * 6, "frame height = 10 cells")
	eq(bf.pt[1], "TOPLEFT", "top left corner by default")
	local cells = OV.Frame.ToCells(OV.lastBytes)
	local same = true
	for i = 1, 320 do
		local r, g, b = OV.Frame.CellRGB(cells[i])
		local t = bf.textures[i + 1]
		if t.r ~= r or t.g ~= g or t.b ~= b or t.a ~= 1 then same = false end
	end
	check(same, "painted colours equal the encoded cells")
	-- switching sinks
	OV.Slash("bridge file")
	check(bf.shown == false, "optical hidden when only the file bridge is wanted")
	local before = table.getn(Stub.fileCalls)
	Stub.Advance(2)
	check(table.getn(Stub.fileCalls) > before, "file bridge still writing")
	Stub.fileError = "disk full"
	Stub.Advance(2)
	check(OV.Bridge.stats.fileErrors >= 1 and OV.Bridge.lastFileError ~= nil, "file errors are caught and counted")
	Stub.fileError = nil
	OV.Slash("bridge corner bottomright")
	eq(OV.db.corner, "BOTTOMRIGHT", "corner saved")
	OV.Slash("bridge size 8")
	eq(OV.db.cell, 8, "cell size saved")
	OV.Slash("bridge both")
	Stub.Advance(1)
	local bf2
	for i = 1, table.getn(Stub.frames) do
		if table.getn(Stub.frames[i].textures) == 321 and Stub.frames[i].w == 32 * 8 then bf2 = Stub.frames[i] end
	end
	check(bf2 ~= nil and bf2.pt[1] == "BOTTOMRIGHT", "rebuilt at the new size and corner")
	-- ExportFile variant
	local OV4 = Stub.Login({ fileApi = "ExportFile" })
	Stub.Advance(2)
	eq(Stub.fileCalls[1].api, "ExportFile", "falls back to ExportFile"); eq(Stub.fileCalls[1].name, "eqvoice", "name without .txt")
	-- no file api at all
	local OV5 = Stub.Login({})
	Stub.Advance(2)
	eq(table.getn(Stub.fileCalls), 0, "no file API -> no file writes, no errors")
	eq(OV5.Bridge.stats.fileErrors, 0, "and no error counted")
end

sec("inbox (desktop -> addon)")
do
	local OV = Stub.Boot()
	local I = OV.Inbox
	OV.character = "Me"
	local ok = inboxText(OV, 7, "Me", { "Alice", "Bob", "Alice", "x", "Has Space", "Waytoolongnamethatexceedstwentyfourbytes", "Carol" }, "radius=40 interval=0.5 report=60 checks=80 bogus")
	local t, why = I.Parse(ok)
	check(t ~= nil, "parses " .. tostring(why))
	eq(t.seq, 7, "seq"); eq(t.character, "Me", "character"); eq(t.config.radius, 40, "config radius"); eq(t.config.interval, 0.5, "config interval")
	eq(t.config.checks, 80, "config checks"); eq(t.count, 3, "names: duplicates, too short, spaces and too long are dropped (got " .. t.count .. ")")
	eq(t.names[1], "Alice", "order kept"); eq(t.names[3], "Carol", "third")
	eq(I.Parse((string.gsub(ok, "\n", "\r\n"))) ~= nil, true, "CRLF line endings are tolerated")
	local torn = string.sub(ok, 1, string.len(ok) - 6)
	local nt, nwhy = I.Parse(torn)
	eq(nt, nil, "a torn read fails"); eq(nwhy, "crc", "on the CRC")
	eq(I.Parse("nonsense"), nil, "no header"); eq(I.Parse(""), nil, "empty")
	eq(I.Parse("OVI2 1 2 0000 Me\n"), nil, "wrong magic"); eq(I.Parse("OVI1 a b 0000 Me\n"), nil, "bad numbers")
	eq(I.Parse(nil), nil, "nil")
	local withTest = inboxText(OV, 8, "Me", nil)
	local body = "T hello\n"
	local tt = I.Parse(string.format("OVI1 9 1 %04X Me\n", OV.Frame.CRC16(body)) .. body)
	eq(tt.test, "hello", "test message")
	local u = I.Parse(string.format("OVI1 9 1 %04X Me\n", OV.Frame.CRC16("Z future line\n")) .. "Z future line\n")
	check(u ~= nil, "unknown line types are ignored (forward compatible)")
end

sec("inbox polling, freshness and identity")
do
	local OV = Stub.Login({ classic = true, fileApi = "WriteCustomFile", units = {} })
	local I = OV.Inbox
	eq(I.Fresh(Stub.now), false, "no file: not fresh"); eq(I.present, false, "no file present")
	Stub.fs["eqvoice-in.txt"] = inboxText(OV, 1, "Me", { "Alice", "Bob" })
	Stub.Advance(1.5)
	eq(I.Fresh(Stub.now), true, "fresh after the first file"); eq(I.count, 2, "two names"); eq(OV.Lookup.n, 2, "roster handed to the lookup")
	eq(I.stats.accepted, 1, "one file accepted")
	Stub.Advance(5)
	eq(I.stats.accepted, 1, "an unchanged file is not accepted again")
	-- the client keeps the file alive by changing seq
	Stub.fs["eqvoice-in.txt"] = inboxText(OV, 2, "Me", { "Alice", "Bob" })
	Stub.Advance(1.5)
	eq(I.seq, 2, "a new sequence number is news even with the same roster")
	eq(I.stats.refreshed, 1, "taken as a heartbeat: no second full parse of an identical roster")
	eq(I.stats.accepted, 1, "so the roster was not 'accepted' again")
	-- fail closed: no change for longer than the TTL
	Stub.Advance(16)
	eq(I.Fresh(Stub.now), false, "stale after 15 s without a new sequence number")
	local st = OV.Frame.Decode(OV.lastBytes)
	eq(math.floor(st.flags / 8) % 2, 0, "the bridge frame says the roster is stale")
	-- a file written for another character is ignored
	Stub.fs["eqvoice-in.txt"] = inboxText(OV, 3, "Mallory", { "Alice" })
	Stub.Advance(1.5)
	eq(I.idMismatch, true, "wrong character detected"); eq(I.Fresh(Stub.now), false, "and never fresh"); eq(OV.Lookup.n, 0, "roster emptied")
	st = OV.Frame.Decode(OV.lastBytes)
	eq(math.floor(st.flags / 64) % 2, 1, "identity mismatch flag in the frame")
	-- corrupted file: ignored, counted
	local bad = I.stats.bad
	Stub.fs["eqvoice-in.txt"] = "OVI1 4 5 0000 Me\nR Alice\n"
	Stub.Advance(1.5)
	eq(I.stats.bad, bad + 1, "a bad CRC is counted")
	-- a good one recovers
	Stub.fs["eqvoice-in.txt"] = inboxText(OV, 5, "Me", { "Alice" })
	Stub.Advance(1.5)
	eq(I.Fresh(Stub.now), true, "recovers"); eq(I.idMismatch, false, "mismatch cleared"); eq(OV.Lookup.n, 1, "roster restored")
	-- the file disappears
	Stub.fs["eqvoice-in.txt"] = nil
	Stub.Advance(1.5)
	eq(I.present, false, "missing file noticed")
end

sec("lookup scheduler")
do
	local units = {
		["0xA"] = { name = "Alice", player = true, dist = 13.2 },
		["0xB"] = { name = "Bob", player = true, dist = 28.6 },
		["0xC"] = { name = "Carol", player = true, dist = 70 },
		["0xW"] = { name = "Wolf", player = false, dist = 5 },
		["0xM"] = { name = "Me", player = true, dist = 0 },
	}
	local OV = Stub.Login({ classic = true, units = units })
	local L = OV.Lookup
	OV.character = "Me"
	L.SetRoster({ "Alice", "Bob", "Carol", "Wolf", "Me", "Nobody" }, 6)
	local n = L.Scan(100)
	eq(L.stats.lastChecked, 5, "five checked (not myself)"); eq(L.stats.lastLoaded, 3, "Alice, Bob and Carol are loaded (the wolf is not a player)")
	eq(L.stats.lastMissed, 2, "the wolf and Nobody are misses")
	eq(n, 2, "results: only those inside the 60 yd report radius")
	eq(L.results[1].name, "Alice", "nearest first"); eq(string.format("%.1f", L.results[1].dist), "13.2", "distance in yards")
	eq(L.results[2].name, "Bob", "second")
	-- results expire when the player stops being loaded
	units["0xA"] = nil
	L.Scan(100.5)
	eq(L.nresults, 2, "Alice is still reported for a moment (result TTL)")
	L.Scan(102)
	eq(L.nresults, 1, "and dropped once her last good lookup is older than the TTL")
	-- backoff for names that keep missing
	local inf = L.info["Nobody"]
	check(inf.misses >= 3, "misses counted")
	check(inf.nextAt > 102, "a name that keeps missing is rescheduled")
	local m0 = inf.misses
	L.Scan(102.1)
	eq(inf.misses, m0, "and not asked again before its time")
	-- report radius from the inbox configuration
	L.Configure({ report = 20, interval = 1, checks = 5 })
	eq(L.cfg.report, 20, "report radius"); eq(L.cfg.interval, 1, "interval"); eq(L.cfg.maxChecks, 5, "check budget")
	L.Configure({ report = 20000, interval = 0.001, checks = 99999 })
	eq(L.cfg.report, 200, "report radius clamped"); eq(L.cfg.interval, 0.2, "interval clamped"); eq(L.cfg.maxChecks, 2000, "budget clamped")
	L.Configure(nil)
end

sec("lookup scheduler: a big roster")
do
	local units = {}
	local names = {}
	for i = 1, 500 do
		names[i] = "Player" .. string.format("%03d", i)
		if i == 123 or i == 456 then units["0x" .. i] = { name = names[i], player = true, dist = 10 + i / 100 } end
	end
	local OV = Stub.Login({ classic = true, units = units })
	local L = OV.Lookup
	OV.character = "Me"
	L.Configure({ checks = 80 })
	L.SetRoster(names, 500)
	local maxChecked, totalFirstPass = 0, 0
	local now = 200
	for scan = 1, 7 do
		L.Scan(now)
		if L.stats.lastChecked > maxChecked then maxChecked = L.stats.lastChecked end
		now = now + 0.5
	end
	check(maxChecked <= 80, "never more than the budget per scan (max " .. maxChecked .. ")")
	eq(L.nresults, 2, "both nearby players found within 7 scans of a 500 name roster")
	-- once seen they are checked every scan, while the rest back off
	local before = L.stats.lastChecked
	L.Scan(now)
	check(L.info[names[123]].loadedAt ~= nil, "the loaded names are tracked")
	local seenFirst = L.info[names[123]].seenAt
	L.Scan(now + 0.5)
	check(L.info[names[123]].seenAt > seenFirst, "a recently seen player is refreshed on every scan")
	-- a name far down the cursor still gets its turn
	for i = 1, 20 do L.Scan(now + i) end
	local stale = 0
	for i = 1, 500 do if not L.info[names[i]].nextAt then stale = stale + 1 end end
	eq(stale, 0, "every name has a schedule")
	-- cost counters
	check(L.stats.scans >= 28, "scans counted"); check(L.stats.checked > 0 and L.stats.missed > 0 and L.stats.loaded > 0, "totals counted")
	-- an API that raises is survived and counted
	Stub.nameplateError = nil
	local OV2 = Stub.Login({ classic = true, units = {} })
	OV2.character = "Me"
	local oldFn = _G.UnitTokenFromName
	_G.UnitTokenFromName = function() error("boom") end
	OV2.Lookup.SetRoster({ "Alice" }, 1)
	OV2.Lookup.Scan(1)
	eq(OV2.Lookup.stats.errors, 1, "an erroring lookup is counted, not fatal")
	_G.UnitTokenFromName = oldFn
	-- no ClassicAPI: unavailable
	local OV3 = Stub.Login({})
	eq(OV3.Lookup.Available(), false, "unavailable without UnitTokenFromName")
end

sec("pipeline: roster in, distances out")
do
	local units = {
		["0xB"] = { name = "Bob", player = true, dist = 18.4 },
		["0xC"] = { name = "Carol", player = true, dist = 55 },
	}
	local OV = Stub.Login({ classic = true, fileApi = "WriteCustomFile", units = units })
	check(OV.loginId > 0 and OV.loginId < 4294967296, "a login id exists")
	eq(OV.character, "Me", "character from the client"); eq(OV.realm, "Testrealm", "realm from the client")
	-- before any roster: no results, roster not fresh
	Stub.Advance(2)
	local st = OV.Frame.Decode(OV.lastBytes)
	eq(table.getn(st.results), 0, "no results without a roster"); eq(math.floor(st.flags / 8) % 2, 0, "roster not fresh")
	eq(math.floor(st.flags / 32) % 2, 1, "lookups available flag"); eq(st.character, "Me", "identity in the frame")
	eq(st.loginId, OV.loginId, "login id in the frame")
	-- the client writes a roster
	Stub.fs["eqvoice-in.txt"] = inboxText(OV, 11, "Me", { "Bob", "Carol", "Dave" }, "report=60")
	Stub.Advance(2)
	st = OV.Frame.Decode(OV.lastBytes)
	eq(st.ack, 11, "the frame acknowledges the roster sequence"); eq(st.rosterSize, 3, "roster size reported")
	eq(table.getn(st.results), 2, "Bob and Carol are loaded and inside the report radius")
	eq(st.results[1].name, "Bob", "nearest first"); eq(st.results[1].dist, 18.5, "distance")
	eq(st.results[2].name, "Carol", "second"); eq(st.results[2].dist, 55, "distance")
	eq(st.checked, 3, "checked metric"); eq(st.loaded, 2, "loaded metric"); eq(st.misses, 1, "missed metric")
	check(st.intervalMs >= 400 and st.intervalMs <= 600, "scan interval reported (" .. st.intervalMs .. " ms)")
	-- distances follow the game, about twice a second
	units["0xB"].dist = 31
	Stub.Advance(1)
	st = OV.Frame.Decode(OV.lastBytes)
	eq(st.results[1].name, "Bob", "still there"); eq(st.results[1].dist, 31, "the new distance is published within a second")
	-- Bob walks out of the report radius's reach: unloaded
	units["0xB"] = nil
	Stub.Advance(3)
	st = OV.Frame.Decode(OV.lastBytes)
	eq(table.getn(st.results), 1, "Bob is no longer reported once he is not loaded"); eq(st.results[1].name, "Carol", "Carol remains")
	-- /ev status and nearby do not raise and show the numbers
	Stub.chat = {}
	OV.Slash("status"); OV.Slash("nearby")
	local chat = table.concat(Stub.chat, "\n")
	check(string.find(chat, "FRESH", 1, true) and string.find(chat, "roster 3", 1, true), "status shows the inbox and roster: " .. chat)
	check(string.find(chat, "Carol 55.0", 1, true), "and the nearby list")
	-- fail closed: the roster stops being refreshed
	Stub.Advance(16)
	st = OV.Frame.Decode(OV.lastBytes)
	eq(table.getn(st.results), 0, "stale roster -> nobody reported"); eq(math.floor(st.flags / 8) % 2, 0, "roster flag cleared")
	-- off
	Stub.fs["eqvoice-in.txt"] = inboxText(OV, 12, "Me", { "Carol" })
	Stub.Advance(2)
	eq(table.getn(OV.Frame.Decode(OV.lastBytes).results), 1, "back to work with a new roster")
	OV.Slash("off")
	Stub.Advance(1)
	st = OV.Frame.Decode(OV.lastBytes)
	eq(st.flags % 2, 0, "addon off: flag cleared"); eq(table.getn(st.results), 0, "and nobody reported")
	-- a configuration change takes effect: tighter report radius
	OV.Slash("on")
	Stub.fs["eqvoice-in.txt"] = inboxText(OV, 13, "Me", { "Carol" }, "report=40")
	Stub.Advance(2)
	eq(table.getn(OV.Frame.Decode(OV.lastBytes).results), 0, "Carol at 55 yd is outside a 40 yd report radius")
	-- test message from the client is logged
	local body = "R Carol\nT ping from client\n"
	Stub.fs["eqvoice-in.txt"] = string.format("OVI1 14 1 %04X Me\n", OV.Frame.CRC16(body)) .. body
	Stub.Advance(2)
	local lines = OV.LogTail(8)
	check(string.find(table.concat(lines, "\n"), "ping from client", 1, true), "the client's test message reaches the log")
	-- logout
	Stub.FireEvent("PLAYER_LOGOUT")
	st = OV.Frame.Decode(OV.lastBytes)
	eq(math.floor(st.flags / 16) % 2, 1, "logout flag in the last frame"); eq(table.getn(st.results), 0, "and no results")
end

sec("pipeline: optical copy truncates, file keeps everything")
do
	local units, names = {}, {}
	for i = 1, 40 do
		names[i] = "Crowd" .. string.format("%02d", i)
		units["0x" .. i] = { name = names[i], player = true, dist = 5 + i }
	end
	local OV = Stub.Login({ classic = true, fileApi = "WriteCustomFile", units = units })
	OV.db.optical, OV.db.opticalChosen = true, true; OV.Bridge.optical = true          -- this test is about the optical copy: ask for it
	Stub.fs["eqvoice-in.txt"] = inboxText(OV, 1, "Me", names, "report=100 checks=200")
	Stub.Advance(3)
	local full = OV.Frame.Decode(OV.lastBytes)
	eq(table.getn(full.results), 40, "the file frame lists all 40")
	local bf
	for i = 1, table.getn(Stub.frames) do
		if table.getn(Stub.frames[i].textures) == 321 then bf = Stub.frames[i] end
	end
	local cells = {}
	for i = 1, 320 do
		local t = bf.textures[i + 1]
		local v = 0
		for k = 0, 63 do
			local r, g, b = OV.Frame.CellRGB(k)
			if t.r == r and t.g == g and t.b == b then v = k break end
		end
		cells[i] = v
	end
	local optical = OV.Frame.Decode(OV.Frame.FromCells(cells))
	check(optical ~= nil and table.getn(optical.results) < 40 and table.getn(optical.results) > 5, "the painted copy carries the nearest few")
	eq(optical.results[1].name, "Crowd01", "nearest first"); eq(math.floor(optical.flags / 128) % 2, 1, "and says it was truncated")
end

sec("diagnostics")
do
	local OV = Stub.Login({ inGuild = true, db = { transport = "AUTO" } })
	-- SAY with loopback
	Stub.loopback = { SAY = true }
	OV.Slash("diag say")
	Stub.Advance(8)
	check(OV.db.diag.say ~= nil and OV.db.diag.say.ok, "SAY loopback recorded as ok")
	check(string.find(OV.db.diag.say.result, "LOOPBACK 3/3", 1, true), "all three probes echoed: " .. tostring(OV.db.diag.say.result))
	-- AUTO now picks SAY
	local ch = OV.Comm.CurrentChannels()
	eq(ch[1], "SAY", "AUTO switches to SAY after a proven loopback")
	-- YELL rejected by the client
	Stub.rejectTypes = { YELL = true }
	OV.Slash("diag yell")
	Stub.Advance(8)
	check(not OV.db.diag.yell.ok and string.find(OV.db.diag.yell.result, "ERROR", 1, true), "client refusing YELL recorded as ERROR")
	-- sent but not echoed
	Stub.rejectTypes, Stub.loopback = {}, {}
	OV.Slash("diag party")
	Stub.Advance(8)
	check(not OV.db.diag.party.ok and string.find(OV.db.diag.party.result, "NO ECHO", 1, true), "no echo recorded")
	-- diag traffic uses its own prefix
	local diagSent, hbSent = 0, 0
	for i = 1, table.getn(Stub.sent) do
		if Stub.sent[i].prefix == "OVD" then diagSent = diagSent + 1 end
	end
	check(diagSent >= 6, "diagnostics use the OVD prefix")
	-- api listing does not raise
	OV.Slash("diag api")
	check(OV.db.diag.api ~= nil and string.find(OV.db.diag.api.result, "SendAddonMessage", 1, true), "api probe lists SendAddonMessage")
	-- demo bridge frame
	OV.Slash("diag bridge 20")
	Stub.Advance(2)
	local st = OV.Frame.Decode(OV.lastBytes)
	eq(st.character, "Testchar", "demo frame character"); eq(st.results[2].name, "Bravo", "demo frame results")
	eq(st.results[2].dist, 31.5, "demo distance")
	Stub.Advance(25)
	st = OV.Frame.Decode(OV.lastBytes)
	eq(st.character, "Me", "demo frame ends on its own")
	-- rate test refuses to run without 'go'
	local n0 = table.getn(Stub.sent)
	OV.Slash("diag rate say 5 5")
	Stub.Advance(2)
	local diagAfter = 0
	for i = n0 + 1, table.getn(Stub.sent) do if Stub.sent[i].prefix == "OVD" then diagAfter = diagAfter + 1 end end
	eq(diagAfter, 0, "rate test needs an explicit 'go'")
	Stub.loopback = { SAY = true }
	OV.Slash("diag rate say 2 5 go")
	Stub.Advance(12)
	check(OV.db.diag.rate_say ~= nil and string.find(OV.db.diag.rate_say.result, "sent 10", 1, true), "rate test sent 10 messages: " .. tostring(OV.db.diag.rate_say and OV.db.diag.rate_say.result))
	-- file test
	Stub.fileApi = nil
	OV.Slash("diag file")
	check(OV.db.diag.file ~= nil and not OV.db.diag.file.ok, "file test reports no api")
	-- beacon + listener
	OV.Slash("diag beacon say")
	Stub.loopback = {}
	Stub.Advance(3)
	local beacons = 0
	for i = 1, table.getn(Stub.sent) do
		if Stub.sent[i].prefix == "OVD" and string.find(Stub.sent[i].text, "^R:") then beacons = beacons + 1 end
	end
	check(beacons >= 2, "beacon sends numbered messages")
	OV.Slash("diag beacon off")
	-- report does not raise
	OV.Slash("diag report")
	OV.Slash("status"); OV.Slash("nearby"); OV.Slash("log 5"); OV.Slash("help"); OV.Slash("radius 500"); OV.Slash("debug on"); OV.Slash("debug off"); OV.Slash("reset")
	eq(OV.db.radius, 500, "radius saved")
end

sec("diagnostics: every chat type")
do
	local OV = Stub.Login({ inGuild = true, db = { transport = "AUTO" } })
	-- what the real client did on 2026-10-15: SAY and YELL refused, GUILD fine
	Stub.rejectTypes = { SAY = true, YELL = true }
	Stub.loopback = { GUILD = true, CHANNEL = true }
	OV.Slash("diag all")
	Stub.Advance(14)
	local d = OV.db.diag
	check(d.say and not d.say.ok and string.find(d.say.result, "ERROR", 1, true) and string.find(d.say.result, "Unknown addon chat type", 1, true), "SAY refused by the client is reported as ERROR with the client's words")
	check(d.yell and string.find(d.yell.result, "ERROR", 1, true), "YELL too")
	check(d.officer and string.find(d.officer.result, "NO ECHO", 1, true) and string.find(d.officer.result, "accepted", 1, true), "an accepted type with no echo says so")
	check(d.battleground and string.find(d.battleground.result, "accepted", 1, true), "BATTLEGROUND probe ran")
	check(d.whisper and string.find(d.whisper.result, "accepted", 1, true), "WHISPER probe ran")
	check(d.channel and d.channel.ok and string.find(d.channel.result, "LOOPBACK 3/3", 1, true), "CHANNEL joined a throwaway channel and looped back: " .. tostring(d.channel and d.channel.result))
	local sawChannelTarget, sawWhisperTarget = false, false
	for i = 1, table.getn(Stub.sent) do
		local m = Stub.sent[i]
		if m.chan == "CHANNEL" and m.target == 5 then sawChannelTarget = true end
		if m.chan == "WHISPER" and m.target == "Me" then sawWhisperTarget = true end
	end
	check(sawChannelTarget, "channel probes go to the joined channel's number")
	check(sawWhisperTarget, "whisper probes are addressed to yourself")
	eq(Stub.channels.ovdiag, nil, "the throwaway channel is left afterwards")
	eq(OV.Comm.CurrentChannels()[1], "GUILD", "a refused SAY leaves AUTO on group channels")

	-- the channel cannot be joined
	local OV2 = Stub.Login({ inGuild = true, noJoin = true })
	Stub.loopback = { CHANNEL = true }
	OV2.Slash("diag channel")
	Stub.Advance(8)
	check(OV2.db.diag.channel and string.find(OV2.db.diag.channel.result, "NO CHANNEL", 1, true), "a channel that never joins is reported, not hung")

	-- no channel API at all
	local OV3 = Stub.Login({})
	_G.JoinChannelByName = nil
	OV3.Slash("diag channel")
	check(OV3.db.diag.channel and string.find(OV3.db.diag.channel.result, "NO CHANNEL API", 1, true), "missing channel API is reported")

	-- beacons and rate tests refuse chat types that need a target
	local OV4 = Stub.Login({ inGuild = true })
	OV4.Slash("diag beacon whisper")
	OV4.Slash("diag rate channel 1 5 go")
	Stub.Advance(3)
	local diagMsgs = 0
	for i = 1, table.getn(Stub.sent) do
		if Stub.sent[i].prefix == "OVD" then diagMsgs = diagMsgs + 1 end
	end
	eq(diagMsgs, 0, "beacon/rate on WHISPER or CHANNEL are refused")
end

sec("scan: who is near me")
do
	local units = {
		["0xA1"] = { name = "Alice",  player = true,  dist = 13.2 },
		["0xB2"] = { name = "Bob",    player = true,  dist = 28.6 },
		["0xC3"] = { name = "Mallory", player = true, dist = 5.0, enemy = true },
		["0xD4"] = { name = "Wolf",   player = false, dist = 9.0 },
		["0xE5"] = { name = "Me",     player = true,  dist = 0 },
		["0xF6"] = { name = "Ghost",  player = true },          -- no position known
	}
	local plates = { "0xA1", "0xB2", "0xC3", "0xD4", "0xE5", "0xF6" }

	-- ClassicAPI route
	local OV = Stub.Login({ classic = true, units = units, plates = plates })
	local caps = OV.Scan.Capabilities()
	eq(caps.method, "classicapi", "ClassicAPI nameplates + UnitDistanceSquared select the classicapi method")
	local list, n, method = OV.Scan.Players()
	eq(method, "classicapi", "method reported"); eq(n, 4, "players only: no NPC, not myself (got " .. n .. ")")
	eq(list[1].name, "Mallory", "nearest first"); eq(list[2].name, "Alice", "then by distance")
	eq(list[3].name, "Bob", "then the farthest with a distance"); eq(list[4].name, "Ghost", "unknown distance sorts last")
	eq(string.format("%.1f", list[2].dist), "13.2", "distance in yards (sqrt of the squared value)")
	eq(list[1].enemy, true, "enemy flag"); eq(list[2].enemy, false, "friendly flag"); eq(list[4].dist, nil, "no distance claimed when none is known")
	local near, nn = OV.Scan.Players(20)
	eq(nn, 3, "maxDist 20 keeps Mallory, Alice and the unknown-distance entry")
	local _, nz = OV.Scan.Players(1)
	eq(nz, 1, "maxDist 1 keeps only the unknown-distance entry")

	-- the APIs raising must not break the scan
	Stub.distError = "boom"
	local l2, n2 = OV.Scan.Players()
	eq(n2, 4, "a failing UnitDistanceSquared still lists the players"); eq(l2[1].dist, nil, "but with no distances")
	Stub.distError = nil
	Stub.nameplateError = "boom"
	local _, n3, m3 = OV.Scan.Players()
	eq(n3, 0, "a failing nameplate call returns an empty list, not an error")
	Stub.nameplateError = nil

	-- by-name lookup (no nameplate needed)
	local r = OV.Scan.Lookup("Alice")
	check(r ~= nil and r.player and string.format("%.1f", r.dist) == "13.2", "lookup by name finds Alice at 13.2 yd")
	local nr, why = OV.Scan.Lookup("Nobody")
	eq(nr, nil, "unknown name"); check(string.find(why, "nobody", 1, true), "reason given")

	-- UnitPosition shape
	local cnt, a, b, c, d = OV.Scan.PositionShape()
	eq(cnt, 4, "ClassicAPI UnitPosition returns four values"); eq(d, 1, "instance id last")

	-- SuperWoW route (no ClassicAPI)
	local OV2 = Stub.Login({ superwow = true, units = units,
		plateFrames = { { name = "Alice", guid = "0xA1" }, { name = "Bob", guid = "0xB2" }, { name = "Wolf", guid = "0xD4" } } })
	eq(OV2.Scan.Capabilities().method, "superwow", "GetName(1) plates select the superwow method")
	local sl, sn, sm = OV2.Scan.Players()
	eq(sm, "superwow", "method"); eq(sn, 2, "players only"); eq(sl[1].name, "Alice", "distance from UnitXP"); eq(string.format("%.1f", sl[1].dist), "13.2", "distance value")

	-- bare client: nameplate frames carry names only
	local OV3 = Stub.Login({ plateFrames = { { name = "Alice" }, { name = "Wolf" }, { name = "Me" } } })
	eq(OV3.Scan.Capabilities().method, "frames", "nothing but WorldFrame: the frames method")
	local fl, fn, fm = OV3.Scan.Players()
	eq(fm, "frames", "method"); eq(fn, 2, "names found, myself excluded, non-plates ignored (NPCs cannot be told apart)")
	eq(fl[1].dist, nil, "no distance on the bare client"); eq(fl[1].player, nil, "and no player/NPC distinction")

	-- nothing at all
	local OV4 = Stub.Login({})
	_G.WorldFrame = nil
	local _, zn, zm = OV4.Scan.Players()
	eq(zm, "none", "no source"); eq(zn, 0, "no players")
	check(OV4.Scan.Lookup("Alice") == nil, "lookup without UnitTokenFromName reports a reason")

	-- the diagnostic commands
	local OV5 = Stub.Login({ classic = true, units = units, plates = plates, inGuild = true })
	OV5.Slash("diag nearby")
	local d5 = OV5.db.diag.nearby
	check(d5 and d5.ok and string.find(d5.result, "method=classicapi", 1, true) and string.find(d5.result, "players=4", 1, true), "diag nearby records method and count: " .. tostring(d5 and d5.result))
	check(string.find(d5.result, "Alice 13.2 yd", 1, true), "and the distances")
	local joined = table.concat(Stub.chat, "\n")
	check(string.find(joined, "UnitPosition(player) returns 4 values", 1, true), "reports the UnitPosition shape")
	check(string.find(joined, "method: classicapi", 1, true), "reports which sources exist")
	OV5.Slash("diag name Alice")
	check(OV5.db.diag.name and OV5.db.diag.name.ok and string.find(OV5.db.diag.name.result, "13.2 yd", 1, true), "diag name measures a player by name")
	OV5.Slash("diag name Nobody")
	check(OV5.db.diag.name and not OV5.db.diag.name.ok, "an unknown name is a recorded miss")
	OV5.Slash("diag name")
	Stub.chat = {}
	OV5.Slash("diag nearby watch")
	Stub.Advance(5)
	local watched = table.concat(Stub.chat, "\n")
	check(string.find(watched, "nearby [classicapi]", 1, true) and string.find(watched, "Alice 13.2", 1, true), "watch mode prints the list every couple of seconds")
	OV5.Slash("diag nearby watch")
	Stub.chat = {}
	Stub.Advance(5)
	eq(table.getn(Stub.chat), 0, "and stops when toggled off")
end

sec("scan: finding out why nobody is found")
do
	-- GUID tokens that UnitIsPlayer does not recognise: plates exist, players are not found
	local units = { ["0xA1"] = { name = "Alice", player = false, dist = 10 }, ["0xA2"] = { name = "Bob", player = false, dist = 12 } }
	local OV = Stub.Login({ classic = true, units = units, plates = { "0xA1", "0xA2" }, inGuild = true })
	local list, n = OV.Scan.Players()
	eq(n, 0, "no players when UnitIsPlayer says no")
	eq(OV.Scan.stats.guids, 2, "stage count: two units seen"); eq(OV.Scan.stats.notPlayer, 2, "both rejected as not players")
	local lines, ln = OV.Scan.Probe()
	local joined = table.concat(lines, "\n")
	check(string.find(joined, "GetNamePlateGUIDs: 2 units", 1, true), "probe reports how many GUIDs the nameplate call returned")
	check(string.find(joined, "0xA1 name=Alice isPlayer=nil", 1, true), "probe shows the raw answers for a sample unit: " .. joined)
	check(string.find(joined, "WorldFrame children", 1, true), "probe also reports the WorldFrame frames")
	OV.Slash("diag nearby")
	local chat = table.concat(Stub.chat, "\n")
	check(string.find(chat, "stages: 2 units seen -> 0 players", 1, true), "diag nearby prints the stage counts")
	check(string.find(chat, "raw look at every source", 1, true), "and the raw probe when it finds nobody")

	-- the nameplate call itself fails
	Stub.nameplateError = "kaboom"
	local l2, n2 = OV.Scan.Probe()
	check(string.find(table.concat(l2, "\n"), "raised: ", 1, true), "a raising GetNamePlateGUIDs is reported, not hidden")
	Stub.nameplateError = nil

	-- probe on a client with none of the APIs
	local OV3 = Stub.Login({})
	_G.WorldFrame = nil
	local l3 = OV3.Scan.Probe()
	check(string.find(table.concat(l3, "\n"), "not available", 1, true), "probe on a bare client says what is missing")
end

sec("diagnostics: inbox and probe children")
do
	local OV = Stub.Login({ fileApi = "WriteCustomFile" })
	OV.Slash("diag inbox")
	check(OV.db.diag.inbox and not OV.db.diag.inbox.ok and string.find(OV.db.diag.inbox.result, "no file read", 1, true), "an empty inbox is a recorded miss with instructions")
	Stub.fs["eqvoice-in.txt"] = "OVI1 hello from the client"
	OV.Slash("diag inbox")
	check(OV.db.diag.inbox.ok and string.find(OV.db.diag.inbox.result, "CustomData", 1, true) and string.find(OV.db.diag.inbox.result, "OVI1 hello", 1, true), "a file written outside the game is read through the mod API")
	check(string.find(OV.db.diag.inbox.result, "not a roster file", 1, true), "and the plain test text is called what it is, without alarm")
	Stub.fs["eqvoice-in.txt"] = inboxText(OV, 3, "Me", { "Bob" })
	OV.Slash("diag inbox")
	check(string.find(OV.db.diag.inbox.result, "VALID roster file: seq 3, for Me, 1 name(s)", 1, true), "a real roster file is recognised: " .. OV.db.diag.inbox.result)
	Stub.fs["eqvoice-in.txt"] = inboxText(OV, 4, "Someone", { "Bob" })
	OV.Slash("diag inbox")
	check(string.find(OV.db.diag.inbox.result, "would be ignored", 1, true), "and one for another character is flagged")
	local OV2 = Stub.Login({})
	OV2.Slash("diag inbox")
	check(not OV2.db.diag.inbox.ok, "no file API: a miss, not an error")

	-- the probe lists what the WorldFrame GUID children are
	local units = { ["0xA1"] = { name = "Alice", player = true, dist = 10 } }
	local OV3 = Stub.Login({ superwow = true, units = units, plateFrames = { { name = "Alice", guid = "0xA1" } } })
	local lines = OV3.Scan.Probe()
	local joined = table.concat(lines, "\n")
	check(string.find(joined, "child 1 0xA1 name=Alice isPlayer=1", 1, true), "probe describes each GUID child: " .. joined)
end

sec("group harness: logout")
do
	local OV = Stub.Login({ inGuild = true, db = { transport = "AUTO" } })
	Stub.Advance(5)
	Stub.sent = {}
	Stub.FireEvent("PLAYER_LOGOUT")
	check(sentOn("GUILD", "OV1") >= 1, "goodbye on logout")
	check(OV.db.log ~= nil and table.getn(OV.db.log) > 0, "log saved for SavedVariables")
	check(OV.db.lastStats ~= nil and OV.db.lastStats.frames > 0, "stats saved")
	local OV2 = Stub.Login({ inGuild = true })
	Stub.Advance(10)
	eq(table.getn(Stub.sent), 0, "the group harness is silent by default")
end

sec("lookup cost benchmark")
do
	local OV = Stub.Login({ classic = true })
	OV.Slash("diag lookupcost 50")
	Stub.Advance(7)
	check(OV.db.diag.lookupcost ~= nil and OV.db.diag.lookupcost.ok, "benchmark ran and recorded: " .. tostring(OV.db.diag.lookupcost and OV.db.diag.lookupcost.result))
	check(string.find(OV.db.diag.lookupcost.result, "50 lookups/frame", 1, true), "result names the load")
	local OV2 = Stub.Login({})
	OV2.Slash("diag lookupcost")
	Stub.Advance(1)
	eq(OV2.db.diag.lookupcost, nil, "no benchmark without the API")
end

sec("silent by default: a player sees nothing from EqVoice in the chat")
do
	-- the whole routine life of the addon: login, a roster, a nearby player, a frame every second, a player leaving, a /reload
	local units = { ["0xB"] = { name = "Bob", player = true, dist = 18.4 } }
	local OV = Stub.Login({ classic = true, fileApi = "WriteCustomFile", units = units })
	Stub.Advance(2)
	Stub.fs["eqvoice-in.txt"] = inboxText(OV, 5, "Me", { "Bob", "Carol" })
	Stub.Advance(6)
	units["0xB"].dist = 31.0
	Stub.Advance(4)
	units["0xB"] = nil
	Stub.Advance(6)
	local OV2 = Stub.Login({ classic = true, fileApi = "WriteCustomFile", units = units, db = OV.db })
	Stub.Advance(6)
	eq(table.getn(Stub.chat), 0, "nothing was printed: " .. table.concat(Stub.chat, " | "))
	check(OV2.db.chatTrace == nil, "the chat narration is off unless a developer turns it on")
	-- the narration itself is not lost: the bridge file still carries it for the desktop client's log
	check(string.find(Stub.fs["eqvoice.txt"] or "", "OVT ", 1, true) ~= nil, "the bridge file still carries the narration (OVT lines)")
	-- an old saved setting that said trace = true (the first test builds printed everything) no longer prints
	Stub.chat = {}
	local OV3 = Stub.Login({ classic = true, fileApi = "WriteCustomFile", units = units, db = { trace = true } })
	Stub.Advance(4)
	eq(table.getn(Stub.chat), 0, "a saved 'trace = true' from an older build does not turn the chat back on")
	-- a developer can ask for it, and ask for it to stop
	SlashCmdList["EQVOICE"]("trace on")
	OV3.Trace("a probe line")
	local said = table.concat(Stub.chat, " | ")
	check(string.find(said, "trace on", 1, true) and string.find(said, "a probe line", 1, true), "/ev trace on narrates in the chat: " .. said)
	SlashCmdList["EQVOICE"]("trace off")
	Stub.chat = {}
	Stub.Advance(4)
	eq(table.getn(Stub.chat), 0, "/ev trace off stops it")
end

sec("trace: every boundary is narrated, in chat and in the bridge file (when a developer asks for it)")
do
	local units = { ["0xB"] = { name = "Bob", player = true, dist = 18.4 } }
	local OV = Stub.Login({ classic = true, fileApi = "WriteCustomFile", units = units, db = { chatTrace = true } })
	local function chatHas(s) return string.find(table.concat(Stub.chat, "\n"), s, 1, true) ~= nil end
	check(chatHas("identity: Me on Testrealm"), "identity is narrated at login")
	check(chatHas("eqvoice-Me.txt / eqvoice-in-Me.txt"), "and names the per-character files")
	Stub.chat = {}
	Stub.Advance(2)
	check(chatHas("inbound: no roster file yet"), "no roster file: said once, with where it looked")
	local n = 0
	for _ in string.gmatch(table.concat(Stub.chat, "\n"), "no roster file yet") do n = n + 1 end
	eq(n, 1, "only once, not every poll")
	Stub.chat = {}
	Stub.fs["eqvoice-in.txt"] = inboxText(OV, 5, "Me", { "Bob", "Carol" })
	Stub.Advance(2)
	check(chatHas("inbound: read CustomData\\eqvoice-in.txt seq 5 for Me: roster is now 2 name(s): Bob,Carol"), "inbound roster read is narrated: " .. table.concat(Stub.chat, " | "))
	check(chatHas("lookup Bob: FOUND by exact name, 18.4 yd"), "a successful exact-name lookup is narrated with the distance")
	check(chatHas("lookup Carol: not available (no such player loaded near you)"), "a failed lookup says why")
	check(chatHas("outbound: frame #"), "the outbound frame is narrated")
	check(chatHas("Bob 18.4 yd"), "with the results in it")
	-- changes only: standing still adds nothing, walking 5 yd adds one line
	Stub.chat = {}
	Stub.Advance(2)
	local quiet = table.getn(Stub.chat)
	units["0xB"].dist = 25
	Stub.Advance(3)
	check(chatHas("lookup Bob: 25.0 yd"), "a new distance is narrated once it has moved")
	check(table.getn(Stub.chat) > quiet, "and the outbound change too")
	units["0xB"] = nil
	Stub.Advance(3)
	check(chatHas("lookup Bob: not available"), "losing a player is narrated")
	-- the same lines go to the bridge file, numbered, after the frame line
	local content = Stub.fs["eqvoice.txt"]
	check(string.sub(content, 1, 5) == "OVB2 ", "the file still starts with the frame")
	local lines = {}
	for line in string.gmatch(content, "[^\n]+") do lines[table.getn(lines) + 1] = line end
	check(table.getn(lines) >= 3, "trace lines follow the frame (" .. table.getn(lines) .. " lines)")
	local last = 0
	for i = 2, table.getn(lines) do
		local seq, wall, text = string.match(lines[i], "^OVT (%d+) (%d+) (.+)$")
		check(seq ~= nil, "trace line format: " .. lines[i])
		check(tonumber(seq) > last, "sequence numbers rise"); last = tonumber(seq)
		check(tonumber(wall) >= 1700000000, "wall clock stamp")
	end
	check(table.getn(lines) - 1 <= 16, "at most 16 trace lines travel with each frame")
	-- more than 16 lines: only the newest travel
	for i = 1, 40 do OV.Trace("filler " .. i) end
	local text = OV.TraceText()
	local count = 0
	for _ in string.gmatch(text, "[^\n]+") do count = count + 1 end
	eq(count, 16, "the ring keeps the newest 16"); check(string.find(text, "filler 40", 1, true) and not string.find(text, "filler 20", 1, true), "newest, not oldest")
	-- trace off: silent in chat, still in the file
	OV.Slash("trace off")
	Stub.chat = {}
	OV.Trace("quiet line")
	eq(table.getn(Stub.chat), 0, "trace off prints nothing")
	check(string.find(OV.TraceText(), "quiet line", 1, true), "but the bridge file still carries it")
	OV.Slash("trace on")
	-- control characters cannot break the line format
	OV.Trace("a\nb\r\nc")
	check(not string.find(OV.TraceText(), "OVT %d+ %d+ a\nb"), "newlines in a message are flattened")
end

sec("per-character files: two game instances in one folder")
do
	local units = { ["0xB"] = { name = "Bob", player = true, dist = 12 } }
	local OV = Stub.Login({ classic = true, fileApi = "WriteCustomFile", units = units })
	check(OV.FileSafeName() == "Me", "plain letters qualify")
	Stub.Advance(2)
	check(Stub.fs["eqvoice.txt"] ~= nil and Stub.fs["eqvoice-Me.txt"] ~= nil, "both outbound files are written")
	eq(string.sub(Stub.fs["eqvoice.txt"], 1, 5), "OVB2 ", "the plain file has a frame")
	local first = string.match(Stub.fs["eqvoice-Me.txt"], "^[^\n]+")
	check(string.sub(first, 1, 5) == "OVB2 ", "and so has the per-character file")
	-- inbound: the per-character file wins over the shared one
	Stub.fs["eqvoice-in.txt"] = inboxText(OV, 1, "Me", { "Carol" })
	Stub.fs["eqvoice-in-Me.txt"] = inboxText(OV, 1, "Me", { "Bob" })
	Stub.Advance(2)
	eq(OV.Lookup.order[1], "Bob", "eqvoice-in-Me.txt is read first")
	check(string.find(OV.Inbox.source, "eqvoice-in-Me.txt", 1, true), "and reported as the source: " .. tostring(OV.Inbox.source))
	Stub.fs["eqvoice-in-Me.txt"] = nil
	Stub.fs["eqvoice-in.txt"] = inboxText(OV, 2, "Me", { "Carol" })
	Stub.Advance(2)
	eq(OV.Lookup.order[1], "Carol", "without it, eqvoice-in.txt is used")
	check(string.find(OV.Inbox.source, "eqvoice-in.txt", 1, true) and not string.find(OV.Inbox.source, "-Me", 1, true), "and reported as the source")
	-- a name that is not plain ASCII letters gets no per-character file
	local OV2 = Stub.Login({ classic = true, fileApi = "WriteCustomFile", playerName = "Zoe2" })
	eq(OV2.FileSafeName(), nil, "digits disqualify")
	Stub.Advance(2)
	check(Stub.fs["eqvoice-Zoe2.txt"] == nil and Stub.fs["eqvoice.txt"] ~= nil, "only the shared file is written")
	-- per-character writes are counted
	local OV3 = Stub.Login({ classic = true, fileApi = "WriteCustomFile" })
	Stub.Advance(2)
	check(OV3.Bridge.stats.charWrites ~= nil and OV3.Bridge.stats.charWrites >= 1, "per-character writes are counted")
end

-- ============================================================ /say calibration
sec("say calibration")
do
	local units = { u1 = { name = "Bobby", player = true, dist = 30 } }
	local OV = Stub.Login({ classic = true, units = units })
	Stub.Advance(5)
	eq(table.getn(Stub.said), 0, "silent unless asked")
	OV.Slash("say start")
	check(string.find(Stub.chat[table.getn(Stub.chat)] or "", "choose the other character", 1, true), "needs a partner first")
	eq(OV.Calibrate.on, false, "and stays off")
	OV.Slash("say with Bobby")
	OV.Slash("say start")
	eq(OV.Calibrate.on, true, "on with a partner")
	Stub.Advance(10)
	local n = table.getn(Stub.said)
	check(n >= 4 and n <= 6, "about one /say per interval, got " .. n)
	eq(Stub.said[1].chan, "SAY", "spoken in /say")
	eq(Stub.said[1].text, "ovcal 1 2000", "numbered, with its interval")
	eq(Stub.said[2].text, "ovcal 2 2000", "in order")
	local tr = OV.TraceText()
	check(string.find(tr, "saycal sent 1 to Bobby at 30.0 yd", 1, true), "the send is narrated with the exact distance")

	-- the partner's lines arrive
	units.u1.dist = 22
	Stub.Advance(1)
	Stub.FireEvent("CHAT_MSG_SAY", "ovcal 1 2000", "Bobby", "Common")
	check(string.find(OV.TraceText(), "saycal heard 1 from Bobby at 22.0 yd", 1, true), "a heard line, with the distance at that moment")
	Stub.FireEvent("CHAT_MSG_SAY", "ovcal 2 2000", "Stranger", "Common")
	eq(OV.Calibrate.stats.heard, 1, "only the chosen partner counts")
	-- a gap in the numbers is a message that did not arrive; its distance comes from the samples around its due time
	units.u1.dist = 30
	Stub.Advance(2)
	units.u1.dist = 38
	Stub.Advance(2)
	units.u1.dist = 44
	Stub.Advance(2)
	Stub.FireEvent("CHAT_MSG_SAY", "ovcal 4 2000", "Bobby", "Common")
	eq(OV.Calibrate.stats.missed, 2, "numbers 2 and 3 never arrived")
	local m2, m3 = OV.Calibrate.missLog[1], OV.Calibrate.missLog[2]
	check(m2 and m3 and m2.seq == 2 and m3.seq == 3 and m2.dist and m3.dist and m2.dist < m3.dist, "and they were further and further away")
	check(m2.dist >= 29.9 and m3.dist <= 44.1, "inside the walked range")
	check(string.find(OV.TraceText(), "saycal skipped 3 from Bobby at ", 1, true), "skips are narrated")
	-- the other faction's /say is garbled: it was delivered, but its number is lost
	Stub.FireEvent("CHAT_MSG_SAY", "Lok tar ogar", "Bobby", "Orcish")
	eq(OV.Calibrate.stats.garbled, 1, "heard without a number")
	check(string.find(OV.TraceText(), "saycal heard ? from Bobby", 1, true), "and said so")
	-- buckets and the saved copy
	local b, starts = OV.Calibrate.Buckets()
	check(table.getn(starts) >= 2 and b[starts[1]].heard + b[starts[1]].missed > 0, "bucketed by 5 yards")
	OV.Slash("say stop")
	eq(OV.Calibrate.on, false, "stop")
	check(OV.db.saycal and string.find(OV.db.saycal.heard, "1:22.0", 1, true) and string.find(OV.db.saycal.skipped, "2:", 1, true),
		"kept in the saved variables")
	local said = table.getn(Stub.said)
	Stub.Advance(6)
	eq(table.getn(Stub.said), said, "and silent again afterwards")
	-- it must stay out of the public chat trace: no chat spam while it runs (the lines travel in the bridge file)
	local loud = 0
	for i = 1, table.getn(Stub.chat) do
		if string.find(Stub.chat[i], "saycal", 1, true) then loud = loud + 1 end
	end
	eq(loud, 0, "saycal lines are not printed to chat")
end
do
	local OV = Stub.Login({ classic = true, units = { u1 = { name = "Bobby", player = true, dist = 10 } } })
	Stub.sayError = "Interface action failed"
	OV.Slash("say with Bobby")
	OV.Slash("say start 6")
	Stub.Advance(3)
	check(OV.Calibrate.stats.failed >= 1 and string.find(OV.TraceText(), "saycal send FAILED", 1, true), "a refused /say is reported, not hidden")
	Stub.sayError = nil
	Stub.Advance(5)
	eq(OV.Calibrate.on, false, "start 6 stops itself after six seconds")
	local OV2 = Stub.Login({ units = {} })          -- no ClassicAPI
	OV2.Slash("say with Bobby")
	OV2.Slash("say start")
	eq(OV2.Calibrate.on, false, "without the name lookup it refuses (nothing to measure with)")
	OV2.Slash("say interval 2.5")
	eq(OV2.Calibrate.interval, 2.5, "interval is configurable")
	OV2.Slash("say interval 99")
	eq(OV2.Calibrate.interval, 2.5, "within limits")
	OV2.Slash("say interval 1")
	eq(OV2.Calibrate.interval, 2.5, "and never faster than 2 s: a 1 s pass lost eight lines in a row to what looks like the server's chat flood limit")
end

-- ====================================================== scheduling at scale
sec("lookup scheduling at scale")
do
	local function roster(n)
		local t = {}
		for i = 1, n do t[i] = string.format("Pl%04d", i) end
		return t
	end
	-- the desktop writes 25 names per R line, and rewrites the file every 4 s (the game treats 15 s of silence as gone)
	local function bigInbox(OV, seq, names, cfg)
		local body = "C " .. cfg .. "\n"
		local i = 1
		while i <= table.getn(names) do
			local chunk = {}
			for j = i, math.min(i + 24, table.getn(names)) do chunk[table.getn(chunk) + 1] = names[j] end
			body = body .. "R " .. table.concat(chunk, ",") .. "\n"
			i = i + 25
		end
		return string.format("OVI1 %d %d %04X Me\n", seq, 1760000000 + seq, OV.Frame.CRC16(body)) .. body
	end
	local function newDesktop(OV, names, checks)
		local d = { seq = 0, names = names, cfg = "interval=0.5 checks=" .. checks .. " report=60", next = 0 }
		function d.write()
			d.seq = d.seq + 1
			Stub.fs["eqvoice-in.txt"] = bigInbox(OV, d.seq, d.names, d.cfg)
		end
		function d.advance(seconds)
			local steps = math.floor(seconds / 0.25 + 0.5)
			for s = 1, steps do
				if Stub.now >= d.next then d.write(); d.next = Stub.now + 4 end
				Stub.Advance(0.25)
			end
		end
		return d
	end
	local function found(OV, name)
		for i = 1, OV.Lookup.nresults do
			if OV.Lookup.results[i].name == name then return true end
		end
		return false
	end
	-- Seconds from a stranger becoming loaded until the scan reports them, with a settled roster of n unresolved names.
	local function discovery(n, checks, targetIndex)
		local units = {}
		local OV = Stub.Login({ classic = true, fileApi = "WriteCustomFile", units = units })
		local names = roster(n)
		local d = newDesktop(OV, names, checks)
		d.advance(40)                                      -- everyone has missed many times: the slowest tier
		local target = names[targetIndex]
		units["0xT"] = { name = target, player = true, dist = 25 }
		local t0 = Stub.now
		for step = 1, 80 do
			d.advance(0.25)
			if found(OV, target) then return Stub.now - t0 end
		end
		return nil
	end
	for _, spec in ipairs({ { 50, 80 }, { 100, 80 }, { 250, 80 }, { 500, 84 }, { 1000, 167 } }) do
		local n, checks = spec[1], spec[2]
		local w = 0
		for _, idx in ipairs({ 1, math.floor(n / 2), n }) do
			local dt = discovery(n, checks, idx)
			check(dt ~= nil, "n=" .. n .. " target " .. idx .. " is found")
			if dt and dt > w then w = dt end
		end
		local sweep = n / checks * 0.5
		local bound = (sweep > 2 and sweep or 2) + 1.0
		check(w <= bound, string.format("n=%d: a stranger is found within %.2f s (took %.2f s)", n, bound, w))
		print(string.format("   roster %4d, %3d checks per 0.5 s scan: slowest of three strangers found in %.2f s (full sweep %.2f s)", n, checks, w, sweep))
	end

	-- a name added to the roster is looked at in the very next scan, whatever the size of the sweep
	local units = {}
	local OV = Stub.Login({ classic = true, fileApi = "WriteCustomFile", units = units })
	local names = roster(1000)
	local d = newDesktop(OV, names, 167)
	d.advance(40)
	local more = roster(1000)
	more[1001] = "Newcomer"
	units["0xN"] = { name = "Newcomer", player = true, dist = 12 }
	d.names = more
	d.next = Stub.now                                       -- the desktop writes the new roster now
	local t0, at = Stub.now, nil
	for step = 1, 16 do
		d.advance(0.25)
		if found(OV, "Newcomer") and not at then at = Stub.now - t0 end
	end
	check(at ~= nil and at <= 2.0, "a newly online player is found within 2 s (the file is read once a second): " .. tostring(at))
	print(string.format("   a newcomer added to a settled roster of 1000 was found in %.2f s", at or -1))

	-- a neighbour already loaded stays fresh on every scan, however big the roster
	units["0xN"].dist = 30
	d.advance(0.5)
	local d1
	for i = 1, OV.Lookup.nresults do if OV.Lookup.results[i].name == "Newcomer" then d1 = OV.Lookup.results[i].dist end end
	eq(d1, 30, "its distance is refreshed within one scan")
	-- cost: lookups in one scan never exceed the budget by more than the number of neighbours
	check(OV.Lookup.stats.lastChecked <= 167 + 5, "a scan stays inside its budget: " .. OV.Lookup.stats.lastChecked)
end

sec("say calibration: watching it live")
do
	local units = { u1 = { name = "Bobby", player = true, dist = 31.5 } }
	local OV = Stub.Login({ classic = true, units = units, db = { chatTrace = true } })
	OV.Slash("say with Bobby")
	OV.Slash("say show")
	eq(OV.Calibrate.show, true, "show is on")
	OV.Slash("say start")
	Stub.Advance(4)
	local chat = table.concat(Stub.chat, "\n")
	check(string.find(chat, "saycal sent 1 to Bobby at 31.5 yd", 1, true), "each line is printed with its distance: " .. string.sub(chat, -200))
	Stub.FireEvent("CHAT_MSG_SAY", "ovcal 1 2000", "Bobby", "Common")
	check(string.find(table.concat(Stub.chat, "\n"), "saycal heard 1 from Bobby at 31.5 yd", 1, true), "and what is heard")
	OV.Slash("say show")
	eq(OV.Calibrate.show, false, "show toggles off")
	local before = table.getn(Stub.chat)
	Stub.Advance(4)
	local loud = 0
	for i = before + 1, table.getn(Stub.chat) do
		if string.find(Stub.chat[i], "saycal", 1, true) then loud = loud + 1 end
	end
	eq(loud, 0, "quiet again")
	OV.Slash("say stop")
end

sec("say calibration: a character that cannot speak only listens")
do
	local units = { u1 = { name = "Bobby", player = true, dist = 20 } }
	local OV = Stub.Login({ classic = true, units = units })
	OV.Slash("say with Bobby")
	OV.Slash("say listen 30")
	eq(OV.Calibrate.on, true, "listening is on")
	eq(OV.Calibrate.listenOnly, true, "and says nothing")
	Stub.Advance(1)
	check(string.find(OV.TraceText(), "saycal start with Bobby, listening only", 1, true), "the start line says so (the PC-side analyzer reads it)")
	Stub.Advance(10)
	eq(table.getn(Stub.said), 0, "it never spoke, not once")
	units.u1.dist = 26
	Stub.FireEvent("CHAT_MSG_SAY", "ovcal 1 2000", "Bobby", "Common")
	units.u1.dist = 34
	Stub.Advance(4)
	Stub.FireEvent("CHAT_MSG_SAY", "ovcal 3 2000", "Bobby", "Common")
	eq(OV.Calibrate.stats.heard, 2, "it still hears the partner")
	eq(OV.Calibrate.stats.missed, 1, "and a gap in the numbers is still a miss")
	check(OV.Calibrate.missLog[1] and OV.Calibrate.missLog[1].seq == 2 and OV.Calibrate.missLog[1].dist ~= nil, "with its distance")
	OV.Slash("say report")
	check(string.find(table.concat(Stub.chat, "\n"), "(listening only)", 1, true), "the report says it was listening only")
	Stub.Advance(30)
	eq(OV.Calibrate.on, false, "listen 30 stops itself")
	eq(table.getn(Stub.said), 0, "still silent")
	OV.Slash("say start")
	eq(OV.Calibrate.listenOnly, false, "a later start speaks again")
	Stub.Advance(3)
	check(table.getn(Stub.said) >= 1, "and it does")
end

sec("inbound heartbeat is cheap")
do
	local units = {}
	local OV = Stub.Login({ classic = true, fileApi = "WriteCustomFile", units = units })
	local names = {}
	for i = 1, 300 do names[i] = string.format("Pl%04d", i) end
	local function file(seq, character, list)
		local body = "C interval=0.5 checks=80 report=60\n"
		local i = 1
		while i <= table.getn(list) do
			local chunk = {}
			for j = i, math.min(i + 24, table.getn(list)) do chunk[table.getn(chunk) + 1] = list[j] end
			body = body .. "R " .. table.concat(chunk, ",") .. "\n"
			i = i + 25
		end
		return string.format("OVI1 %d %d %04X %s\n", seq, 1760000000 + seq, OV.Frame.CRC16(body), character) .. body
	end
	local parses = 0
	local realParse = OV.Inbox.Parse
	OV.Inbox.Parse = function(text) parses = parses + 1; return realParse(text) end
	Stub.fs["eqvoice-in-Me.txt"] = file(1, "Me", names)
	Stub.Advance(2)
	eq(parses, 1, "the first file is parsed in full")
	eq(OV.Lookup.n, 300, "and the roster is in")
	-- heartbeats: same roster, new seq, every 4 s for a minute
	for seq = 2, 16 do
		Stub.fs["eqvoice-in-Me.txt"] = file(seq, "Me", names)
		Stub.Advance(4)
		check(OV.Inbox.Fresh(OV.Now()), "still fresh after heartbeat " .. seq)
	end
	eq(parses, 1, "fifteen heartbeats cost no parse at all")
	eq(OV.Inbox.seq, 16, "but the sequence number followed")
	eq(OV.Inbox.stats.refreshed, 15, "and each was counted as a refresh")
	check(OV.Lookup.nresults == 0 and OV.Lookup.n == 300, "lookups kept running on the same roster")
	-- a real change is parsed
	names[301] = "Newbie"
	Stub.fs["eqvoice-in-Me.txt"] = file(17, "Me", names)
	Stub.Advance(2)
	eq(parses, 2, "a changed roster is parsed")
	eq(OV.Lookup.n, 301, "and applied")
	-- the same body written for another character is never taken by the shortcut
	Stub.fs["eqvoice-in-Me.txt"] = file(18, "Someone", names)
	Stub.Advance(2)
	eq(parses, 3, "another character's file goes through the full check")
	eq(OV.Inbox.idMismatch, true, "and is ignored")
	-- a torn file (header says one CRC, body is another) is refused, and the old roster stays
	Stub.fs["eqvoice-in-Me.txt"] = file(19, "Me", names)
	Stub.Advance(2)
	eq(OV.Inbox.idMismatch, false, "the right character again")
	local good = file(20, "Me", names)
	Stub.fs["eqvoice-in-Me.txt"] = string.gsub(good, "Pl0001", "Pl0002")       -- the body changed, the CRC did not
	Stub.Advance(2)
	check(OV.Inbox.stats.bad >= 1, "a torn file is counted as bad")
	check(OV.Inbox.seq ~= 20, "and its sequence number is not taken")
	eq(OV.Lookup.order[1], "Pl0001", "the previous roster stays")
end

sec("lookup cost at scale benchmark")
do
	local OV = Stub.Login({ classic = true })
	OV.Slash("diag scale")
	Stub.Advance(24)
	check(OV.db.diag.scale ~= nil and OV.db.diag.scale.ok, "the scale benchmark ran and recorded")
	local r = OV.db.diag.scale and OV.db.diag.scale.result or ""
	for _, n in ipairs({ 50, 100, 250, 500, 1000 }) do
		check(string.find(r, string.format("%4d names", n), 1, true), "a row for " .. n .. " names")
	end
	check(string.find(r, "sweep", 1, true) and string.find(r, "us per lookup", 1, true) and string.find(r, "roster file parse", 1, true),
		"with the sweep time, the cost per lookup and the cost of parsing the file")
	local lines = OV.Diag.ScaleReport(4.0, { 5.0, 6.0, 9.0, 14.0, 24.0 }, { 4.5, 5.0, 6.0, 9.0, 16.0 })
	check(string.find(lines[5], "1000 names: 24.00 ms/frame (42 fps; idle 4.00 ms), 20.0 us per lookup", 1, true), "the arithmetic: " .. lines[5])
	check(string.find(lines[5], "roster file parse 12.0 ms", 1, true) and string.find(lines[1], "roster file parse 0.5 ms", 1, true), "parse cost per size: " .. lines[5])
	local fake = OV.Diag.FakeRosterText(1000)
	local parsed = OV.Inbox.Parse(fake)
	check(parsed and parsed.count == 1000, "the made-up roster is a valid roster file of 1000 names")
	local OV2 = Stub.Login({})
	OV2.Slash("diag scale")
	Stub.Advance(1)
	eq(OV2.db.diag.scale, nil, "no benchmark without the API")
end

require("panel_tests")(Stub, sec, check, eq)
require("voice_ui_tests")(Stub, sec, check, eq)

print(string.format("\n%d passed, %d failed", passed, failed))
os.exit(failed)
