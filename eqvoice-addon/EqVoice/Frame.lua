-- EqVoice / Frame.lua
-- Bridge frame "BF2": what the addon knows, packed into bytes, written to a file (and, truncated,
-- painted as colour cells). The desktop client reads it. Pure functions, no WoW API. The Python
-- decoder in eqvoice_client/bridge/frame.py is the other half; protocol/bridge_vectors.json,
-- generated from this file, keeps the two in step.
--
-- Bytes
--   0       version (2)
--   1..2    counter, +1 per publish, wraps at 65536
--   3..4    N, payload length (big endian)
--   5..     payload (N bytes)
--   ..      CRC-16/CCITT-FALSE over every byte before it, big endian
--
-- Payload
--   0       flags: 1 enabled, 2 file bridge on, 4 optical on, 8 roster fresh (inbound file
--           readable and current), 16 logging out, 32 lookups available, 64 identity mismatch
--           (the roster was written for another character), 128 results truncated
--   1..4    loginId: random per addon load; changes on every login, /reload and character switch
--   5..8    wall clock (unix seconds) when the frame was built
--   9..10   roster ack: sequence number of the last inbound roster file accepted
--   11..12  roster size: names currently being looked up
--   13      names checked in the last scan
--   14      of those, how many were loaded nearby
--   15      of those, how many were not loaded
--   16..17  average milliseconds between scans
--   18..19  cost of the last scan in 0.1 ms units (0 when the client clock is too coarse)
--   20      character name length, then the name
--   ..      realm name length, then the name
--   ..      result count R
--   R times: name length, name, distance (one byte, units of 0.5 yard, 254 max), flags
--            (1 = confirmed player, 2 = enemy)
--
-- Cells (optical copy, for diagnostics): a grid of 32 x 10 cells in a screen corner, 6 bits each
-- (two bits per colour channel: 0, 85, 170, 255). Cells 1..8 are a fixed anchor (the corners of
-- the RGB cube) so a reader can find the grid and measure its cell size. Bytes are written most
-- significant bit first from cell 9. Capacity is 234 bytes, so the optical copy carries the
-- nearest results that fit and sets the truncated flag.

local OV = EqVoice
local Frame = {}
OV.Frame = Frame

Frame.VERSION     = 2
Frame.COLS        = 32
Frame.ROWS        = 10
Frame.NCELLS      = 320
Frame.ANCHOR      = { 63, 0, 48, 12, 3, 60, 15, 51 }   -- W K R G B Y C M
Frame.LEVELS      = { 0, 85 / 255, 170 / 255, 1 }
Frame.HEADER      = 5
Frame.OPTICAL_MAX = 234
Frame.MAX_RESULTS = 200
Frame.NAME_MAX    = 24
Frame.REALM_MAX   = 32

Frame.FLAG_ENABLED     = 1
Frame.FLAG_FILE        = 2
Frame.FLAG_OPTICAL     = 4
Frame.FLAG_ROSTER      = 8
Frame.FLAG_LOGOUT      = 16
Frame.FLAG_LOOKUPS     = 32
Frame.FLAG_ID_MISMATCH = 64
Frame.FLAG_TRUNCATED   = 128

Frame.R_PLAYER = 1
Frame.R_ENEMY  = 2

-- ------------------------------------------------------------------- CRC-16

local crcTable
local function buildTable()
	crcTable = {}
	for i = 0, 255 do
		local crc = i * 256
		for k = 1, 8 do
			if crc >= 32768 then
				crc = OV.xor(OV.mod(crc * 2, 65536), 4129)   -- 0x1021
			else
				crc = OV.mod(crc * 2, 65536)
			end
		end
		crcTable[i] = crc
	end
end

-- CRC-16/CCITT-FALSE: init 0xFFFF, poly 0x1021, no reflection, no final xor.
-- Check value for "123456789" is 0x29B1.
function Frame.CRC16(s)
	if not crcTable then buildTable() end
	local crc = 65535
	for i = 1, string.len(s) do
		local idx = OV.xor(math.floor(crc / 256), string.byte(s, i))
		crc = OV.xor(OV.mod(crc * 256, 65536), crcTable[idx])
	end
	return crc
