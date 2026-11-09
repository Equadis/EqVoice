-- EqVoice / Calibrate.lua
-- How far does ordinary /say text carry on this server? EqVoice's default voice radius started as an assumption (40 yd); measured here, it is 41.
-- This measures it with two characters, one of whom walks away from the other:
--
--   both:   /ev say with <the other character's exact name>
--   both:   /ev say start            (optionally: /ev say start 120   stops itself after 120 s)
--   a character that cannot /say (muted, no permission): /ev say listen   instead of start - it only listens, and the OTHER
--           character speaks. Never "start" on a character that cannot speak: its lines would count as not heard at any distance.
--   walk apart slowly to about 80 yards, and back; do it twice. /ev say stop on both.
--   either: /ev say report           what this side saw
--   PC:     python tools/say_calibrate.py     joins both sides' logs and fits the cutoff
--
-- While it runs, every `interval` seconds this character /says "ovcal <n> <interval ms>" and notes the exact distance to
-- the partner (UnitTokenFromName + UnitDistanceSquared, the same lookup the voice pipeline uses: it works far beyond /say
-- range, so the distance is known even when the text is not delivered). When the partner's message arrives
-- (CHAT_MSG_SAY) the distance at that moment is noted too. A gap in the numbers is a message that did not arrive: its
-- distance is taken from the samples around the time it was due. Everything is also narrated as "saycal ..." trace lines,
-- which travel through the bridge file into the desktop client's log, where tools/say_calibrate.py reads them.
--
-- The text is "ovcal ..." on purpose: it is visible to everyone in range, it is only sent while the mode is on, and nothing
-- else in EqVoice ever speaks in a public channel.

local OV = EqVoice
local Cal = {}
OV.Calibrate = Cal

Cal.on        = false
Cal.listenOnly = false       -- true: never speak, only record what the partner's /say does
Cal.show      = false       -- true: also print each line (with its distance) in the chat window, to watch the walk live
Cal.partner   = nil
Cal.seq       = 0
Cal.interval  = 2.0
Cal.PROBE     = 0.5          -- seconds between distance samples
Cal.nextSend  = 0
Cal.nextProbe = 0
Cal.untilT    = nil
Cal.dist      = nil          -- last good distance to the partner
Cal.hist      = {}           -- ring of { t = time, d = yards or nil }
Cal.histN     = 0
Cal.lastHeard = nil          -- { seq, t }
Cal.sentLog   = {}           -- { seq, dist } for what this side said
Cal.heardLog  = {}           -- { seq, dist } for what arrived from the partner
Cal.missLog   = {}           -- { seq, dist } for numbers that were skipped
Cal.stats     = { sent = 0, heard = 0, missed = 0, garbled = 0, failed = 0 }

local HIST_MAX = 720
local KEEP     = 400
local BUCKET   = 5

local function fmt(d)
	if d then return string.format("%.1f yd", d) end
	return "? yd"
end

local function push(list, entry)
	list[table.getn(list) + 1] = entry
	if table.getn(list) > KEEP then table.remove(list, 1) end
end

-- ------------------------------------------------------------------ distance

local function sample(now)
	local d = nil
	if Cal.partner and OV.Lookup and OV.Lookup.CheckOne then d = OV.Lookup.CheckOne(Cal.partner) end
	Cal.dist = d
	Cal.histN = Cal.histN + 1
	Cal.hist[OV.mod(Cal.histN - 1, HIST_MAX) + 1] = { t = now, d = d }
	return d
end

-- The distance at time t from the samples around it (nearest good one within 1.5 s), or nil.
local function distAt(t)
	local best, bestGap = nil, 1.5
	local first = Cal.histN - HIST_MAX + 1
	if first < 1 then first = 1 end
	for i = Cal.histN, first, -1 do
		local e = Cal.hist[OV.mod(i - 1, HIST_MAX) + 1]
		if e and e.d then
			local gap = e.t - t
			if gap < 0 then gap = -gap end
			if gap < bestGap then best, bestGap = e.d, gap end
		end
		if e and e.t < t - 3 then break end
	end
	return best
end
Cal.DistAt = distAt

-- ------------------------------------------------------------------- control

