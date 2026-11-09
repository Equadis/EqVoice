-- Parse a roster file with the real addon parser and print the result in a fixed format.
--   luajit tests/inbox_parity.lua <file>
-- Used by eqvoice-client/tests/test_inbox.py to prove the Python writer and the Lua reader agree.

local here = (string.gsub(arg[0], "[/\\][^/\\]*$", ""))
package.path = here .. "/?.lua;" .. package.path
local Stub = require("wow_stub")
Stub.root = here .. "/../EqVoice"
local OV = Stub.Boot()

local f = assert(io.open(arg[1], "rb"))
local text = f:read("*a")
f:close()

local t, why = OV.Inbox.Parse(text)
if not t then
	print("ERR " .. tostring(why))
	return
end
print("OK " .. string.format("%d %d %s", t.seq, t.ts, t.character))
local keys = {}
for k in pairs(t.config) do keys[table.getn(keys) + 1] = k end
table.sort(keys)
for i = 1, table.getn(keys) do print("CFG " .. keys[i] .. "=" .. string.format("%.10g", t.config[keys[i]])) end
print("NAMES " .. table.concat(t.names, ","))
if t.test then print("TEST " .. t.test) end