end

-- ------------------------------------------------------------ number packing

local function u16(n)
	n = OV.clamp(math.floor(n or 0), 0, 65535)
	return string.char(math.floor(n / 256), OV.mod(n, 256))
end

local function u32(n)
	n = OV.clamp(math.floor(n or 0), 0, 4294967295)
	local a = math.floor(n / 16777216)
	n = n - a * 16777216
	local b = math.floor(n / 65536)
	n = n - b * 65536
	local c = math.floor(n / 256)
	return string.char(a, b, c, n - c * 256)
end

local function byte(n)
	return string.char(OV.clamp(math.floor(n or 0), 0, 255))
end

local function lstr(s, max)
	s = s or ""
	if string.len(s) > max then s = string.sub(s, 1, max) end
	return string.char(string.len(s)) .. s
end

local function rd16(s, i) return string.byte(s, i) * 256 + string.byte(s, i + 1) end
local function rd32(s, i)
	return ((string.byte(s, i) * 256 + string.byte(s, i + 1)) * 256 + string.byte(s, i + 2)) * 256 + string.byte(s, i + 3)
end

-- ------------------------------------------------------------------ encode

local function cleanName(name)
	name = name or ""
	local out, n = {}, 0
	for i = 1, string.len(name) do
		local b = string.byte(name, i)
		if b >= 33 and b ~= 127 and b ~= 44 then          -- no control characters, spaces or commas
			if n + 1 > Frame.NAME_MAX then break end
			n = n + 1
			out[n] = string.char(b)
		end
	end
	return table.concat(out)
end

-- state = { counter, flags, loginId, wall, ack, rosterSize, checked, loaded, misses, intervalMs, cost,
--           character, realm, results = { {name, dist, flags}, ... } nearest first }
-- maxBytes (optional): the largest frame allowed; the farthest results are dropped to fit.
-- Returns the frame bytes, or nil plus a reason.
function Frame.Encode(state, maxBytes)
	local flags = state.flags or 0
	local ident = lstr(cleanName(state.character), Frame.NAME_MAX) .. lstr(state.realm, Frame.REALM_MAX)
	local results = state.results or {}
	local entries, count, size = {}, 0, 0
	local base = Frame.HEADER + 20 + string.len(ident) + 1 + 2     -- header + fixed + identity + count + crc
	local cut = false
	for i = 1, Frame.MAX_RESULTS do
		local r = results[i]
		if not r then break end
		local name = cleanName(r.name)
		if string.len(name) > 0 then
			local e = lstr(name, Frame.NAME_MAX) .. byte(math.min(254, math.floor((r.dist or 0) * 2 + 0.5))) .. byte(r.flags or 0)
			if maxBytes and base + size + string.len(e) > maxBytes then
				cut = true
				break
			end
			count = count + 1
			entries[count] = e
			size = size + string.len(e)
		end
	end
	if results[count + 1] and not cut then cut = true end        -- some were skipped (empty names or the cap)
	if cut then flags = flags + (flags >= 128 and 0 or 128) end
	local payload = byte(flags) .. u32(state.loginId) .. u32(state.wall) .. u16(state.ack) ..
		u16(state.rosterSize) .. byte(state.checked) .. byte(state.loaded) .. byte(state.misses) ..
		u16(state.intervalMs) .. u16(state.cost) .. ident .. byte(count) .. table.concat(entries)
	local head = string.char(Frame.VERSION) .. u16(OV.mod(math.floor(state.counter or 0), 65536)) ..
		u16(string.len(payload)) .. payload
	local crc = Frame.CRC16(head)
	local frame = head .. u16(crc)
	if maxBytes and string.len(frame) > maxBytes then return nil, "toolong" end
	return frame
end

-- ------------------------------------------------------------------ decode
-- Used by the tests and by /ev diag bridge to prove the encoder round-trips.

