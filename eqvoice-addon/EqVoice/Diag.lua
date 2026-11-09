-- EqVoice / Diag.lua
-- Empirical tests for the things nobody can know from the armchair. Run them on
-- a throwaway character. Results are printed and also kept in EqVoiceDB.diag
-- (written to disk at logout / /reload; tools/read_savedvars.py prints them).
--
--   /ev diag api                 what exists on this client
--   /ev diag nearby               who is around me, and how far (no group needed)?
--   /ev diag nearby watch         print that every 2 s while you walk
--   /ev diag name <player>        look one player up by name
--   /ev diag all                  probe every chat type the 1.12 client might know
--   /ev diag say, yell, party, raid, guild, officer, battleground, whisper, channel or group
--                                send 3 probes, wait for them to loop back
--   /ev diag beacon say          second client: send a numbered beacon each second
--   /ev diag listen              print every OVD message heard (run on client B)
--   /ev diag rate say 2 10 go    delivery vs. send rate (can trigger flood limits)
--   /ev diag bridge [seconds]    paint a known demo frame for the client's bridge test
--   /ev diag file                try the mod file APIs
--   /ev diag report              print the saved results
--
-- Diagnostics use their own prefix (OVD) so they never mix with heartbeats.

local OV = EqVoice
local Diag = {}
OV.Diag = Diag

Diag.tests    = {}      -- running probe / rate tests
Diag.ntests   = 0
Diag.beacon   = nil
Diag.listen   = false
Diag.peers    = {}
Diag.demoUntil = 0

local PROBE_COUNT   = 3
local PROBE_WAIT    = 6      -- seconds before a probe is judged
local KNOWN = { say = "SAY", yell = "YELL", party = "PARTY", raid = "RAID", guild = "GUILD",
                officer = "OFFICER", battleground = "BATTLEGROUND", whisper = "WHISPER", channel = "CHANNEL" }
-- Chat types that need a 4th argument (a player name, or a channel number) and cannot be used for beacons.
local NEEDS_TARGET = { WHISPER = true, CHANNEL = true }
local ALL_ORDER = { "say", "yell", "officer", "battleground", "whisper", "channel" }

Diag.CHANNEL_NAME = "ovdiag"
Diag.watchNearby = false
Diag.watchNext = 0


local function g(name)
	if getglobal then return getglobal(name) end
	if _G then return _G[name] end
	return nil
end

local function say(msg) OV.Print("|cffffcc00diag|r " .. msg) end

local function record(key, ok, result, extra)
	OV.db.diag = OV.db.diag or {}
	local d = { ok = ok and true or false, result = result, t = time and time() or 0 }
	if extra then
		for k, v in pairs(extra) do d[k] = v end
	end
	OV.db.diag[key] = d
	OV.Log("diag " .. key .. ": " .. result)
end

local function yn(b) return b and "yes" or "no" end

