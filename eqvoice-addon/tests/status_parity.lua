-- Parse a status file with the real addon parser and print the result in a fixed format.
--   luajit tests/status_parity.lua <file>
-- Used by eqvoice-client/tests/test_uifiles.py to prove the Python writer and the Lua reader agree.

local here = (string.gsub(arg[0], "[/\\][^/\\]*$", ""))
package.path = here .. "/?.lua;" .. package.path
local Stub = require("wow_stub")
Stub.root = here .. "/../EqVoice"
local OV = Stub.Boot()

local f = assert(io.open(arg[1], "rb"))
local text = f:read("*a")
f:close()

local t, why = OV.Status.Parse(text)
if not t then
	print("ERR " .. tostring(why))
	return
end
print("OK " .. string.format("%.0f %.0f %s", t.seq, t.ts, t.character))
local keys = {}
for k in pairs(t.fields) do keys[table.getn(keys) + 1] = k end
table.sort(keys)
for i = 1, table.getn(keys) do print("S " .. keys[i] .. "=" .. t.fields[keys[i]]) end
print("M " .. t.headline)
for i = 1, t.nmore do print("N " .. t.more[i]) end
for i = 1, t.npeers do
	local p = t.peers[i]
	print(string.format("P %s %s %d %d %s %s %d", p.name, p.dist and string.format("%.1f", p.dist) or "-", p.connected and 1 or 0, p.speaking and 1 or 0, p.path,
		p.state, p.muted and 1 or 0))
end
for _, which in ipairs({ "in", "out" }) do
	for i = 1, table.getn(t.devices[which]) do print("D " .. which .. " " .. i .. " " .. t.devices[which][i]) end
end
local names = {}
for k in pairs(t.muted) do names[table.getn(names) + 1] = k end
table.sort(names)
print("X " .. table.concat(names, " "))
