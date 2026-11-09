-- EqVoice / Comm.lua
-- Sending and receiving presence heartbeats, and the rate limiting that keeps
-- the addon a good citizen on a server whose addon-message limits are unknown.
--
-- Budget (defaults): one heartbeat per 4 s, growing to one per 12 s in a crowd;
-- a token bucket of 3 messages refilling 1 per 2 s; a hard ceiling of 30 per
-- minute; at most one heartbeat ever waiting. That is about 0.25 msg/s.

local OV = EqVoice
local Comm = {}
OV.Comm = Comm

Comm.BASE_IV     = 4
Comm.MAX_IV      = 12
Comm.BUCKET_CAP  = 3
Comm.BUCKET_RATE = 0.5       -- tokens per second
Comm.PER_MINUTE  = 30

Comm.seq      = 0
Comm.nextHb   = 0
Comm.lastSend = -100
Comm.lastIv   = 4
Comm.stats    = { sent = 0, errors = 0, throttled = 0, recv = 0, malformed = 0, reasons = {} }
Comm.lastError = nil
Comm.lastFlags = nil

-- ------------------------------------------------------------ rate limiting

function Comm.NewBucket(cap, rate, now)
	-- hist is a fixed ring of send times (oldest at `head`, `nh` of them), written by
	-- plain index only: no table.remove / table.getn on a structure that changes size.
	return { cap = cap, rate = rate, tokens = cap, t = now, hist = {}, head = 1, nh = 0 }
end

-- Takes one token if allowed. `perMinute` is the sliding-window ceiling.
function Comm.Take(b, now, perMinute)
	local dt = now - b.t
	if dt > 0 then
		b.tokens = math.min(b.cap, b.tokens + dt * b.rate)
		b.t = now
	end
	while b.nh > 0 do
		local oldest = b.hist[b.head]
		if oldest and now - oldest <= 60 then break end
		b.head = OV.mod(b.head, perMinute) + 1
		b.nh = b.nh - 1
	end
	if b.tokens < 1 or b.nh >= perMinute then return false end
	b.tokens = b.tokens - 1
	b.hist[OV.mod(b.head - 1 + b.nh, perMinute) + 1] = now
	b.nh = b.nh + 1
	return true
end

-- Heartbeat interval grows with the number of people nearby, so a crowded
-- city does not turn into a broadcast storm.
function Comm.Interval(nearbyCount)
	return OV.clamp(Comm.BASE_IV + math.floor(nearbyCount / 5), Comm.BASE_IV, Comm.MAX_IV)
end