-- /ev diag nearby : what can this client tell me about the players around me, without any group?
function Diag.Nearby(now)
	local S = OV.Scan
	local caps = S.Capabilities()
	say("scan sources: ClassicAPI nameplates " .. yn(caps.classicNameplates) .. ", UnitDistanceSquared " .. yn(caps.distanceSquared) ..
		", UnitTokenFromName " .. yn(caps.tokenFromName) .. ", SuperWoW plates " .. yn(caps.superWoWPlates) ..
		", UnitXP " .. yn(caps.unitXP) .. ", UnitPosition " .. yn(caps.unitPosition) .. "  ->  method: " .. caps.method)
	local count, a, b, c, d = S.PositionShape()
	if count then
		say("UnitPosition(player) returns " .. count .. " values: " .. tostring(a) .. ", " .. tostring(b) .. ", " .. tostring(c) .. ", " .. tostring(d))
	end
	local cv = {}
	local names = { "NameplateRange", "NP_NameplateDistance" }
	for i = 1, table.getn(names) do
		local ok, v = pcall(GetCVar, names[i])
		cv[table.getn(cv) + 1] = names[i] .. "=" .. ((ok and v ~= nil) and tostring(v) or "n/a")
	end
	say("nameplate reach: " .. table.concat(cv, "  "))
	local list, n, method = S.Players()
	local shown, parts = n, {}
	if shown > 12 then shown = 12 end
	for i = 1, shown do
		local e = list[i]
		parts[i] = e.name .. (e.dist and string.format(" %.1f yd", e.dist) or " ?") .. (e.enemy and " (enemy)" or "")
	end
	local withDist = 0
	for i = 1, n do if list[i].dist then withDist = withDist + 1 end end
	local result = "method=" .. method .. " players=" .. n .. " with distance=" .. withDist ..
		(n > 0 and ("  " .. table.concat(parts, ", ")) or "")
	local st = S.stats
	say("stages: " .. st.guids .. " units seen -> " .. n .. " players  (not players " .. st.notPlayer .. ", no name " .. st.noName ..
		", myself " .. st.self .. ", api errors " .. st.errors .. ")")
	if n == 0 then
		say("no players found: raw look at every source follows")
		local lines, ln = S.Probe()
		for i = 1, ln do say("  " .. lines[i]) end
	end
	say(result)
	record("nearby", method ~= "none" and (withDist > 0 or n == 0 and caps.method ~= "frames"), result, { method = method, players = n, distances = withDist })
end

-- /ev diag name <Name> : look a player up by name, the way a directory-driven scan would
function Diag.Name(name)
	if not name or name == "" then say("usage: /ev diag name <exact character name>") return end
	local r, why = OV.Scan.Lookup(name)
	if not r then
		say("name " .. name .. " -> " .. tostring(why))
		record("name", false, "name " .. name .. ": " .. tostring(why))
		return
	end
	local res = string.format("%s -> token %s, player=%s, distance %s", tostring(r.name), tostring(r.token), tostring(r.player),
		r.dist and string.format("%.1f yd", r.dist) or "unknown")
	say(res)
	record("name", r.dist ~= nil, res)
end

local function watchTick(now)
	if not Diag.watchNearby or now < Diag.watchNext then return end
	Diag.watchNext = now + 2
	local list, n, method = OV.Scan.Players()
	local parts = {}
	for i = 1, n do
		if i > 10 then break end
		parts[i] = list[i].name .. (list[i].dist and string.format(" %.1f", list[i].dist) or " ?")
	end
	say("nearby [" .. method .. "] " .. n .. ": " .. table.concat(parts, ", "))
end

-- /ev diag inbox : can the game read a file that something outside the game wrote? (the client -> addon direction)
-- Reads exactly what the addon reads each second (eqvoice-in-<Character>.txt first, then eqvoice-in.txt) and says
-- whether it parses as a roster file. The plain test text that ships in CustomData is not one, and that is fine: the point
-- is that the bytes arrive.
function Diag.Inbox()
	local I = OV.Inbox
	local text = I.ReadFile()
	if not text then
		local who = OV.FileSafeName and OV.FileSafeName()
		local res = "no file read: put a text file named " .. (who and ("eqvoice-in-" .. who .. ".txt or ") or "") ..
			"eqvoice-in.txt in the game folder's CustomData\\ (or Imports\\) and run this again"
		say(res)
		record("inbox", false, res)
		return
	end
	local shown = string.gsub(string.sub(text, 1, 70), "%c", " ")
	local res = tostring(I.source) .. " -> " .. string.len(text) .. " bytes: " .. shown
	local t, why = I.Parse(text)
	if t then
		res = res .. "  | VALID roster file: seq " .. t.seq .. ", for " .. t.character .. ", " .. t.count .. " name(s)" ..
			(t.character ~= OV.character and ("  (but this game is " .. tostring(OV.character) .. ": it would be ignored)") or "")
	else
		res = res .. "  | readable, but not a roster file (" .. tostring(why) .. ") - fine for a plain test file; the desktop client writes the real one"
	end
	say(res)
	record("inbox", true, res)
