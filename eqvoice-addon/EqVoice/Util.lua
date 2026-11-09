-- EqVoice / Util.lua
-- Shared helpers. The 1.12 client runs Lua 5.0, so nothing here uses the #
-- operator, the % operator, string.match/gmatch, select() or `...`.
-- tools/lint_lua50.py enforces that; the test suite runs on LuaJIT (5.1) and
-- cannot see those mistakes by itself.

EqVoice = EqVoice or {}
local OV = EqVoice

OV.VERSION     = "0.2.0"
OV.PROTO       = 1
OV.PREFIX      = "OV1"          -- heartbeat traffic
OV.DIAG_PREFIX = "OVD"          -- diagnostics traffic, never mixed with the above
OV.ALPHABET    = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"   -- 32 symbols, no I L O U

-- ---------------------------------------------------------------- arithmetic

function OV.mod(a, b)
	return a - math.floor(a / b) * b
end

function OV.clamp(v, lo, hi)
	if v < lo then return lo end
	if v > hi then return hi end
	return v
end

function OV.round(v)
	return math.floor(v + 0.5)
end

-- Bitwise XOR for integers 0..2^31 without a bit library (Lua 5.0 has none).
function OV.xor(a, b)
	local r, bit = 0, 1
	while a > 0 or b > 0 do
		local x = OV.mod(a, 2)
		local y = OV.mod(b, 2)
		if x ~= y then r = r + bit end
		a = (a - x) / 2
		b = (b - y) / 2
		bit = bit * 2
	end
	return r
end

function OV.shr(a, n) return math.floor(a / (2 ^ n)) end
function OV.shl(a, n) return a * (2 ^ n) end

-- ------------------------------------------------------------------- strings

-- Split on a single plain-text separator character. Returns the parts and
-- their count, because Lua 5.0 has no length operator to recover it.
function OV.split(s, sep)
	local out, n, pos = {}, 0, 1
	while true do
		local i = string.find(s, sep, pos, true)
		if not i then
			n = n + 1
			out[n] = string.sub(s, pos)
			break
		end
		n = n + 1
		out[n] = string.sub(s, pos, i - 1)
		pos = i + 1
	end
	return out, n
end

function OV.tohex(s)
	local out, n = {}, 0
	for i = 1, string.len(s) do
		n = n + 1
		out[n] = string.format("%02X", string.byte(s, i))
	end
	return table.concat(out)
end

function OV.fromhex(h)
	if OV.mod(string.len(h), 2) ~= 0 then return nil end
	local out, n = {}, 0
	for i = 1, string.len(h), 2 do
		local v = tonumber(string.sub(h, i, i + 1), 16)
		if not v then return nil end
		n = n + 1
		out[n] = string.char(v)
	end
	return table.concat(out)
end

-- ------------------------------------------------------------ session ids
-- A sid is 40 random bits written as 8 symbols of OV.ALPHABET. It is chosen by
-- the addon at each login and never saved: it identifies a play session, not a
-- person. The desktop client adopts it from the bridge.

function OV.NewSid(rand)
	rand = rand or math.random
	local t, n = {}, 0
	for i = 1, 8 do
		local k = math.floor(rand() * 32) + 1
		if k > 32 then k = 32 end
		n = n + 1
		t[n] = string.sub(OV.ALPHABET, k, k)
	end
	return table.concat(t)
end

-- 8 symbols -> 5 raw bytes (40 bits, big endian). nil when malformed.
function OV.SidToBytes(sid)
	if type(sid) ~= "string" or string.len(sid) ~= 8 then return nil end
	local v = 0
	for i = 1, 8 do
		local c = string.sub(sid, i, i)
		local k = string.find(OV.ALPHABET, c, 1, true)
		if not k then return nil end
		v = v * 32 + (k - 1)
	end
	local b = {}
	for i = 5, 1, -1 do
		b[i] = string.char(OV.mod(v, 256))
		v = math.floor(v / 256)
	end
	return table.concat(b)
end

