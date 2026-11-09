-- Print the trace lines the real Calibrate.lua writes for a small /say calibration, one per line prefixed "TRACE ",
-- so the Python side (tools/say_calibrate.py) can check that it understands the addon's actual words.
--   luajit tests/say_sample.lua
-- Scenario: Me and Bobby; Me speaks four numbers while Bobby walks away from 20 to 50 yards; Bobby's numbers 1, 2 and
-- one garbled line arrive; number 3 never arrives.

local here = (string.gsub(arg[0], "[/\\][^/\\]*$", ""))
package.path = here .. "/?.lua;" .. package.path
local Stub = require("wow_stub")
Stub.root = here .. "/../EqVoice"

local units = { ["0xB"] = { name = "Bobby", player = true, dist = 20 } }
local OV = Stub.Login({ classic = true, units = units, playerName = "Me" })
local orig = OV.Trace
OV.Trace = function(msg, quiet)
	io.write("TRACE ", msg, "\n")
	return orig(msg, quiet)
end
OV.Slash("say with Bobby")
OV.Slash("say start")
Stub.Advance(2)
units["0xB"].dist = 22
Stub.FireEvent("CHAT_MSG_SAY", "ovcal 1 2000", "Bobby", "Common")
units["0xB"].dist = 30
Stub.Advance(2)
Stub.FireEvent("CHAT_MSG_SAY", "ovcal 2 2000", "Bobby", "Common")
units["0xB"].dist = 42
Stub.Advance(4)
Stub.FireEvent("CHAT_MSG_SAY", "ovcal 4 2000", "Bobby", "Common")
units["0xB"].dist = 50
Stub.Advance(2)
Stub.FireEvent("CHAT_MSG_SAY", "Lok tar ogar", "Bobby", "Orcish")
OV.Slash("say stop")