end

-- /ev diag lookupcost [n] : what do N name lookups per frame do to this client's frame time?
-- Counts frames for 2 s idle, then 3 s with n lookups of names that do not exist (the common case:
-- most roster names are not nearby), and reports the difference per lookup.
Diag.bench = nil

function Diag.LookupCost(n, now)
	if not OV.Lookup.Available() then say("lookups unavailable (needs ClassicAPI)") return end
	if Diag.bench then say("a lookup benchmark is already running") return end
	n = OV.clamp(math.floor(tonumber(n) or 100), 1, 2000)
	local b = { n = n, phase = "idle", t0 = now, frames = 0, idleFrames = 0, loadFrames = 0, idleSecs = 2, loadSecs = 3, acc = 0 }
	Diag.bench = b
	local f = CreateFrame("Frame")
	b.frame = f
	f:SetScript("OnUpdate", function()
		local dt = arg1 or 0
		b.acc = b.acc + dt
		b.frames = b.frames + 1
		if b.phase == "idle" then
			if b.acc >= b.idleSecs then
				b.idleFrames, b.idleElapsed = b.frames, b.acc
				b.phase, b.acc, b.frames = "load", 0, 0
			end
		else
			for i = 1, b.n do OV.Lookup.CheckOne("Zzqxv" .. i) end
			if b.acc >= b.loadSecs then
				b.loadFrames, b.loadElapsed = b.frames, b.acc
				f:SetScript("OnUpdate", nil)
				local idleMs = b.idleElapsed / b.idleFrames * 1000
				local loadMs = b.loadElapsed / b.loadFrames * 1000
				local per = (loadMs - idleMs) / b.n * 1000
				local res = string.format("%d lookups/frame: frame time %.2f ms idle -> %.2f ms (%.0f -> %.0f fps), about %.1f us per lookup",
					b.n, idleMs, loadMs, 1000 / idleMs, 1000 / loadMs, per)
				say(res)
				record("lookupcost", true, res, { n = b.n, idleMs = idleMs, loadMs = loadMs })
				Diag.bench = nil
			end
		end
	end)
	say("benchmark started: 2 s idle, then 3 s of " .. n .. " lookups per frame. Do not move the camera.")
end

-- /ev diag scale : what a big roster costs this client, at 50, 100, 250, 500 and 1000 names.
-- Counts frames for 2 s idle, then 2 s each with (a) n lookups of names that do not exist per frame (the common case: most
-- roster names are nowhere near) and (b) parsing a valid n-name roster file once per frame. Prints one row per size: frame time,
-- fps, microseconds per lookup, milliseconds per parse, and for the schedule in force how long a full sweep takes and what it
-- costs per second. (The roster file is parsed in full only when the roster changes; a heartbeat is accepted by its header.)
Diag.scale = nil
Diag.SCALE_SIZES = { 50, 100, 250, 500, 1000 }

-- A valid roster file for n made-up names, for timing the parser.
function Diag.FakeRosterText(n)
	local body = "C interval=0.5 checks=80 report=60\n"
	local i = 1
	while i <= n do
		local chunk = {}
		for j = i, math.min(i + 24, n) do chunk[table.getn(chunk) + 1] = string.format("Zz%05d", j) end
		body = body .. "R " .. table.concat(chunk, ",") .. "\n"
		i = i + 25
	end
	return string.format("OVI1 1 1700000000 %04X Me\n", OV.Frame.CRC16(body)) .. body
end

