-- EqVoice / Inbox.lua
-- The desktop -> addon direction of the file bridge. The client writes a small text file; the
-- addon polls it once a second through a mod's file API (ReadCustomFile or ImportFile).
--
--   OVI1 <seq> <unix-ts> <crc> <character>
--   C radius=40 interval=0.5 report=60 checks=80       configuration (all optional)
--   R Alice,Bob,Carol                                  roster: names to look up (may repeat)
--   T free text                                        debug message, shown by /ev diag inbox
--
--   seq        changes with every write, wraps; the addon treats a *change* as news
--   crc        CRC-16/CCITT-FALSE of everything after the first newline (carriage returns ignored),
--              four upper-case hex digits. A torn or partial read fails it and is ignored.
--   character  the character the client believes this game is running. If it is not the character
--              logged in here, the file is ignored: a client cannot get lookups for a name it does
--              not actually have running.
--
-- Fail closed: the client rewrites the file at least every 5 s. If the sequence number does not
-- move for Inbox.TTL seconds the roster is dropped and lookups stop.
--
-- Cost. Most rewrites only move `seq` (the roster is the same, the write is a heartbeat). Checking the CRC and splitting a
-- thousand names in Lua 5.0 costs tens of milliseconds, a visible hitch every few seconds, so a file whose body is identical
-- to the last accepted one (same CRC field, same bytes, same character) is accepted by its header alone.

local OV = EqVoice
local Inbox = {}
OV.Inbox = Inbox

Inbox.NAME      = "eqvoice-in"      -- ".txt" is added by ReadCustomFile
Inbox.TTL       = 15
Inbox.MAX_NAMES = 2000
Inbox.NAME_MAX  = 24

Inbox.lastText   = nil
Inbox.seq        = nil
Inbox.changedAt  = -1000
Inbox.version    = 0        -- counts accepted files; lets the caller notice a change cheaply
Inbox.names      = {}
Inbox.count      = 0
Inbox.config     = {}
Inbox.character  = nil
Inbox.ts         = 0
Inbox.test       = nil
Inbox.present    = false
Inbox.idMismatch = false
Inbox.stats      = { reads = 0, accepted = 0, bad = 0, refreshed = 0 }
Inbox.lastBody   = nil      -- the raw body of the last file that passed the full check
Inbox.lastCrc    = nil      -- its CRC field, upper case
Inbox.lastReason = nil

local function crcHex(body)
	return string.format("%04X", OV.Frame.CRC16(body))
end

-- Pure: text -> table, or nil plus a reason. Never raises.
function Inbox.Parse(text)
	if type(text) ~= "string" then return nil, "type" end
	local nl = string.find(text, "\n", 1, true)
	if not nl then return nil, "noheader" end
	local head = string.gsub(string.sub(text, 1, nl - 1), "\r", "")
	local body = string.gsub(string.sub(text, nl + 1), "\r", "")
	local p, n = OV.split(head, " ")
	if n < 5 or p[1] ~= "OVI1" then return nil, "magic" end
	local seq, ts = tonumber(p[2]), tonumber(p[3])
	if not seq or not ts then return nil, "numbers" end
	if string.upper(p[4]) ~= crcHex(body) then return nil, "crc" end

	local t = { seq = seq, ts = ts, character = p[5], config = {}, names = {}, count = 0 }
	local seen = {}
	local pos = 1
	local len = string.len(body)
	while pos <= len do
		local e = string.find(body, "\n", pos, true)
		local line
		if e then line = string.sub(body, pos, e - 1); pos = e + 1
		else line = string.sub(body, pos); pos = len + 1 end
		local kind = string.sub(line, 1, 1)
		local rest = string.sub(line, 3)
		if kind == "C" then
			local items, ni = OV.split(rest, " ")
			for i = 1, ni do
				local eq = string.find(items[i], "=", 1, true)
				if eq then
					local k, v = string.sub(items[i], 1, eq - 1), tonumber(string.sub(items[i], eq + 1))
					if v then t.config[k] = v end
				end
			end
		elseif kind == "R" then
			local names, nn = OV.split(rest, ",")
			for i = 1, nn do
				local name = names[i]
				if string.len(name) >= 2 and string.len(name) <= Inbox.NAME_MAX and not string.find(name, "[%c%s]")
						and not seen[name] and t.count < Inbox.MAX_NAMES then
					seen[name] = true
					t.count = t.count + 1
					t.names[t.count] = name
				end
			end
		elseif kind == "T" then
			t.test = rest
		end
	end
	return t
