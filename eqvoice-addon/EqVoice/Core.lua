-- EqVoice / Core.lua
-- Wires the modules to the client: events, the periodic tick, saved settings, the bridge and the
-- /ev commands.
--
-- The loop, every 0.25 s tick:
--   1. poll the inbound file (once a second): roster of names to look up, configuration
--   2. scan: ask the client where each roster name is (Lookup), at most every scan interval
--   3. publish: write the frame (identity, health, results) to the outbound file, and paint a
--      truncated copy as colour cells
-- Group heartbeats (Comm/Nearby/Proto) are a diagnostic harness, off by default.

local OV = EqVoice

OV.defaults = {
	enabled   = true,
	transport = "OFF",      -- OFF | AUTO | GROUP | SAY | YELL : group heartbeats are a test harness only
	radius    = 150,        -- group harness only: map units out of 10000
	maxPeers  = 8,
	optical   = false,     -- the coloured block in a screen corner (a diagnostic copy of the bridge): off. The file bridge carries everything.
	file      = true,
	cell      = 6,
	corner    = "TOPLEFT",
	debug     = false,
	-- chatTrace (nil = off): narrate every boundary in the chat too (/ev trace on). The bridge file carries the narration whatever this is.
}

local TICK         = 0.25
local INBOX_POLL   = 1.0
local PUBLISH_MIN  = 0.25   -- seconds between frames when new results arrived
local PUBLISH_IDLE = 1.0    -- liveness: the client treats a silent bridge as dead

OV.started     = false
OV.counter     = 0
OV.lastPub     = -100
OV.lastInbox   = -100
OV.lastScan    = -100
OV.dirty       = true
OV.locCache    = nil
OV.locAt       = -100
OV.character   = nil
OV.realm       = nil
OV.loginId     = 0

-- ---------------------------------------------------------------- settings

function OV.InitDB()
	if type(EqVoiceDB) ~= "table" then EqVoiceDB = {} end
	for k, v in pairs(OV.defaults) do
		if EqVoiceDB[k] == nil then EqVoiceDB[k] = v end
	end
	if type(EqVoiceDB.diag) ~= "table" then EqVoiceDB.diag = {} end
	if type(EqVoiceDB.ui) ~= "table" then EqVoiceDB.ui = {} end       -- the panel: window positions, indicator on/off
	OV.db = EqVoiceDB
	OV.debugOn = OV.db.debug
	if not OV.db.opticalChosen then OV.db.optical = false end              -- an earlier version drew the block by default: only /ev bridge optical|both brings it back
	OV.Bridge.optical = OV.db.optical
	OV.Bridge.file = OV.db.file
	OV.Bridge.corner = OV.db.corner
	OV.Bridge.cellSize = OV.db.cell
end

-- ---------------------------------------------------------------- location
-- Only used by the group harness. Returns { zone, x, y } where zone is "<continent>.<zone>" or
-- "h<hash>", x and y are 0..9999 (0,0 when unknown). SetMapToCurrentZone is skipped while the
-- world map is open so the player's map view is never changed under their hands.

function OV.ComputeLoc()
	local mapOpen = WorldMapFrame and WorldMapFrame.IsVisible and WorldMapFrame:IsVisible()
	if not mapOpen and SetMapToCurrentZone then SetMapToCurrentZone() end
	if not mapOpen and GetCurrentMapContinent then
		local c, z = GetCurrentMapContinent(), GetCurrentMapZone()
		local x, y = 0, 0
		if GetPlayerMapPosition then x, y = GetPlayerMapPosition("player") end
		if c and z and c > 0 and z > 0 and x and y and (x ~= 0 or y ~= 0) then
			return {
				zone = c .. "." .. z,
				x = OV.clamp(math.floor(x * 10000), 0, 9999),
				y = OV.clamp(math.floor(y * 10000), 0, 9999),
			}
		end
	end
	local name = (GetRealZoneText and GetRealZoneText()) or ""
	return { zone = "h" .. OV.Hash4(name), x = 0, y = 0 }
end