-- idleMs: frame time with nothing extra. lookupMs[i], parseMs[i]: frame time while doing the work for SCALE_SIZES[i].
function Diag.ScaleReport(idleMs, lookupMs, parseMs)
	local L = OV.Lookup
	local checks, interval = L.cfg.maxChecks, L.cfg.interval
	local out = {}
	for i = 1, table.getn(Diag.SCALE_SIZES) do
		local n = Diag.SCALE_SIZES[i]
		local per = (lookupMs[i] - idleMs) / n * 1000
		if per < 0 then per = 0 end
		local perSecond = checks / interval
		local sweep = n / checks * interval
		local parse = parseMs and (parseMs[i] - idleMs) or 0
		if parse < 0 then parse = 0 end
		out[i] = string.format("%4d names: %.2f ms/frame (%.0f fps; idle %.2f ms), %.1f us per lookup | schedule %d per %.1f s: sweep %.1f s, %.0f lookups/s = %.2f ms per second | roster file parse %.1f ms",
			n, lookupMs[i], 1000 / lookupMs[i], idleMs, per, checks, interval, sweep, perSecond, per * perSecond / 1000, parse)
	end
	return out
end

function Diag.Scale(now)
	if not OV.Lookup.Available() then say("lookups unavailable (needs ClassicAPI)") return end
	if Diag.bench or Diag.scale then say("a lookup benchmark is already running") return end
	local sizes = Diag.SCALE_SIZES
	local phases = { { kind = "idle" } }
	for i = 1, table.getn(sizes) do phases[table.getn(phases) + 1] = { kind = "lookup", n = sizes[i], i = i } end
	for i = 1, table.getn(sizes) do phases[table.getn(phases) + 1] = { kind = "parse", n = sizes[i], i = i, text = Diag.FakeRosterText(sizes[i]) } end
	local s = { at = 1, acc = 0, frames = 0, secs = 2, lookupMs = {}, parseMs = {}, phases = phases }
	Diag.scale = s
	local f = CreateFrame("Frame")
	s.frame = f
	f:SetScript("OnUpdate", function()
		s.acc = s.acc + (arg1 or 0)
		s.frames = s.frames + 1
		local ph = s.phases[s.at]
		if ph.kind == "lookup" then
			for i = 1, ph.n do OV.Lookup.CheckOne("Zzqxv" .. i) end
		elseif ph.kind == "parse" then
			OV.Inbox.Parse(ph.text)
		end
		if s.acc >= s.secs then
			local ms = s.acc / s.frames * 1000
			if ph.kind == "idle" then s.idleMs = ms
			elseif ph.kind == "lookup" then s.lookupMs[ph.i] = ms
			else s.parseMs[ph.i] = ms end
			s.at = s.at + 1
			s.acc, s.frames = 0, 0
			if s.at > table.getn(s.phases) then
				f:SetScript("OnUpdate", nil)
				local lines = Diag.ScaleReport(s.idleMs, s.lookupMs, s.parseMs)
				for i = 1, table.getn(lines) do say(lines[i]) end
				record("scale", true, table.concat(lines, " // "), { idleMs = s.idleMs })
				Diag.scale = nil
			end
		end
	end)
	say("scale benchmark started: 2 s idle, then 2 s each for lookups and roster parsing at 50, 100, 250, 500 and 1000 names (about 22 s). Do not move the camera.")
end

-- ---------------------------------------------------------------------- api

