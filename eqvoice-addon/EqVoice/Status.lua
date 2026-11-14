-- EqVoice / Status.lua
-- The desktop -> addon file that feeds the in-game panel: is the EqVoice app running, is voice working,
-- what is the microphone doing, who is near and talking. The app rewrites it whenever something changes and at
-- least every 2 s; the addon polls it twice a second through a mod's file API.
--
--   OVS1 <seq> <unix-ts> <crc> <character>
--   S level=ok mode=open mute=0 deaf=0 tx=1 talk=0 vol=100 near=2 conn=1 key=F9 game=1 hub=1 mic=ok app=0.2.0 boost=normal gate=auto gdb=-48
--   M Ready: Alice on Realm. 2 players in range, 1 connected.       one line, always present
--   N another thing to fix                                          up to 4 of these
--   P <name> <yards|-> <connected 0|1> <speaking 0|1> <direct|relay|-> [<state> [<muted 0|1>]]    one per player, nearest first
--   X <name> <name> ...                                             the mute list (several lines when it is long)
--
-- state is what that player's own EqVoice reports, for the icon beside their name: s speaking, o microphone open and quiet,
-- m muted (or push to talk with the key not held), d deafened, - not known. muted is 1 when YOU have muted them. An older
-- app sends neither: both are then taken as unknown.
--
-- crc is CRC-16/CCITT-FALSE of everything after the first newline (four upper-case hex digits), as in the roster
-- file. The character in the header must be the character logged in here, or the file is ignored: two game
-- windows in one folder each read their own eqvoice-st-<Character>.txt.
--
-- The app is "running" while the sequence number keeps moving. The file is also removed when the app quits.
-- eqvoice-client/eqvoice_client/bridge/uifiles.py is the other half; tests cross-check the two.

local OV = EqVoice
local Status = {}
OV.Status = Status

Status.NAME      = "eqvoice-st"      -- ".txt" is added by ReadCustomFile
Status.TTL       = 6                   -- seconds without a new sequence number = the app is gone
Status.POLL      = 0.5
Status.MAX_PEERS = 40
Status.MAX_DEVICES = 8           -- microphones and speakers listed, each
Status.devices = { ["in"] = {}, out = {} }
Status.defaults = { ["in"] = "", out = "" }     -- what the system default device is now, by name
Status.blocked = {}              -- lower-case name -> true: the players who do not hear you
Status.nblocked = 0
Status.MAX_MUTED = 60
Status.STATES    = "solmd-"
Status.MAX_MORE  = 4

Status.lastPoll   = -100
Status.lastText   = nil
Status.lastBody   = nil
Status.lastCrc    = nil
Status.lastReason = nil
Status.seq        = nil
Status.character  = nil
Status.changedAt  = -1000
Status.version    = 0       -- counts accepted files that carried news
Status.present    = false
Status.mismatch   = false
Status.fields     = {}
Status.headline   = ""
Status.more       = {}
Status.nmore      = 0
Status.peers      = {}
Status.npeers     = 0
Status.muted      = {}      -- lower-case name -> true: the mute list
Status.nmuted     = 0
Status.byName     = {}      -- lower-case name -> that player's row
Status.stats      = { reads = 0, accepted = 0, bad = 0, refreshed = 0 }

local function crcHex(body)
	return string.format("%04X", OV.Frame.CRC16(body))
end