-- What the heartbeat says about this player's voice: enabled, plus muted / deafened / app running when the desktop app is there
-- (the panel's own pending clicks count too, so a mute is announced as soon as it is clicked).
function Comm.Flags(now)
	local P = OV.Proto
	local f = P.FLAG_ENABLED
	local S = OV.Status
	if S and S.Alive and S.Alive(now) then
		f = f + P.FLAG_APP
		local m = OV.Panel and OV.Panel.Model and OV.Panel.Model(now) or nil
		local muted, deaf = S.fields.mute == "1", S.fields.deaf == "1"
		if m then muted, deaf = m.mute, m.deaf end
		if deaf then f = f + P.FLAG_DEAF end
		if muted then f = f + P.FLAG_MUTED end
	end
	return f
end

-- ------------------------------------------------------------- channel pick
-- mode: AUTO | SAY | YELL | GROUP. env: { guild, raid, party } booleans.
-- diag: the saved diagnostics table; AUTO uses SAY only after the loopback test
-- proved it, otherwise falls back to group channels.

function Comm.Channels(mode, env, diag)
	local out, n = {}, 0
	local function add(c)
		n = n + 1
		out[n] = c
	end
	local function group()
		if env.guild then add("GUILD") end
		if env.raid then add("RAID") elseif env.party then add("PARTY") end
	end
	mode = string.upper(mode or "AUTO")
	if mode == "OFF" then return out, 0 end
	if mode == "SAY" then add("SAY")
	elseif mode == "YELL" then add("YELL")
	elseif mode == "GROUP" then group()
	else
		if diag and diag.say and diag.say.ok then add("SAY") else group() end
	end
	return out, n
end

function Comm.IsLocalChannel(c)
	return c == "SAY" or c == "YELL"
end

-- ------------------------------------------------------------------ sending

function Comm.SendRaw(prefix, text, chan)
	if not SendAddonMessage then
		Comm.lastError = "SendAddonMessage missing"
		return false
	end
	local ok, err = pcall(SendAddonMessage, prefix, text, chan)
	if not ok then
		Comm.stats.errors = Comm.stats.errors + 1
		Comm.lastError = chan .. ": " .. tostring(err)
		OV.Log("send error " .. Comm.lastError)
		return false
	end
	return true
end

local function envNow()
	return {
		guild = (IsInGuild and IsInGuild()) and true or false,
		raid  = (GetNumRaidMembers and GetNumRaidMembers() > 0) and true or false,
		party = (GetNumPartyMembers and GetNumPartyMembers() > 0) and true or false,
	}
end

function Comm.CurrentChannels()
	return Comm.Channels(OV.db.transport, envNow(), OV.db.diag)
end

local function newSeq()
	Comm.seq = OV.mod(Comm.seq + 1, 65536)
	return Comm.seq
end

-- Sends one heartbeat on every channel the current mode selects. Each channel
-- costs one token. Returns the number of messages actually sent.
function Comm.SendHeartbeat(now)
	if not OV.sid or not OV.db.enabled then return 0 end
	local chans, n = Comm.CurrentChannels()
	local sent = 0
	local iv = Comm.Interval(OV.nearby and OV.nearby.count or 0)
	Comm.lastIv = iv
	for i = 1, n do
		if Comm.Take(OV.bucket, now, Comm.PER_MINUTE) then
			local loc = nil
			if not Comm.IsLocalChannel(chans[i]) then loc = OV.GetLoc() end
			local text = OV.Proto.EncodeHeartbeat(OV.sid, newSeq(), iv, Comm.Flags(now), loc)
			if Comm.SendRaw(OV.PREFIX, text, chans[i]) then
				sent = sent + 1
				Comm.stats.sent = Comm.stats.sent + 1
			end
		else
			Comm.stats.throttled = Comm.stats.throttled + 1
		end
	end
	if sent > 0 then Comm.lastSend = now end
	return sent
end

function Comm.SendBye(now)
	if not OV.sid then return end
	local chans, n = Comm.CurrentChannels()
	for i = 1, n do
		-- A goodbye is allowed to overdraw the bucket by one message.
		Comm.SendRaw(OV.PREFIX, OV.Proto.EncodeBye(OV.sid, newSeq()), chans[i])
	end
end

function Comm.Tick(now)
	if not OV.sid or not OV.db.enabled or OV.db.transport == "OFF" then return end
	local flags = Comm.Flags(now)
	if flags ~= Comm.lastFlags then
		-- muted, deafened or the app came or went: say so soon, not after a whole interval
		if Comm.lastFlags ~= nil and now + 0.6 < Comm.nextHb then Comm.nextHb = now + 0.3 + math.random() * 0.3 end
		Comm.lastFlags = flags
	end
	if now < Comm.nextHb then return end
	local errorsBefore = Comm.stats.errors
	local sent = Comm.SendHeartbeat(now)
	local iv = Comm.lastIv
	if sent == 0 then
		if Comm.stats.errors > errorsBefore then
			Comm.nextHb = now + 10       -- the client refused the channel: do not hammer it
		else
			Comm.nextHb = now + 1        -- no channel / throttled: retry soon
		end
	else
		Comm.nextHb = now + iv + math.random() * 0.5
	end
end

-- A new neighbour should learn about us quickly, not after a full interval.
function Comm.OnNewPeer(now)
	if now - Comm.lastSend >= 2 then
		local soon = now + 0.3 + math.random() * 1.2
		if soon < Comm.nextHb then Comm.nextHb = soon end
	end
end

-- ---------------------------------------------------------------- receiving

function Comm.OnAddon(prefix, text, dist, sender, now)
	if prefix ~= OV.PREFIX or not OV.nearby then return nil end
	Comm.stats.recv = Comm.stats.recv + 1
	local m, why = OV.Proto.Decode(text)
	if not m then
		Comm.stats.malformed = Comm.stats.malformed + 1
		Comm.stats.reasons[why] = (Comm.stats.reasons[why] or 0) + 1
		return nil
	end
	local res = OV.nearby:Hear(m, sender, dist, now, OV.GetLoc())
	if res == "new" then
		OV.Log("nearby + " .. m.sid .. " " .. tostring(sender) .. " via " .. tostring(dist))
		Comm.OnNewPeer(now)
	elseif res == "bye" or res == "left" then
		OV.Log("nearby - " .. m.sid .. " " .. tostring(sender) .. " (" .. res .. ")")
	elseif not OV.Nearby.IsAccepted(res) and res ~= "self" then
		OV.Log("ignored " .. tostring(sender) .. " " .. res)
	end
	return res
end
