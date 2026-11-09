-- Send commands through the real addon (Control.Send) and write the file it produced.
--   luajit tests/control_parity.lua <out file> <character> <command> <argument> [<command> <argument> ...]
-- Used by eqvoice-client/tests/test_uifiles.py to prove the Lua writer and the Python reader agree.

local here = (string.gsub(arg[0], "[/\\][^/\\]*$", ""))
package.path = here .. "/?.lua;" .. package.path
local Stub = require("wow_stub")
Stub.root = here .. "/../EqVoice"
local OV = Stub.Login({ fileApi = "WriteCustomFile", classic = true, playerName = arg[2] })

local i = 3
while arg[i] do
	assert(OV.Control.Send(arg[i], arg[i + 1]), "send failed")
	i = i + 2
end
local text = Stub.fs["eqvoice-ctl-" .. arg[2] .. ".txt"] or Stub.fs["eqvoice-ctl.txt"]
local f = assert(io.open(arg[1], "wb"))
f:write(text)
f:close()
print("OK")
