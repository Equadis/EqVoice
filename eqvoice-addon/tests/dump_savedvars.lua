-- Plays a short session on the stub client and writes EqVoiceDB the way WoW writes SavedVariables
-- (tab-indented, ["key"] = value, "-- [n]" comments on array items). Used to test tools/read_savedvars.py.
--   luajit dump_savedvars.lua <out-file>

local here = arg[0]:gsub("[/\\][^/\\]*$", "")
package.path = here .. "/?.lua;" .. package.path
local Stub = require("wow_stub")
Stub.root = here .. "/../EqVoice"

local OV = Stub.Login({ inGuild = true })
Stub.loopback = { SAY = true }
OV.Slash("diag say")
Stub.Advance(8)
Stub.loopback = {}
OV.Slash("diag yell")
Stub.Advance(8)
OV.Slash("diag api")
OV.Log('a line with "quotes", a backslash \\ and a tab\t.')
Stub.Hear("H:AAAAAAAA:1:4:1:2.7:4200:5800", "GUILD", "Alice")
Stub.FireEvent("PLAYER_LOGOUT")

local function ser(v, indent)
	local t = type(v)
	if t == "string" then
		return '"' .. v:gsub("\\", "\\\\"):gsub('"', '\\"'):gsub("\t", "\\t"):gsub("\n", "\\n") .. '"'
	elseif t == "number" or t == "boolean" then
		return tostring(v)
	elseif t == "table" then
		local out, pad = { "{" }, string.rep("\t", indent + 1)
		local n = #v
		local keys = {}
		for k in pairs(v) do if type(k) ~= "number" then keys[#keys + 1] = k end end
		table.sort(keys)
		for _, k in ipairs(keys) do
			out[#out + 1] = pad .. '["' .. k .. '"] = ' .. ser(v[k], indent + 1) .. ","
		end
		for i = 1, n do
			out[#out + 1] = pad .. ser(v[i], indent + 1) .. ", -- [" .. i .. "]"
		end
		out[#out + 1] = string.rep("\t", indent) .. "}"
		return table.concat(out, "\n")
	end
	return "nil"
end

local f = assert(io.open(arg[1], "wb"))
f:write("EqVoiceDB = " .. ser(EqVoiceDB, 0) .. "\n")
f:close()
print("wrote " .. arg[1])
