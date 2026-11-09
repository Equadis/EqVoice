-- Print what the real addon code writes to its outbound file for a small scenario, so the Python side can check
-- that the desktop client's trace parser and tools/pipeline_report.py understand the addon's actual words.
--   luajit tests/trace_sample.lua
-- (the stub clock and file system stand in for the game; the strings are the addon's own)

local here = (string.gsub(arg[0], "[/\\][^/\\]*$", ""))
package.path = here .. "/?.lua;" .. package.path
local Stub = require("wow_stub")
Stub.root = here .. "/../EqVoice"

local units = { ["0xB"] = { name = "Bobby", player = true, dist = 18.4 } }
local OV = Stub.Login({ classic = true, fileApi = "WriteCustomFile", units = units, playerName = "Alice" })
Stub.Advance(1)
local body = "C radius=40 interval=0.5 report=60 checks=80\nR Bobby,Carol\n"
Stub.fs["eqvoice-in-Alice.txt"] = string.format("OVI1 5 1760000000 %04X Alice\n", OV.Frame.CRC16(body)) .. body
Stub.Advance(3)
units["0xB"].dist = 31
Stub.Advance(3)
units["0xB"] = nil
Stub.Advance(3)
Stub.FireEvent("PLAYER_LOGOUT")
io.write(Stub.fs["eqvoice-Alice.txt"])