function OV.GetLoc()
	local now = OV.Now()
	if OV.locCache and now - OV.locAt < 2 then return OV.locCache end
	OV.locCache = OV.ComputeLoc()
	OV.locAt = now
	return OV.locCache
end

-- ------------------------------------------------------------------ start

function OV.Start()
	if OV.started then return end
	math.randomseed(math.floor((time and time() or 0) + OV.Now() * 1000))
	math.random(); math.random(); math.random()
	OV.character = UnitName("player")
	OV.realm = (GetRealmName and GetRealmName()) or ""
	OV.loginId = math.floor(math.random() * 4294967296)
	OV.sid = OV.NewSid()                    -- group harness only
	OV.nearby = OV.Nearby.New({
		selfName = OV.character,
		selfSid = OV.sid,
		radius = OV.db.radius,
		maxEntries = 40,
	})
	OV.bucket = OV.Comm.NewBucket(OV.Comm.BUCKET_CAP, OV.Comm.BUCKET_RATE, OV.Now())
	OV.Comm.nextHb = OV.Now() + 2 + math.random() * 2
	OV.started = true
	OV.Log("started as " .. tostring(OV.character) .. "-" .. tostring(OV.realm) .. " login " .. OV.loginId)
	OV.Trace(string.format("identity: %s on %s, login id %s, file names %s", tostring(OV.character), tostring(OV.realm),
		tostring(OV.loginId), OV.FileSafeName() and ("eqvoice-" .. OV.FileSafeName() .. ".txt / eqvoice-in-" .. OV.FileSafeName() .. ".txt") or "eqvoice.txt / eqvoice-in.txt"))
	OV.Panel.Init()
end

-- ------------------------------------------------------------------ inbound

local function sameRoster(a, na, b, nb)
	if na ~= nb then return false end
	for i = 1, na do
		if a[i] ~= b[i] then return false end
	end
	return true
end

-- A new inbound file was accepted: take its configuration and roster.
local function nameList(names, count)
	local out = {}
	for i = 1, math.min(count, 6) do out[i] = names[i] end
	return table.concat(out, ",") .. (count > 6 and (",... (" .. count .. " in all)") or "")
end

function OV.ApplyInbox(now)
	local I = OV.Inbox
	OV.Lookup.Configure(I.config)
	if not sameRoster(I.names, I.count, OV.Lookup.order, OV.Lookup.n) then
		OV.Lookup.SetRoster(I.names, I.count)
		OV.Log("roster: " .. I.count .. " names (seq " .. tostring(I.seq) .. ")")
		OV.Trace("inbound: read " .. tostring(I.source) .. " seq " .. tostring(I.seq) .. " for " .. tostring(I.character) ..
			": roster is now " .. I.count .. " name(s): " .. nameList(I.names, I.count))
	elseif I.stats.accepted == 1 then
		OV.Trace("inbound: read " .. tostring(I.source) .. " seq " .. tostring(I.seq) .. " for " .. tostring(I.character) ..
			": " .. I.count .. " name(s)")
	end
	if I.idMismatch and I.lastReason ~= OV.lastMismatch then
		OV.lastMismatch = I.lastReason
		OV.Trace("inbound: IGNORED, " .. tostring(I.lastReason) .. " (this game is " .. tostring(OV.character) .. ")")
	end
	if I.test then OV.Log("inbox test message: " .. I.test) end
	OV.dirty = true
end

local function runLookups(now)
	local fresh = OV.db.enabled and OV.Lookup.Available() and OV.Inbox.Fresh(now)
	if fresh and OV.Lookup.n > 0 then
		if now - OV.lastScan >= OV.Lookup.cfg.interval then
			OV.lastScan = now
			OV.Lookup.Scan(now)
			OV.dirty = true
		end
	elseif OV.Lookup.nresults > 0 then
		OV.Lookup.Clear()                    -- roster gone stale or addon off: report nobody
		OV.dirty = true
	end
end

-- ------------------------------------------------------------------ bridge