-- Pure: text -> table, or nil plus a reason. Never raises.
function Status.Parse(text)
	if type(text) ~= "string" then return nil, "type" end
	local nl = string.find(text, "\n", 1, true)
	if not nl then return nil, "noheader" end
	local head = string.gsub(string.sub(text, 1, nl - 1), "\r", "")
	local body = string.gsub(string.sub(text, nl + 1), "\r", "")
	local p, n = OV.split(head, " ")
	if n < 5 or p[1] ~= "OVS1" then return nil, "magic" end
	local seq, ts = tonumber(p[2]), tonumber(p[3])
	if not seq or not ts then return nil, "numbers" end
	if string.upper(p[4]) ~= crcHex(body) then return nil, "crc" end

	local t = { seq = seq, ts = ts, character = p[5], fields = {}, headline = "", more = {}, nmore = 0, peers = {}, npeers = 0, muted = {}, nmuted = 0, byName = {},
		devices = { ["in"] = {}, out = {} }, defaults = { ["in"] = "", out = "" }, blocked = {}, nblocked = 0 }
	local pos, len = 1, string.len(body)
	while pos <= len do
		local e = string.find(body, "\n", pos, true)
		local line
		if e then line = string.sub(body, pos, e - 1); pos = e + 1
		else line = string.sub(body, pos); pos = len + 1 end
		local kind = string.sub(line, 1, 1)
		local rest = string.sub(line, 3)
		if kind == "S" then
			local items, ni = OV.split(rest, " ")
			for i = 1, ni do
				local eq = string.find(items[i], "=", 1, true)
				if eq then t.fields[string.sub(items[i], 1, eq - 1)] = string.sub(items[i], eq + 1) end
			end
		elseif kind == "M" then
			t.headline = rest
		elseif kind == "N" then
			if t.nmore < Status.MAX_MORE then
				t.nmore = t.nmore + 1
				t.more[t.nmore] = rest
			end
		elseif kind == "P" then
			local f, nf = OV.split(rest, " ")
			if nf >= 5 and t.npeers < Status.MAX_PEERS then
				t.npeers = t.npeers + 1
				local state = f[6]
				if not state or string.len(state) ~= 1 or not string.find(Status.STATES, state, 1, true) then state = "-" end
				local row = { name = f[1], dist = tonumber(f[2]), connected = (f[3] == "1"), speaking = (f[4] == "1"), path = f[5], state = state, muted = (f[7] == "1"),
					blocked = (f[8] == "1"), vol = tonumber(f[9] or "") or 100 }
				t.peers[t.npeers] = row
				t.byName[string.lower(f[1])] = row
			end
		elseif kind == "D" then
			-- D <in|out> <n> <name of the device>: the name may have spaces, so it is the rest of the line
			local s, e, which, n, name = string.find(rest, "^(%a+) (%d+) (.+)$")
			local list = s and t.devices[which] or nil
			if list and table.getn(list) < Status.MAX_DEVICES then list[table.getn(list) + 1] = name end
		elseif kind == "E" then
			-- E <in|out> <name>: what the system default device is now
			local s, e, which, name = string.find(rest, "^(%a+) (.+)$")
			if s and t.defaults[which] ~= nil then t.defaults[which] = name end
		elseif kind == "K" then
			local f, nf = OV.split(rest, " ")
			for i = 1, nf do
				if f[i] ~= "" and t.nblocked < Status.MAX_MUTED then
					t.nblocked = t.nblocked + 1
					t.blocked[string.lower(f[i])] = true
				end
			end
		elseif kind == "X" then
			local f, nf = OV.split(rest, " ")
			for i = 1, nf do
				if f[i] ~= "" and t.nmuted < Status.MAX_MUTED then
					t.nmuted = t.nmuted + 1
					t.muted[string.lower(f[i])] = true
				end
			end
		end
	end
	return t
end

-- This character's file wins; otherwise the plain one (a single game in the folder).
function Status.ReadFile()
	local who = OV.FileSafeName and OV.FileSafeName()
	if who then
		local v, where = OV.Inbox.ReadNamed(Status.NAME .. "-" .. who)
		if v then Status.source = where; return v end
	end
	local v, where = OV.Inbox.ReadNamed(Status.NAME)
	Status.source = where
	return v
end

-- Call every tick; it reads at most every POLL seconds. Returns true when news arrived.
function Status.Poll(now)
	if now - Status.lastPoll < Status.POLL then return false end
	Status.lastPoll = now
	local st = Status.stats
	st.reads = st.reads + 1
	local text = Status.ReadFile()
	if text == nil or text == "" then
		Status.present = false
		return false
	end
	Status.present = true
	if text == Status.lastText then return false end
	Status.lastText = text
	-- heartbeat: only the header moved, so the body (and its CRC) is the one already accepted
	local nl = string.find(text, "\n", 1, true)
	if nl and Status.lastBody and string.sub(text, nl + 1) == Status.lastBody then
		local hp, hn = OV.split(string.gsub(string.sub(text, 1, nl - 1), "\r", ""), " ")
		local hseq = tonumber(hp[2])
		if hn >= 5 and hp[1] == "OVS1" and hseq and string.upper(hp[4]) == Status.lastCrc and hp[5] == Status.character then
			if hseq ~= Status.seq then
				Status.seq, Status.changedAt = hseq, now
				st.refreshed = st.refreshed + 1
			end
			return false
		end
	end
	local t, why = Status.Parse(text)
	if not t then
		st.bad = st.bad + 1
		Status.lastReason = why
		return false
	end
	Status.mismatch = (OV.character ~= nil and t.character ~= OV.character)
	if Status.mismatch then
		Status.lastReason = "written for " .. tostring(t.character)
		return false
	end
	Status.lastReason = nil
	if t.seq == Status.seq then return false end
	Status.seq, Status.character, Status.changedAt = t.seq, t.character, now
	Status.fields, Status.headline = t.fields, t.headline
	Status.more, Status.nmore = t.more, t.nmore
	Status.peers, Status.npeers = t.peers, t.npeers
	Status.muted, Status.nmuted, Status.byName = t.muted, t.nmuted, t.byName
	Status.devices = t.devices
	Status.defaults = t.defaults
	Status.blocked, Status.nblocked = t.blocked, t.nblocked
	local hp = OV.split(string.gsub(string.sub(text, 1, nl - 1), "\r", ""), " ")
	Status.lastBody, Status.lastCrc = string.sub(text, nl + 1), string.upper(hp[4])
	Status.version = Status.version + 1
	st.accepted = st.accepted + 1
	return true
end

-- Is the desktop app there, and for this character?
function Status.Alive(now)
	return Status.present and not Status.mismatch and Status.seq ~= nil and (now - Status.changedAt) <= Status.TTL
end
