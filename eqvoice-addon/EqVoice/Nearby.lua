-- EqVoice / Nearby.lua
-- The set of EqVoice users currently considered nearby, with timeout expiry
-- and the abuse checks that can be done inside the addon. Pure logic: time is
-- passed in, nothing here touches the WoW API, so the whole file is unit tested.
--
-- The sender name on a CHAT_MSG_ADDON event is supplied by the server, so a
-- client cannot claim to be someone else. A client *can* claim any sid, which is
-- why a sid is tied to the first sender that used it and contested ids are
-- excluded until the claim lapses.

local OV = EqVoice
local Nearby = {}
OV.Nearby = Nearby

Nearby.__index = Nearby

local FLOOD_WINDOW = 12     -- seconds
local FLOOD_MAX    = 8      -- heartbeats accepted from one sender per window
local CONFLICT_SEC = 60

-- opts: selfName, selfSid, radius (map units out of 10000), maxEntries
function Nearby.New(opts)
	local self = setmetatable({}, Nearby)
	self.selfName   = opts.selfName
	self.selfSid    = opts.selfSid
	self.radius     = opts.radius or 300
	self.maxEntries = opts.maxEntries or 40
	self.entries    = {}     -- sid -> entry
	self.bySender   = {}     -- sender name -> sid
	self.flood      = {}     -- sender name -> { t0, n }
	self.count      = 0
	self.stats      = { new = 0, refresh = 0, dropped = 0 }
	self.reasons    = {}     -- drop reason -> count
	return self
end

local function drop(self, reason)
	self.stats.dropped = self.stats.dropped + 1
	self.reasons[reason] = (self.reasons[reason] or 0) + 1
	return reason
end

local function expiryOf(e)
	return e.last + 3 * e.iv + 1
end

local function remove(self, sid)
	local e = self.entries[sid]
	if not e then return end
	if self.bySender[e.sender] == sid then self.bySender[e.sender] = nil end
	self.entries[sid] = nil
	self.count = self.count - 1
end

-- Is `loc` close enough to `myLoc` to count as "in range"? Used only for group
-- channels (guild / party / raid), where being on the channel says nothing about
-- being close. Same zone is required. Two positionless players (instances) in
-- the same named zone are treated as together.
function Nearby.NearEnough(myLoc, loc, radius)
	if not myLoc or not loc then return false end
	if myLoc.zone ~= loc.zone then return false end
	local myNone = (myLoc.x == 0 and myLoc.y == 0)
	local theirNone = (loc.x == 0 and loc.y == 0)
	if myNone and theirNone then return true end
	if myNone or theirNone then return false end
	local dx, dy = myLoc.x - loc.x, myLoc.y - loc.y
	return (dx * dx + dy * dy) <= (radius * radius)
end

-- m: decoded message (Proto.Decode). dist: the channel it arrived on.
-- Returns "new", "refresh", "bye", "left" or a drop reason string beginning
-- with a lower-case word; use Nearby.IsAccepted(result) to test.
function Nearby:Hear(m, sender, dist, now, myLoc)
	if not sender or sender == "" then return drop(self, "nosender") end
	if sender == self.selfName or m.sid == self.selfSid then return drop(self, "self") end

	local f = self.flood[sender]
	if not f or now - f.t0 > FLOOD_WINDOW then
		f = { t0 = now, n = 0 }
		self.flood[sender] = f
	end
	f.n = f.n + 1
	if f.n > FLOOD_MAX then return drop(self, "flood") end

	local e = self.entries[m.sid]

	if m.t == "B" then
		if e and e.sender == sender then
			remove(self, m.sid)
			return "bye"
		end
		return drop(self, "bye_unknown")
	end

	local isLocal = (dist == "SAY" or dist == "YELL")
	if not isLocal then
		if not m.loc then return drop(self, "noloc") end
		if not Nearby.NearEnough(myLoc, m.loc, self.radius) then
			if e and e.sender == sender then
				remove(self, m.sid)
				return "left"
			end
			return drop(self, "far")
		end
	end

	if e and e.sender ~= sender then
		-- Two senders claim one sid. Exclude it for a while rather than guess.
		e.conflictUntil = now + CONFLICT_SEC
		return drop(self, "conflict")
	end

	local other = self.bySender[sender]
	if other and other ~= m.sid then
		local oe = self.entries[other]
		if oe and now <= expiryOf(oe) then
			return drop(self, "dupe_sender")
		end
		remove(self, other)
	end

	if e then
		if not OV.Proto.SeqNewer(m.seq, e.seq) then return drop(self, "replay") end
		e.seq, e.iv, e.flags, e.loc, e.last, e.dist = m.seq, m.iv, m.flags, m.loc, now, dist
		e.heard = e.heard + 1
		self.stats.refresh = self.stats.refresh + 1
		return "refresh"
	end

	if self.count >= self.maxEntries then return drop(self, "full") end
	self.entries[m.sid] = {
		sid = m.sid, sender = sender, first = now, last = now, seq = m.seq,
		iv = m.iv, flags = m.flags, loc = m.loc, dist = dist, heard = 1,
	}
	self.bySender[sender] = m.sid
	self.count = self.count + 1
	self.stats.new = self.stats.new + 1
	return "new"
end

function Nearby.IsAccepted(result)
	return result == "new" or result == "refresh"
end

-- Remove entries whose heartbeats stopped. Returns the removed entries.
function Nearby:Expire(now)
	local gone, n = {}, 0
	for sid, e in pairs(self.entries) do
		if now > expiryOf(e) then
			n = n + 1
			gone[n] = e
		end
	end
	for i = 1, n do remove(self, gone[i].sid) end
	for sender, f in pairs(self.flood) do
		if now - f.t0 > FLOOD_WINDOW * 5 then self.flood[sender] = nil end
	end
	return gone, n
end

-- Eligible entries (not contested), oldest first so the order is stable.
function Nearby:List(now, limit)
	local out, n = {}, 0
	for sid, e in pairs(self.entries) do
		if not e.conflictUntil or now > e.conflictUntil then
			e.conflictUntil = nil
			n = n + 1
			out[n] = e
		end
	end
	table.sort(out, function(a, b)
		if a.first ~= b.first then return a.first < b.first end
		return a.sid < b.sid
	end)
	if limit and n > limit then
		for i = n, limit + 1, -1 do out[i] = nil end
		n = limit
	end
	return out, n
end

function Nearby:Clear()
	self.entries, self.bySender, self.flood, self.count = {}, {}, {}, 0
end