function OV.BuildState(now)
	if OV.Diag.demoUntil > now then
		local d = OV.Diag.DemoState()
		d.counter = OV.counter
		return d
	end
	local F = OV.Frame
	local optical, file = OV.Bridge.Active()
	local fresh = OV.Inbox.Fresh(now)
	local flags = 0
	if OV.db.enabled then flags = flags + F.FLAG_ENABLED end
	if file then flags = flags + F.FLAG_FILE end
	if optical then flags = flags + F.FLAG_OPTICAL end
	if fresh then flags = flags + F.FLAG_ROSTER end
	if OV.Lookup.Available() then flags = flags + F.FLAG_LOOKUPS end
	if OV.Inbox.idMismatch then flags = flags + F.FLAG_ID_MISMATCH end
	local st = OV.Lookup.stats
	local results = {}
	if OV.db.enabled and fresh then results = OV.Lookup.results end
	return {
		counter = OV.counter, flags = flags, loginId = OV.loginId, wall = time and time() or 0,
		ack = OV.Inbox.seq or 0, rosterSize = OV.Lookup.n,
		checked = st.lastChecked, loaded = st.lastLoaded, misses = st.lastMissed,
		intervalMs = math.floor(st.avgInterval * 1000), cost = math.floor(st.lastCost * 10000),
		character = OV.character, realm = OV.realm, results = results,
	}
end

local function publish(st)
	local F = OV.Frame
	OV.counter = OV.mod(OV.counter + 1, 65536)
	st.counter = OV.counter
	local full, why = F.Encode(st)
	if not full then
		OV.Log("frame encode failed: " .. tostring(why))
		return false
	end
	local compact = full
	if string.len(full) > F.OPTICAL_MAX then compact = F.Encode(st, F.OPTICAL_MAX) or full end
	OV.lastBytes = full
	local ok = OV.Bridge.Publish(full, compact)
	-- narrate what went out: when the results changed, every 10 s as a heartbeat, and always on a write error
	local parts = {}
	for i = 1, math.min(table.getn(st.results), 4) do
		parts[i] = string.format("%s %.1f yd", st.results[i].name, st.results[i].dist)
	end
	local summary = table.concat(parts, ", ")
	local now = OV.Now()
	local errNow = OV.Bridge.lastFileError ~= OV.lastTracedFileError
	if summary ~= OV.lastTracedSummary or errNow or now - (OV.lastTracedPub or -100) >= 10 then
		OV.lastTracedSummary, OV.lastTracedPub, OV.lastTracedFileError = summary, now, OV.Bridge.lastFileError
		OV.Trace("outbound: frame #" .. OV.counter .. (ok and " written" or " NOT written") ..
			" (" .. string.len(full) .. " bytes, file " .. (OV.Bridge.file and (OV.Bridge.FileApi() or "no api") or "off") ..
			", optical " .. (OV.Bridge.optical and "on" or "off") .. ")" ..
			(OV.Bridge.lastFileError and (" last file error: " .. OV.Bridge.lastFileError) or "") ..
			", " .. table.getn(st.results) .. " result(s)" .. (summary ~= "" and (": " .. summary) or ""))
	end
	return ok
end

function OV.PublishBridge(now, force)
	local due = force or (OV.dirty and now - OV.lastPub >= PUBLISH_MIN) or (now - OV.lastPub >= PUBLISH_IDLE)
	if not due then return false end
	OV.lastPub, OV.dirty = now, false
	return publish(OV.BuildState(now))
end

-- Last frame before the game closes: tells the client to drop this session at once.
function OV.PublishLogout(now)
	local st = OV.BuildState(now)
	st.flags = st.flags + OV.Frame.FLAG_LOGOUT
	st.results = {}
	publish(st)
end

-- -------------------------------------------------------------------- tick

