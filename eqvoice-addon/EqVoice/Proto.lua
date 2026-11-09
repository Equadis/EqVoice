-- EqVoice / Proto.lua
-- Heartbeat wire format ("OV1"). Pure functions, no WoW API.
--
--   H:<sid>:<seq>:<iv>:<flags>[:<zone>:<x>:<y>]      presence heartbeat
--   B:<sid>:<seq>                                    leaving now
--
--   sid    8 symbols of OV.ALPHABET (a random per-login session id)
--   seq    0..65535, +1 per message, wraps; receivers drop non-newer values
--   iv     sender's heartbeat interval in seconds (2..60); receivers expire
--          the sender after 3 intervals + 1 s, so crowded areas can slow down
--   flags  hex byte. bit0 = voice enabled, bit1 = muted, bit2 = deafened, bit3 = the EqVoice app is running
--          (so a nearby player can show the right little icon beside your name: see Icons.lua). An older addon sends 1.
--   zone   "<continent>.<zone>" map indices, or "h<4 hex>" when the map position
--          is unavailable. x, y are 0..9999. Only sent on group channels, where
--          "nearby" has to be judged from position because the channel itself
--          is not local.
--
-- Everything is limited to [A-Za-z0-9:.]. Never use "|": it is WoW's chat escape
-- character, and the 1.12 client refuses to send any message containing a "|" that
-- is not a valid escape ("Invalid escape code in chat message"). Classic's SAY/YELL
-- addon channels are also documented to mangle other characters.

local OV = EqVoice
local Proto = {}
OV.Proto = Proto

Proto.MAX_LEN = 80
Proto.FLAG_ENABLED = 1
Proto.FLAG_MUTED   = 2
Proto.FLAG_DEAF    = 4
Proto.FLAG_APP     = 8

function Proto.FormatLoc(loc)
	return loc.zone .. ":" .. math.floor(loc.x) .. ":" .. math.floor(loc.y)
end

function Proto.EncodeHeartbeat(sid, seq, iv, flags, loc)
	local s = "H:" .. sid .. ":" .. math.floor(seq) .. ":" .. math.floor(iv) .. ":" ..
		string.format("%X", flags or 0)
	if loc then s = s .. ":" .. Proto.FormatLoc(loc) end
	return s
end

function Proto.EncodeBye(sid, seq)
	return "B:" .. sid .. ":" .. math.floor(seq)
end

local function parseUInt(s, maxv)
	if not s or not string.find(s, "^%d+$") or string.len(s) > 6 then return nil end
	local v = tonumber(s)
	if v > maxv then return nil end
	return v
end

local function parseLoc(zone, xs, ys)
	if not (string.find(zone, "^%d+%.%d+$") or string.find(zone, "^h%x%x%x%x$")) then
		return nil
	end
	local x = parseUInt(xs, 9999)
	local y = parseUInt(ys, 9999)
	if not x or not y then return nil end
	return { zone = zone, x = x, y = y }
end

-- Returns a table, or nil plus a short reason. Never raises.
function Proto.Decode(text)
	if type(text) ~= "string" then return nil, "type" end
	if string.len(text) > Proto.MAX_LEN then return nil, "long" end
	if string.find(text, "[^%w:%.]") then return nil, "charset" end
	local p, n = OV.split(text, ":")
	local kind = p[1]
	if kind == "H" then
		if n ~= 5 and n ~= 8 then return nil, "fields" end
		if not OV.SidToBytes(p[2]) then return nil, "sid" end
		local seq = parseUInt(p[3], 65535)
		if not seq then return nil, "seq" end
		local iv = parseUInt(p[4], 60)
		if not iv or iv < 2 then return nil, "iv" end
		if not string.find(p[5], "^%x%x?$") then return nil, "flags" end
		local m = { t = "H", sid = p[2], seq = seq, iv = iv, flags = tonumber(p[5], 16) }
		if n == 8 then
			m.loc = parseLoc(p[6], p[7], p[8])
			if not m.loc then return nil, "loc" end
		end
		return m
	elseif kind == "B" then
		if n ~= 3 then return nil, "fields" end
		if not OV.SidToBytes(p[2]) then return nil, "sid" end
		local seq = parseUInt(p[3], 65535)
		if not seq then return nil, "seq" end
		return { t = "B", sid = p[2], seq = seq }
	end
	return nil, "kind"
end

-- true when `new` is strictly ahead of `old` on the 16-bit wrapping counter.
function Proto.SeqNewer(new, old)
	local d = OV.mod(new - old, 65536)
	return d > 0 and d < 32768
end