function Cal.Start(partner, now, seconds, listenOnly)
	if not partner or partner == "" then OV.Print("say: choose the other character first: /ev say with <exact name>") return false end
	if not OV.Lookup.Available() then OV.Print("say: needs the ClassicAPI name lookup (UnitTokenFromName); not available on this client") return false end
	Cal.partner, Cal.on, Cal.listenOnly = partner, true, listenOnly and true or false
	Cal.seq, Cal.lastHeard = 0, nil
	Cal.sentLog, Cal.heardLog, Cal.missLog = {}, {}, {}
	Cal.stats = { sent = 0, heard = 0, missed = 0, garbled = 0, failed = 0 }
	Cal.nextProbe, Cal.nextSend = now, now + 1
	Cal.untilT = seconds and (now + seconds) or nil
	if Cal.listenOnly then
		OV.Trace("saycal start with " .. partner .. ", listening only (this character does not speak)", not Cal.show)
		OV.Print("say calibration LISTENING with " .. partner .. ": this character says nothing; the other one must /ev say start. /ev say stop when done.")
	else
		OV.Trace(string.format("saycal start with %s, one /say every %.1f s", partner, Cal.interval), not Cal.show)
		OV.Print("say calibration ON with " .. partner .. ": " .. "this character /says 'ovcal <n>' every " .. Cal.interval ..
			" s. Walk apart slowly (to ~80 yd) and back. /ev say stop when done.")
	end
	return true
end

function Cal.Stop()
	if not Cal.on then OV.Print("say calibration is not running") return end
	Cal.on = false
	OV.Trace(string.format("saycal stop: sent %d, heard %d, skipped %d", Cal.stats.sent, Cal.stats.heard, Cal.stats.missed), not Cal.show)
	Cal.Save()
	OV.Print("say calibration stopped.")
	Cal.Report()
end

function Cal.Tick(now)
	if not Cal.on then return end
	if now >= Cal.nextProbe then
		Cal.nextProbe = now + Cal.PROBE
		sample(now)
	end
	if not Cal.listenOnly and now >= Cal.nextSend then
		Cal.nextSend = now + Cal.interval
		Cal.seq = Cal.seq + 1
		local ok, err = pcall(SendChatMessage, "ovcal " .. Cal.seq .. " " .. math.floor(Cal.interval * 1000), "SAY")
		if ok then
			Cal.stats.sent = Cal.stats.sent + 1
			push(Cal.sentLog, { seq = Cal.seq, dist = Cal.dist })
			OV.Trace("saycal sent " .. Cal.seq .. " to " .. Cal.partner .. " at " .. fmt(Cal.dist), not Cal.show)
		else
			Cal.stats.failed = Cal.stats.failed + 1
			OV.Trace("saycal send FAILED: " .. tostring(err), not Cal.show)
		end
	end
	if Cal.untilT and now >= Cal.untilT then Cal.Stop() end
end

-- The partner's /say arrived (CHAT_MSG_SAY: text, sender, language).
function Cal.OnSay(text, sender, language, now)
	if not Cal.on or sender ~= Cal.partner then return end
	Cal.stats.heard = Cal.stats.heard + 1
	local d = sample(now)
	local _, _, seqs, ms = string.find(tostring(text or ""), "^ovcal (%d+) (%d+)$")
	local seq = seqs and tonumber(seqs) or nil
	if not seq then
		-- the other faction's /say comes through in their language: the event still proves it was delivered, but without its number
		Cal.stats.garbled = Cal.stats.garbled + 1
		push(Cal.heardLog, { seq = nil, dist = d })
		OV.Trace("saycal heard ? from " .. sender .. " at " .. fmt(d) .. " (text not recognised, language " .. tostring(language) .. ")", not Cal.show)
		return
	end
	local last = Cal.lastHeard
	if last and seq > last.seq + 1 and seq - last.seq <= 60 then
		for k = last.seq + 1, seq - 1 do
			local tk = last.t + (k - last.seq) * (now - last.t) / (seq - last.seq)
			local dk = distAt(tk)
			Cal.stats.missed = Cal.stats.missed + 1
			push(Cal.missLog, { seq = k, dist = dk })
			OV.Trace("saycal skipped " .. k .. " from " .. sender .. " at " .. fmt(dk), not Cal.show)
		end
	end
	Cal.lastHeard = { seq = seq, t = now }
	push(Cal.heardLog, { seq = seq, dist = d })
	OV.Trace("saycal heard " .. seq .. " from " .. sender .. " at " .. fmt(d), not Cal.show)
end

-- ------------------------------------------------------------------ reporting