function Frame.Decode(bytes)
	local len = string.len(bytes)
	if len < Frame.HEADER + 2 then return nil, "short" end
	if string.byte(bytes, 1) ~= Frame.VERSION then return nil, "version" end
	local n = rd16(bytes, 4)
	if len < n + Frame.HEADER + 2 then return nil, "short" end
	local head = string.sub(bytes, 1, n + Frame.HEADER)
	if Frame.CRC16(head) ~= rd16(bytes, n + Frame.HEADER + 1) then return nil, "crc" end
	local p = string.sub(bytes, Frame.HEADER + 1, n + Frame.HEADER)
	if string.len(p) < 22 then return nil, "payload" end
	local st = {
		counter = rd16(bytes, 2),
		flags = string.byte(p, 1), loginId = rd32(p, 2), wall = rd32(p, 6), ack = rd16(p, 10),
		rosterSize = rd16(p, 12), checked = string.byte(p, 14), loaded = string.byte(p, 15),
		misses = string.byte(p, 16), intervalMs = rd16(p, 17), cost = rd16(p, 19), results = {},
	}
	local pos = 21
	local cl = string.byte(p, pos)
	st.character = string.sub(p, pos + 1, pos + cl)
	pos = pos + 1 + cl
	local rl = string.byte(p, pos)
	st.realm = string.sub(p, pos + 1, pos + rl)
	pos = pos + 1 + rl
	local count = string.byte(p, pos)
	pos = pos + 1
	for i = 1, count do
		local nl = string.byte(p, pos)
		if not nl or pos + nl + 2 > string.len(p) then return nil, "result" end
		st.results[i] = {
			name = string.sub(p, pos + 1, pos + nl),
			dist = string.byte(p, pos + nl + 1) / 2,
			flags = string.byte(p, pos + nl + 2),
		}
		pos = pos + nl + 3
	end
	return st
end

-- ------------------------------------------------------------------- cells

-- bytes -> array[1..320] of 6-bit values; cells 1..8 are the anchor.
function Frame.ToCells(bytes)
	local cells = {}
	for i = 1, Frame.NCELLS do cells[i] = 0 end
	for i = 1, 8 do cells[i] = Frame.ANCHOR[i] end
	local idx, acc, nbits = 9, 0, 0
	for i = 1, string.len(bytes) do
		acc = acc * 256 + string.byte(bytes, i)
		nbits = nbits + 8
		while nbits >= 6 do
			local shift = 2 ^ (nbits - 6)
			local v = math.floor(acc / shift)
			acc = acc - v * shift
			nbits = nbits - 6
			if idx > Frame.NCELLS then return nil, "toolong" end
			cells[idx] = v
			idx = idx + 1
		end
	end
	if nbits > 0 then
		if idx > Frame.NCELLS then return nil, "toolong" end
		cells[idx] = acc * (2 ^ (6 - nbits))
	end
	return cells
end

-- Inverse of ToCells for a cell array; returns the bytes or nil.
function Frame.FromCells(cells)
	for i = 1, 8 do
		if cells[i] ~= Frame.ANCHOR[i] then return nil, "anchor" end
	end
	local out, n = {}, 0
	local acc, nbits = 0, 0
	local need = nil
	for idx = 9, Frame.NCELLS do
		acc = acc * 64 + cells[idx]
		nbits = nbits + 6
		while nbits >= 8 do
			local shift = 2 ^ (nbits - 8)
			local b = math.floor(acc / shift)
			acc = acc - b * shift
			nbits = nbits - 8
			n = n + 1
			out[n] = string.char(b)
			if n == Frame.HEADER then
				need = string.byte(out[4]) * 256 + string.byte(out[5]) + Frame.HEADER + 2
			end
			if need and n >= need then return table.concat(out) end
		end
	end
	return nil, "short"
end

function Frame.CellRGB(v)
	local r = math.floor(v / 16)
	local g = OV.mod(math.floor(v / 4), 4)
	local b = OV.mod(v, 4)
	return Frame.LEVELS[r + 1], Frame.LEVELS[g + 1], Frame.LEVELS[b + 1]
end