function OV.Tick(now)
	if not OV.started then return end
	if OV.nearby then
		local gone, n = OV.nearby:Expire(now)
		for i = 1, n do
			OV.Log("group heartbeat - " .. gone[i].sid .. " " .. tostring(gone[i].sender) .. " (timeout)")
		end
	end
	OV.Comm.Tick(now)
	OV.Diag.Tick(now)
	OV.Calibrate.Tick(now)
	if now - OV.lastInbox >= INBOX_POLL then
		OV.lastInbox = now
		if OV.Inbox.Poll(now) then OV.ApplyInbox(now) end
	end
	OV.Status.Poll(now)
	OV.Panel.Tick(now)
	OV.Icons.Tick(now)
	runLookups(now)
	OV.PublishBridge(now, false)
end

-- ------------------------------------------------------------------ events

function OV.OnEvent(event, a1, a2, a3, a4)
	local now = OV.Now()
	if event == "VARIABLES_LOADED" then
		OV.InitDB()
	elseif event == "PLAYER_ENTERING_WORLD" then
		if not OV.db then OV.InitDB() end
		if not OV.started then
			OV.Start()
			OV.Log("v" .. OV.VERSION .. " loaded as " .. tostring(OV.character))        -- silent: nothing is printed at login
		end
		OV.locAt = -100
	elseif event == "CHAT_MSG_ADDON" then
		if not OV.started then return end
		OV.Comm.OnAddon(a1, a2, a3, a4, now)
		OV.Diag.OnAddon(a1, a2, a3, a4, now)
	elseif event == "PLAYER_LOGOUT" then
		if OV.started then
			OV.Comm.SendBye(now)
			OV.Trace("logout: sending the final frame so the desktop client leaves the roster")
			OV.PublishLogout(now)
			OV.SaveLog()
		end
	elseif event == "CHAT_MSG_SAY" then
		if OV.started then OV.Calibrate.OnSay(a1, a2, a3, now) end
	elseif event == "ZONE_CHANGED_NEW_AREA" then
		OV.locAt = -100
	end
end

function OV.SaveLog()
	if not OV.db then return end
	OV.Calibrate.Save()
	local lines, n = OV.LogTail(120)
	OV.db.log = lines
	local ls = OV.Lookup.stats
	OV.db.lastStats = {
		frames = OV.Bridge.stats.frames, fileWrites = OV.Bridge.stats.fileWrites,
		roster = OV.Lookup.n, scans = ls.scans, checked = ls.checked, loaded = ls.loaded, missed = ls.missed,
		inboxAccepted = OV.Inbox.stats.accepted, inboxBad = OV.Inbox.stats.bad,
		sent = OV.Comm.stats.sent, recv = OV.Comm.stats.recv, errors = OV.Comm.stats.errors,
	}
end

-- ---------------------------------------------------------------- commands

local function status()
	local now = OV.Now()
	local I, L = OV.Inbox, OV.Lookup
	local optical, file = OV.Bridge.Active()
	OV.Print("v" .. OV.VERSION .. "  " .. (OV.db.enabled and "ON" or "OFF") .. "  " .. tostring(OV.character) .. "-" ..
		tostring(OV.realm) .. "  login id " .. OV.loginId)
	local age = I.seq and string.format("%.0fs ago", now - I.changedAt) or "never"
	OV.Print("inbox: " .. (I.present and "file found" or "NO FILE (is the EqVoice client running?)") .. "  seq " .. tostring(I.seq) ..
		" (" .. age .. ")  names " .. I.count .. "  accepted " .. I.stats.accepted .. "  bad " .. I.stats.bad ..
		"  " .. (I.Fresh(now) and "FRESH" or "stale") .. (I.idMismatch and ("  IGNORED: " .. tostring(I.lastReason)) or ""))
	local st = L.stats
	OV.Print("lookups: " .. (L.Available() and "available" or "UNAVAILABLE (needs ClassicAPI)") .. "  roster " .. L.n ..
		"  last scan checked " .. st.lastChecked .. ", loaded " .. st.lastLoaded .. ", not loaded " .. st.lastMissed ..
		"  every " .. string.format("%.0f", st.avgInterval * 1000) .. " ms  total scans " .. st.scans .. " errors " .. st.errors)
	local near = {}
	for i = 1, math.min(L.nresults, 5) do
		near[i] = L.results[i].name .. " " .. string.format("%.1f", L.results[i].dist)
	end
	OV.Print("nearby (loaded, in report radius): " .. L.nresults .. (L.nresults > 0 and ("  " .. table.concat(near, ", ")) or ""))
	OV.Print("inbox source: " .. tostring(I.source) .. "   outbound files: eqvoice.txt" ..
		(OV.FileSafeName() and (" + eqvoice-" .. OV.FileSafeName() .. ".txt") or "") .. "   chat trace " .. (OV.db.chatTrace == true and "on" or "off"))
	OV.Print("bridge file " .. (file and ("on via " .. OV.Bridge.FileApi()) or (OV.db.file and "wanted, no file API" or "off")) ..
		"  optical " .. (optical and "on" or "off") .. " (" .. OV.db.corner .. ", cell " .. OV.db.cell .. ")" ..
		"  frames " .. OV.Bridge.stats.frames .. "  file writes " .. OV.Bridge.stats.fileWrites)
	if OV.db.transport ~= "OFF" then
		local chans, n = OV.Comm.CurrentChannels()
		OV.Print("group harness " .. OV.db.transport .. ": " .. (n > 0 and table.concat(chans, ",") or "no channel") ..
			"  heard " .. OV.Comm.stats.recv .. " sent " .. OV.Comm.stats.sent .. " errors " .. OV.Comm.stats.errors)
	end
	if OV.Comm.lastError then OV.Print("last send error: " .. OV.Comm.lastError) end
