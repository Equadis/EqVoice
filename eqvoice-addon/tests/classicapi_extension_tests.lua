-- Standalone regression test for v0.7.4. Run: luajit eqvoice-addon/tests/classicapi_extension_tests.lua
local here = arg and arg[0] and string.gsub(arg[0], "[/\\\\][^/\\\\]*$", "") or "."
package.path = here .. "/?.lua;" .. package.path
local Stub = require("wow_stub")
Stub.root = here .. "/../EqVoice"
local OV = Stub.Boot({classic = true, fileApi = "WriteCustomFile", units = {alice = {name = "Alice", player = true, dist = 5}}})
local function check(x, label) assert(x, label) end
CLASSIC_API_VERSION = nil
EqVoice_GetOverheadPoint = nil
check(not OV.DependencyReady(), "missing ClassicAPI must block startup")
CLASSIC_API_VERSION = 12100
EqVoice_GetOverheadPoint = function() return 100, 100 end
check(not OV.DependencyReady(), "old version must not pass")
CLASSIC_API_VERSION = 12101
EqVoice_GetOverheadPoint = nil
check(not OV.DependencyReady(), "stock version without extension must not pass")
EqVoice_GetOverheadPoint = function() return 100, 100 end
check(OV.DependencyReady(), "required version and feature pass")
Stub.FireEvent("VARIABLES_LOADED")
Stub.FireEvent("PLAYER_ENTERING_WORLD")
check(OV.started, "ready starts normally")
local row = {name = "Alice", state = "w"}
OV.Status.Alive = function() return true end
OV.Status.peers, OV.Status.npeers, OV.Status.byName = {row}, 1, {alice = row}
local I = OV.Icons
I.lastRun = -100
I.Tick(Stub.now)
local o = I.overhead.Alice
check(o and o.host.shown, "icon is visible without creating a nameplate")
check(o.icon.state == "w", "quiet icon")
check(o.icon.tex.w == 24 and o.icon.tex.h == 24, "the overhead icon is 24 pixels (50% bigger than the 16 it was)")
check(o.host.pt[2] == WorldFrame, "uses screen projection origin")
EqVoice_GetOverheadPoint = function() return nil end
I.lastRun = -100
I.Tick(Stub.now)
check(not o.host.shown, "offscreen projection must hide icon")
EqVoice_GetOverheadPoint = function() return 100, 200 end
I.SetOverhead(false)
check(not o.host.shown, "overhead toggle disables icon")
print("ClassicAPI extension regression checks passed")