function Diag.Api()
	local names = {
		"SendAddonMessage", "RegisterAddonMessagePrefix", "C_ChatInfo",
		"WriteCustomFile", "ReadCustomFile", "CustomFileExists", "ExportFile", "ImportFile",
		"UnitXP", "UnitPosition", "UnitNameplate", "LoggingChat",
		"GetPlayerMapPosition", "SetMapToCurrentZone", "GetRealZoneText",
		"IsInGuild", "GetNumRaidMembers", "GetNetStats",
	}
	local present, absent = {}, {}
	for i = 1, table.getn(names) do
		local v = g(names[i])
		if v ~= nil then
			present[table.getn(present) + 1] = names[i] .. "(" .. type(v) .. ")"
		else
			absent[table.getn(absent) + 1] = names[i]
		end
	end
	if type(UnitXP) == "function" then
		local ok = pcall(UnitXP, "nop", "nop")
		present[table.getn(present) + 1] = "UnitXP-call:" .. (ok and "ok" or "error")
	end
	local ver, build, date = "?", "?", "?"
	if GetBuildInfo then ver, build, date = GetBuildInfo() end
	local lines = {
		"client " .. tostring(ver) .. " build " .. tostring(build) .. " " .. tostring(date) ..
			"  lua " .. tostring(_VERSION) .. "  locale " .. tostring(GetLocale and GetLocale() or "?"),
		"present: " .. table.concat(present, " "),
		"absent:  " .. table.concat(absent, " "),
		"file api: " .. tostring(OV.Bridge.FileApi()) ..
			"  gxWindow=" .. tostring(GetCVar and GetCVar("gxWindow") or "?") ..
			"  gxResolution=" .. tostring(GetCVar and GetCVar("gxResolution") or "?") ..
			"  useUiScale=" .. tostring(GetCVar and GetCVar("useUiScale") or "?"),
	}
	for i = 1, table.getn(lines) do say(lines[i]) end
	OV.db.diag = OV.db.diag or {}
	OV.db.diag.api = { ok = true, result = table.concat(lines, " // "), t = time and time() or 0 }
	return lines
end

-- ------------------------------------------------------------ probe / rate

local function newNonce()
	return string.format("%06X", math.floor(math.random() * 16777215))
end

local function addTest(t)
	Diag.ntests = Diag.ntests + 1
	Diag.tests[Diag.ntests] = t
end