end

local function listNearby()
	local now = OV.Now()
	local L = OV.Lookup
	OV.Print("nearby by name lookup: " .. L.nresults)
	for i = 1, L.nresults do
		OV.Print(string.format("  %s  %.1f yd", L.results[i].name, L.results[i].dist))
	end
	if OV.nearby and OV.nearby.count > 0 then
		local list, n = OV.nearby:List(now, 40)
		OV.Print("group harness heard: " .. n)
		for i = 1, n do
			local e = list[i]
			OV.Print(string.format("  %s  %s  via %s  last %.1fs ago", e.sid, tostring(e.sender), tostring(e.dist), now - e.last))
		end
	end
end

local function help()
	OV.Print("/ev status, nearby, log [n], on, off")
	OV.Print("/ev bridge optical, file, both or off      /ev bridge corner <TOPLEFT..>   /ev bridge size <3-16>")
	OV.Print("/ev trace on or off (narrate every boundary in the chat; off by default)     /ev debug on or off     /ev reset")
	OV.Print("/ev say with <Name>, start (or listen, for a character that cannot /say), stop, report   measure how far /say carries (see Calibrate.lua)")
	OV.Print("/ev diag api, nearby, name, inbox, lookupcost, all, say, yell, group, beacon, listen, rate, bridge, file, report")
	OV.Print("/ev transport off, auto, group, say or yell   (group heartbeats: a test harness, off by default)")
end