-- { bucketStart = { heard = n, missed = n } } over the 5-yard buckets seen, and the list of starts in order.
function Cal.Buckets()
	local b, starts = {}, {}
	local function add(list, key)
		for i = 1, table.getn(list) do
			local d = list[i].dist
			if d then
				local s = math.floor(d / BUCKET) * BUCKET
				if not b[s] then b[s] = { heard = 0, missed = 0 }; starts[table.getn(starts) + 1] = s end
				b[s][key] = b[s][key] + 1
			end
		end
	end
	add(Cal.heardLog, "heard")
	add(Cal.missLog, "missed")
	table.sort(starts)
	return b, starts
end

function Cal.Report()
	local far, near = nil, nil
	for i = 1, table.getn(Cal.heardLog) do
		local d = Cal.heardLog[i].dist
		if d and (not far or d > far) then far = d end
	end
	for i = 1, table.getn(Cal.missLog) do
		local d = Cal.missLog[i].dist
		if d and (not near or d < near) then near = d end
	end
	OV.Print(string.format("say calibration with %s%s: sent %d, heard %d, skipped %d (a gap in the numbers)%s", tostring(Cal.partner),
		Cal.listenOnly and " (listening only)" or "", Cal.stats.sent, Cal.stats.heard, Cal.stats.missed, Cal.stats.garbled > 0 and (", " .. Cal.stats.garbled .. " heard without a number") or ""))
	OV.Print("  farthest heard: " .. fmt(far) .. "    nearest skipped: " .. fmt(near) ..
		"    (this side only; python tools/say_calibrate.py joins both sides)")
	local b, starts = Cal.Buckets()
	for i = 1, table.getn(starts) do
		local s = starts[i]
		local e = b[s]
		local tot = e.heard + e.missed
		OV.Print(string.format("  %3d-%3d yd: heard %d of %d (%d%%)", s, s + BUCKET, e.heard, tot, math.floor(100 * e.heard / tot + 0.5)))
	end
end

-- Keep what was measured in the saved variables (written at logout or /reload), as compact "seq:yards" lists.
local function pack(list)
	local out = {}
	for i = 1, table.getn(list) do
		out[i] = tostring(list[i].seq or "?") .. ":" .. (list[i].dist and string.format("%.1f", list[i].dist) or "?")
	end
	return table.concat(out, ",")
end

function Cal.Save()
	if not OV.db then return end
	if table.getn(Cal.sentLog) + table.getn(Cal.heardLog) + table.getn(Cal.missLog) == 0 then return end
	OV.db.saycal = { character = OV.character, partner = Cal.partner, at = time and time() or 0, interval = Cal.interval,
		sent = pack(Cal.sentLog), heard = pack(Cal.heardLog), skipped = pack(Cal.missLog) }
end

-- /ev say ...
function Cal.Handle(w, n, now, raw)
	local sub = w[2] or ""
	if sub == "with" then
		local name = raw and raw[3]
		if not name or name == "" then OV.Print("usage: /ev say with <exact character name>") return end
		Cal.partner = name
		OV.Print("say calibration partner: " .. name .. ". Now /ev say start (on both characters).")
	elseif sub == "start" then
		Cal.Start(Cal.partner, now, tonumber(w[3]))
	elseif sub == "show" then
		Cal.show = not Cal.show
		OV.Print("say calibration lines in the chat window: " .. (Cal.show and "ON (each line shows the distance; /ev say show again to stop)" or "off"))
	elseif sub == "listen" then
		Cal.Start(Cal.partner, now, tonumber(w[3]), true)
	elseif sub == "stop" then
		Cal.Stop()
	elseif sub == "interval" then
		local v = tonumber(w[3])
		if v and v >= 2 and v <= 10 then Cal.interval = v; OV.Print("say interval " .. v .. " s")
		else OV.Print("usage: /ev say interval 2..10   (seconds between /says, now " .. Cal.interval .. "; faster can trip the server's chat flood limit, which silently drops lines)") end
	elseif sub == "report" then
		Cal.Report()
	else
		OV.Print("/ev say with <Name>, start [seconds], listen [seconds] (cannot /say), stop, show (watch it live), report, interval <s>   - measures how far /say carries (see Calibrate.lua)")
		OV.Print("now: " .. (Cal.on and ("ON with " .. tostring(Cal.partner)) or "off") .. ", partner " .. tostring(Cal.partner))
	end
end