-- Loopback probe: does the client accept this chat type, and does the server
-- hand the message back to the sender?
--   CHANNEL joins a throwaway channel first and sends to its number;
--   WHISPER sends to yourself (the client's acceptance of the type is the answer).
function Diag.StartProbe(chan, now)
	local key = string.lower(chan)
	local t = {
		kind = "probe", chan = chan, key = key, nonce = newNonce(), t0 = now, started = now,
		sentAt = {}, nsent = 0, echoed = 0, errors = 0, lastErr = nil,
		lat = 0, sender = nil, dist = nil, done = false, target = nil, joining = false,
	}
	if chan == "WHISPER" then
		t.target = UnitName and UnitName("player") or nil
	elseif chan == "CHANNEL" then
		if type(JoinChannelByName) ~= "function" or type(GetChannelName) ~= "function" then
			record(key, false, "NO CHANNEL API")
			say("CHANNEL -> NO CHANNEL API on this client")
			return nil
		end
		JoinChannelByName(Diag.CHANNEL_NAME)
		t.joining = true
	end
	addTest(t)
	say("probe " .. chan .. " started (" .. PROBE_COUNT .. " messages, result in " .. PROBE_WAIT .. " s)")
	return t
end

local function probeSend(t, now)
	t.nsent = t.nsent + 1
	t.sentAt[t.nsent] = now
	local ok, err = pcall(SendAddonMessage, OV.DIAG_PREFIX, "P:" .. t.nonce .. ":" .. t.nsent, t.chan, t.target)
	if not ok then
		t.errors = t.errors + 1
		t.lastErr = tostring(err)
	end
end

function Diag.StartRate(chan, perSec, seconds, now)
	perSec = OV.clamp(tonumber(perSec) or 1, 0.2, 10)
	seconds = OV.clamp(tonumber(seconds) or 10, 2, 30)
	local t = {
		kind = "rate", chan = chan, key = "rate_" .. string.lower(chan), nonce = newNonce(),
		t0 = now, perSec = perSec, seconds = seconds, nsent = 0, echoed = 0,
		errors = 0, lastErr = nil, nextT = now, done = false, sentAt = {},
	}
	addTest(t)
	say(string.format("rate test %s: %.1f msg/s for %d s", chan, perSec, seconds))
	return t
end

local function finishProbe(t)
	t.done = true
	local result, ok
	if t.nsent > 0 and t.errors == t.nsent then
		ok, result = false, "ERROR " .. tostring(t.lastErr)
	elseif t.echoed == 0 then
		ok, result = false, "SENT " .. t.nsent .. ", NO ECHO (the client accepted the type)"
	else
		ok = true
		result = string.format("LOOPBACK %d/%d  avg %.0f ms  sender=%s dist=%s",
			t.echoed, t.nsent, (t.lat / t.echoed) * 1000, tostring(t.sender), tostring(t.dist))
	end
	record(t.key, ok, result)
	say(t.chan .. " -> " .. result)
	if t.chan == "CHANNEL" and type(LeaveChannelByName) == "function" then
		LeaveChannelByName(Diag.CHANNEL_NAME)
	end
end

local function finishRate(t)
	t.done = true
	local pct = 0
	if t.nsent > 0 then pct = (t.echoed / t.nsent) * 100 end
	local result = string.format("sent %d echoed %d (%.0f%%) errors %d", t.nsent, t.echoed, pct, t.errors)
	record(t.key, t.echoed > 0, result)
	say(t.chan .. " rate " .. string.format("%.1f/s", t.perSec) .. " -> " .. result ..
		"  (a low echo percentage at a high rate means the limit is below it)")
end

-- ------------------------------------------------------------------ beacon

function Diag.StartBeacon(chan, on, now)
	if not on then
		Diag.beacon = nil
		say("beacon stopped")
		return
	end
	Diag.beacon = { chan = chan, n = 0, nextT = now }
	say("beacon on " .. chan .. ": one numbered message per second. Walk away; the other client runs /ev diag listen.")
end

local function mapPos()
	if OV.GetLoc then
		local l = OV.GetLoc()
		if l then return l.zone, l.x, l.y end
	end
	return "?", 0, 0
end

-- ---------------------------------------------------------------- receiving

function Diag.OnAddon(prefix, text, dist, sender, now)
	if prefix ~= OV.DIAG_PREFIX then return end
	local p, n = OV.split(text, ":")
	local me = UnitName and UnitName("player") or nil
	if sender == me then
		-- own message came back: that is the loopback proof
		for i = 1, Diag.ntests do
			local t = Diag.tests[i]
			if not t.done and p[1] ~= "R" and p[2] == t.nonce then
				local idx = tonumber(p[3])
				t.echoed = t.echoed + 1
				t.sender, t.dist = sender, dist
				if idx and t.sentAt[idx] then t.lat = t.lat + (now - t.sentAt[idx]) end
			end
		end
		return
	end
	if p[1] == "R" and n >= 5 then
		local peer = Diag.peers[sender] or { missed = 0 }
		local seq = tonumber(p[2]) or 0
		if peer.n and seq > peer.n + 1 then peer.missed = peer.missed + (seq - peer.n - 1) end
		peer.n, peer.last, peer.reported = seq, now, false
		peer.tx, peer.ty, peer.zone = tonumber(p[3]) or 0, tonumber(p[4]) or 0, p[5]
		peer.dist = dist
		local mz, mx, my = mapPos()
		peer.mx, peer.my, peer.mz = mx, my, mz
		Diag.peers[sender] = peer
		if Diag.listen then
			say(string.format("beacon %s #%d via %s  them %s %d,%d  me %s %d,%d  missed %d",
				sender, seq, tostring(dist), p[5], peer.tx, peer.ty, mz, mx, my, peer.missed))
		end
	elseif Diag.listen then
		say("heard " .. tostring(sender) .. " via " .. tostring(dist) .. ": " .. text)
	end
end

-- -------------------------------------------------------------------- tick

function Diag.Tick(now)
	watchTick(now)
	for i = 1, Diag.ntests do
		local t = Diag.tests[i]
		if not t.done then
			if t.kind == "probe" and t.joining then
				-- wait for the server to confirm the throwaway channel, then start the clock
				local idx = GetChannelName(Diag.CHANNEL_NAME)
				if idx and idx > 0 then
					t.joining, t.target, t.t0 = false, idx, now
				elseif now - t.started > 5 then
					t.joining, t.done = false, true
					record(t.key, false, "NO CHANNEL (could not join " .. Diag.CHANNEL_NAME .. ")")
					say("CHANNEL -> could not join the test channel")
				end
			elseif t.kind == "probe" then
				if t.nsent < PROBE_COUNT and now >= t.t0 + t.nsent then probeSend(t, now) end
				if now >= t.t0 + PROBE_WAIT then finishProbe(t) end
			else
				while t.nsent < t.perSec * t.seconds and now >= t.nextT do
					t.nsent = t.nsent + 1
					t.sentAt[t.nsent] = now
					local ok, err = pcall(SendAddonMessage, OV.DIAG_PREFIX, "S:" .. t.nonce .. ":" .. t.nsent, t.chan)
					if not ok then t.errors = t.errors + 1; t.lastErr = tostring(err) end
					t.nextT = t.nextT + 1 / t.perSec
				end
				if now >= t.t0 + t.seconds + 3 then finishRate(t) end
			end
		end
	end
	if Diag.beacon and now >= Diag.beacon.nextT then
		local b = Diag.beacon
		b.n = OV.mod(b.n + 1, 65536)
		b.nextT = now + 1
		local zone, x, y = mapPos()
		pcall(SendAddonMessage, OV.DIAG_PREFIX, "R:" .. b.n .. ":" .. x .. ":" .. y .. ":" .. zone, b.chan)
	end
	for sender, peer in pairs(Diag.peers) do
		if peer.last and not peer.reported and now - peer.last > 6 then
			peer.reported = true
			local mz, mx, my = mapPos()
			say(string.format("LOST %s: last beacon #%d, them %s %d,%d, me now %s %d,%d (they were at map %s %d,%d when last heard)",
				sender, peer.n or 0, tostring(peer.zone), peer.tx or 0, peer.ty or 0, mz, mx, my,
				tostring(peer.mz), peer.mx or 0, peer.my or 0))
		end
	end
end

-- ---------------------------------------------------------------- file test

function Diag.File()
	local api = OV.Bridge.FileApi()
	if not api then
		say("no file API on this client (neither WriteCustomFile nor ExportFile). The optical bridge is the only option.")
		record("file", false, "no file api")
		return
	end
	local stamp = "selftest " .. tostring(time and time() or 0)
	local ok, err
	if api == "WriteCustomFile" then
		ok, err = pcall(WriteCustomFile, "eqvoice-selftest.txt", stamp, "w")
	else
		ok, err = pcall(ExportFile, "eqvoice-selftest", stamp)
	end
	if not ok then
		say(api .. " raised: " .. tostring(err))
		record("file", false, api .. " error " .. tostring(err))
		return
	end
	local back = nil
	if api == "WriteCustomFile" and type(ReadCustomFile) == "function" then
		local ok2, v = pcall(ReadCustomFile, "eqvoice-selftest.txt")
		if ok2 then back = v end
	elseif type(ImportFile) == "function" then
		local ok2, v = pcall(ImportFile, "eqvoice-selftest")
		if ok2 then back = v end
	end
	local res = api .. " wrote ok; read-back " .. tostring(back == stamp and "MATCH" or ("MISMATCH " .. tostring(back)))
	say(res .. ". Now look for eqvoice-selftest.txt under the game folder (CustomData\\ or Imports\\).")
	record("file", back == stamp, res, { api = api })
end

-- ------------------------------------------------------------------ report

function Diag.Report()
	local d = OV.db.diag
	if not d then say("nothing recorded yet") return end
	for k, v in pairs(d) do
		say(k .. ": " .. (v.ok and "ok " or "NO ") .. tostring(v.result))
	end
end

-- Demo frame for /ev diag bridge: a fixed, recognisable state.
function Diag.DemoState()
	return {
		flags = 1 + 2 + 4 + 8 + 32, loginId = 1234567, wall = time and time() or 0, ack = 0, rosterSize = 2,
		checked = 2, loaded = 2, misses = 0, intervalMs = 500, cost = 0,
		character = "Testchar", realm = "Testrealm",
		results = {
			{ name = "Alpha", dist = 12, flags = 1 },
			{ name = "Bravo", dist = 31.5, flags = 1 },
		},
	}
end

-- --------------------------------------------------------------- dispatcher

local function groupChannels()
	local list, n = {}, 0
	if IsInGuild and IsInGuild() then n = n + 1; list[n] = "GUILD" end
	if GetNumRaidMembers and GetNumRaidMembers() > 0 then n = n + 1; list[n] = "RAID"
	elseif GetNumPartyMembers and GetNumPartyMembers() > 0 then n = n + 1; list[n] = "PARTY" end
	return list, n
end

function Diag.Handle(w, n, now, raw)
	local sub = w[2] or ""
	if sub == "api" then
		Diag.Api()
	elseif KNOWN[sub] then
		Diag.StartProbe(KNOWN[sub], now)
	elseif sub == "nearby" then
		if w[3] == "watch" then
			Diag.watchNearby = not Diag.watchNearby
			Diag.watchNext = 0
			say("nearby watch " .. (Diag.watchNearby and "on (every 2 s; run again to stop)" or "off"))
		else
			Diag.Nearby(now)
		end
	elseif sub == "name" then
		Diag.Name(raw and raw[3] or w[3])
	elseif sub == "inbox" then
		Diag.Inbox()
	elseif sub == "lookupcost" then
		Diag.LookupCost(w[3], now)
	elseif sub == "scale" then
		Diag.Scale(now)
	elseif sub == "all" then
		for i = 1, table.getn(ALL_ORDER) do Diag.StartProbe(KNOWN[ALL_ORDER[i]], now) end
		say("all probes started; results appear within about 12 s")
	elseif sub == "group" then
		local list, c = groupChannels()
		if c == 0 then say("not in a guild, party or raid: nothing to test") end
		for i = 1, c do Diag.StartProbe(list[i], now) end
	elseif sub == "beacon" then
		if w[3] == "off" then
			Diag.StartBeacon(nil, false, now)
			return
		end
		local chan = KNOWN[w[3] or "say"]
		if not chan or NEEDS_TARGET[chan] then say("usage: /ev diag beacon say/yell/party/raid/guild/officer or off") return end
		Diag.StartBeacon(chan, true, now)
	elseif sub == "listen" then
		Diag.listen = not Diag.listen
		say("listen " .. (Diag.listen and "on" or "off"))
	elseif sub == "rate" then
		local chan = KNOWN[w[3] or ""]
		if not chan or NEEDS_TARGET[chan] then say("usage: /ev diag rate say/yell/party/raid/guild/officer <per-second> <seconds> go") return end
		if w[6] ~= "go" then
			say("this sends many messages quickly and can get you flood-kicked on some servers. Repeat the command with 'go' on the end.")
			return
		end
		Diag.StartRate(chan, w[4], w[5], now)
	elseif sub == "bridge" then
		local secs = OV.clamp(tonumber(w[3]) or 30, 5, 600)
		Diag.demoUntil = now + secs
		say("painting the demo frame for " .. secs .. " s: character Testchar-Testrealm, results Alpha 12.0 yd and Bravo 31.5 yd")
	elseif sub == "file" then
		Diag.File()
	elseif sub == "report" then
		Diag.Report()
	else
		say("api, nearby, nearby watch, name <player>, inbox, lookupcost [n], scale, all, say, yell, party, raid, guild, officer, battleground, whisper, channel, group, beacon <chan>, listen, rate <chan> <n> <s> go, bridge [s], file, report")
	end
end