function OV.BytesToSid(b)
	if type(b) ~= "string" or string.len(b) ~= 5 then return nil end
	local v = 0
	for i = 1, 5 do v = v * 256 + string.byte(b, i) end
	local t = {}
	for i = 8, 1, -1 do
		local k = OV.mod(v, 32)
		t[i] = string.sub(OV.ALPHABET, k + 1, k + 1)
		v = math.floor(v / 32)
	end
	return table.concat(t)
end

-- A short stable hash of a string, as 4 hex digits (used to name zones whose
-- map position is unavailable, e.g. instances).
function OV.Hash4(s)
	local h = 0
	for i = 1, string.len(s) do
		h = OV.mod(h * 31 + string.byte(s, i), 65536)
	end
	return string.format("%04X", h)
end

-- ----------------------------------------------------------------- logging
-- Ring buffer kept in memory and copied into SavedVariables on logout, so a
-- problem can be read after the fact with tools/read_savedvars.py.

OV.logLines = OV.logLines or {}
OV.logCount = OV.logCount or 0
OV.LOG_MAX  = 120

function OV.Now()
	if GetTime then return GetTime() end
	return 0
end

function OV.Log(msg)
	OV.logCount = OV.logCount + 1
	OV.logLines[OV.mod(OV.logCount - 1, OV.LOG_MAX) + 1] =
		string.format("%8.1f ", OV.Now()) .. msg
	if OV.debugOn then OV.Print("|cff888888" .. msg .. "|r") end
end

function OV.LogTail(n)
	local total = OV.logCount
	local first = total - n + 1
	if first < 1 then first = 1 end
	local out, k = {}, 0
	for i = first, total do
		k = k + 1
		out[k] = OV.logLines[OV.mod(i - 1, OV.LOG_MAX) + 1]
	end
	return out, k
end

function OV.Print(msg)
	if DEFAULT_CHAT_FRAME then
		DEFAULT_CHAT_FRAME:AddMessage("|cff66ccffEqVoice|r " .. msg)
	end
end

-- ------------------------------------------------------------------- trace
-- Boundary-by-boundary narration for diagnosing a test. Every trace line goes to the log ring and, as "OVT <seq> <wall> <text>" lines
-- after the frame, into the outbound bridge file, so the desktop client logs what the addon saw next to its own lines. It goes to the
-- chat frame only when a developer asked (/ev trace on): a player never sees it.

OV.TRACE_KEEP = 16
OV.traceSeq   = OV.traceSeq or 0
OV.traceRing  = OV.traceRing or {}

function OV.Trace(msg, quiet)
	msg = string.gsub(tostring(msg), "%c", " ")
	if string.len(msg) > 200 then msg = string.sub(msg, 1, 200) end
	OV.traceSeq = OV.traceSeq + 1
	OV.traceRing[OV.mod(OV.traceSeq - 1, OV.TRACE_KEEP) + 1] =
		{ seq = OV.traceSeq, wall = (time and time()) or 0, text = msg }
	OV.Log("T " .. msg)
	if not quiet and OV.db and OV.db.chatTrace == true then
		OV.Print("|cff9fe89f" .. msg .. "|r")
	end
end

-- The newest trace lines as bridge-file text (empty when there are none).
function OV.TraceText()
	local first = OV.traceSeq - OV.TRACE_KEEP + 1
	if first < 1 then first = 1 end
	local out, n = {}, 0
	for i = first, OV.traceSeq do
		local e = OV.traceRing[OV.mod(i - 1, OV.TRACE_KEEP) + 1]
		if e and e.seq == i then
			n = n + 1
			out[n] = "OVT " .. e.seq .. " " .. e.wall .. " " .. e.text
		end
	end
	return table.concat(out, "\n")
end

-- The logged-in character as part of a file name, or nil when it is not plain ASCII letters. Two game
-- instances that share one folder use eqvoice-<Name>.txt / eqvoice-in-<Name>.txt so they do not collide.
function OV.FileSafeName()
	local c = OV.character
	if c and string.len(c) >= 2 and string.len(c) <= 24 and not string.find(c, "[^A-Za-z]") then return c end
	return nil
end