end

-- One file by base name (no extension): CustomData first, then Imports. Returns text and where it came from.
local function readNamed(base)
	if type(ReadCustomFile) == "function" then
		local ok, v = pcall(ReadCustomFile, base .. ".txt")
		if ok and type(v) == "string" and v ~= "" then return v, "CustomData\\" .. base .. ".txt" end
	end
	if type(ImportFile) == "function" then
		local ok, v = pcall(ImportFile, base)
		if ok and type(v) == "string" and v ~= "" then return v, "Imports\\" .. base .. ".txt" end
	end
	return nil
end

Inbox.ReadNamed = readNamed         -- the panel's status file is read the same way

-- The file for this character (eqvoice-in-<Character>) wins, so two game instances in one folder do not
-- read each other's roster; otherwise the plain eqvoice-in. Sets Inbox.source to what was read.
function Inbox.ReadFile()
	local who = OV.FileSafeName and OV.FileSafeName()
	if who then
		local v, where = readNamed(Inbox.NAME .. "-" .. who)
		if v then Inbox.source = where; return v end
	end
	local v, where = readNamed(Inbox.NAME)
	Inbox.source = where
	return v
end

-- Call about once a second. Returns true when a new file was accepted.
function Inbox.Poll(now)
	local st = Inbox.stats
	st.reads = st.reads + 1
	local text = Inbox.ReadFile()
	if text == nil or text == "" then
		if Inbox.present or not Inbox.saidAbsent then
			local who = OV.FileSafeName and OV.FileSafeName()
			OV.Trace("inbound: " .. (Inbox.present and "the roster file disappeared" or "no roster file yet") ..
				" (looking in CustomData for " .. (who and ("eqvoice-in-" .. who .. ".txt or ") or "") .. "eqvoice-in.txt)")
			Inbox.saidAbsent = true
		end
		Inbox.present = false
		return false
	end
	Inbox.present = true
	Inbox.saidAbsent = false
	if text == Inbox.lastText then return false end
	Inbox.lastText = text
	-- heartbeat: only the header moved
	local nl = string.find(text, "\n", 1, true)
	if nl and Inbox.lastBody and string.sub(text, nl + 1) == Inbox.lastBody then
		local hp, hn = OV.split(string.gsub(string.sub(text, 1, nl - 1), "\r", ""), " ")
		local hseq = tonumber(hp[2])
		if hn >= 5 and hp[1] == "OVI1" and hseq and string.upper(hp[4]) == Inbox.lastCrc and hp[5] == Inbox.character then
			if hseq ~= Inbox.seq then
				Inbox.seq, Inbox.ts, Inbox.changedAt = hseq, tonumber(hp[3]) or Inbox.ts, now
				st.refreshed = st.refreshed + 1
			end
			return false
		end
	end
	local t, why = Inbox.Parse(text)
	if not t then
		st.bad = st.bad + 1
		Inbox.lastReason = why
		OV.Trace("inbound: " .. tostring(Inbox.source) .. " is not a valid roster file (" .. tostring(why) .. "), " ..
			string.len(text) .. " bytes, starts: " .. string.sub(text, 1, 40))
		return false
	end
	if t.seq == Inbox.seq then return false end
	Inbox.seq, Inbox.ts, Inbox.character, Inbox.test = t.seq, t.ts, t.character, t.test
	Inbox.changedAt = now
	if nl then
		local hp = OV.split(string.gsub(string.sub(text, 1, nl - 1), "\r", ""), " ")
		Inbox.lastBody, Inbox.lastCrc = string.sub(text, nl + 1), string.upper(hp[4])
	end
	Inbox.config = t.config
	Inbox.idMismatch = (OV.character ~= nil and t.character ~= OV.character)
	if Inbox.idMismatch then
		Inbox.names, Inbox.count = {}, 0
		Inbox.lastReason = "written for " .. tostring(t.character)
	else
		Inbox.names, Inbox.count = t.names, t.count
		Inbox.lastReason = nil
	end
	Inbox.version = Inbox.version + 1
	st.accepted = st.accepted + 1
	return true
end

-- Is the roster usable right now?
function Inbox.Fresh(now)
	return Inbox.present and not Inbox.idMismatch and Inbox.seq ~= nil and (now - Inbox.changedAt) <= Inbox.TTL
end