function OV.Slash(msg)
	local now = OV.Now()
	local rawWords = OV.split(msg or "", " ")      -- original case, for character names
	msg = string.lower(msg or "")
	local w, n = OV.split(msg, " ")
	if not OV.started or not OV.db then
		OV.Print("not ready yet (log in to the world first)")
		return
	end
	local cmd = w[1] or ""
	if cmd == "" or cmd == "help" then
		help()
	elseif cmd == "status" then
		status()
	elseif cmd == "nearby" then
		listNearby()
	elseif cmd == "log" then
		local lines, k = OV.LogTail(tonumber(w[2]) or 15)
		for i = 1, k do OV.Print(lines[i]) end
	elseif cmd == "on" then
		OV.db.enabled = true
		OV.Comm.nextHb = now
		OV.dirty = true
		OV.Print("on")
	elseif cmd == "off" then
		OV.Comm.SendBye(now)
		OV.db.enabled = false
		OV.nearby:Clear()
		OV.Lookup.Clear()
		OV.PublishBridge(now, true)
		OV.Print("off")
	elseif cmd == "transport" then
		local m = string.upper(w[2] or "")
		if m == "OFF" or m == "AUTO" or m == "SAY" or m == "YELL" or m == "GROUP" then
			OV.db.transport = m
			OV.Comm.nextHb = now
			OV.Print("group heartbeat transport " .. m)
		else
			OV.Print("off, auto, group, say or yell")
		end
	elseif cmd == "radius" then
		local r = tonumber(w[2])
		if r and r >= 10 and r <= 10000 then
			OV.db.radius = r
			OV.nearby.radius = r
			OV.Print("group-harness radius " .. r .. " map units (10000 = whole zone map)")
		else
			OV.Print("group-harness radius " .. OV.db.radius .. "; usage /ev radius 10..10000")
		end
	elseif cmd == "bridge" then
		local what = w[2] or ""
		if what == "optical" or what == "both" or what == "file" or what == "off" then OV.db.opticalChosen = true end
		if what == "optical" then OV.db.optical, OV.db.file = true, false
		elseif what == "file" then OV.db.optical, OV.db.file = false, true
		elseif what == "both" then OV.db.optical, OV.db.file = true, true
		elseif what == "off" then OV.db.optical, OV.db.file = false, false
		elseif what == "corner" then
			if OV.Bridge.SetCorner(w[3]) then OV.db.corner = OV.Bridge.corner else OV.Print("TOPLEFT TOPRIGHT BOTTOMLEFT BOTTOMRIGHT") end
		elseif what == "size" then
			OV.db.cell = OV.Bridge.SetCellSize(w[3])
		else
			OV.Print("optical, file, both, off, corner <C>, size <3-16>")
		end
		OV.Bridge.optical, OV.Bridge.file = OV.db.optical, OV.db.file
		OV.PublishBridge(now, true)
		status()
	elseif cmd == "trace" then
		OV.db.chatTrace = (w[2] == "on") or nil
		OV.Print("trace " .. (OV.db.chatTrace and "on: every boundary is narrated here and in the bridge file" or "off (the bridge file still carries it)"))
	elseif cmd == "debug" then
		OV.db.debug = (w[2] == "on")
		OV.debugOn = OV.db.debug
		OV.Print("debug " .. (OV.debugOn and "on" or "off"))
	elseif cmd == "reset" then
		OV.nearby:Clear()
		OV.Lookup.Clear()
		OV.Comm.stats = { sent = 0, errors = 0, throttled = 0, recv = 0, malformed = 0, reasons = {} }
		OV.Print("cleared results and counters")
	elseif cmd == "say" then
		OV.Calibrate.Handle(w, n, now, rawWords)
	elseif cmd == "diag" then
		OV.Diag.Handle(w, n, now, rawWords)
	else
		help()
	end
end

-- ------------------------------------------------------------------ frames

local function install()
	if not CreateFrame then return end
	local f = CreateFrame("Frame", "EqVoiceEventFrame")
	f:RegisterEvent("VARIABLES_LOADED")
	f:RegisterEvent("PLAYER_ENTERING_WORLD")
	f:RegisterEvent("PLAYER_LOGOUT")
	f:RegisterEvent("CHAT_MSG_ADDON")
	f:RegisterEvent("CHAT_MSG_SAY")
	f:RegisterEvent("ZONE_CHANGED_NEW_AREA")
	f:SetScript("OnEvent", function()
		OV.OnEvent(event, arg1, arg2, arg3, arg4)
	end)
	local acc = 0
	f:SetScript("OnUpdate", function()
		acc = acc + arg1
		if acc >= TICK then
			acc = 0
			OV.Tick(OV.Now())
		end
	end)
	OV.eventFrame = f
	SLASH_EQVOICE1 = "/ev"
	SLASH_EQVOICE2 = "/eqvdiag"
	SlashCmdList["EQVOICE"] = function(msg) OV.Slash(msg) end
	SLASH_EQVOICEUI1 = "/eqvc"
	SLASH_EQVOICEUI2 = "/eqvoice"
	SlashCmdList["EQVOICEUI"] = function(msg) OV.Panel.Slash(msg) end
end

install()
