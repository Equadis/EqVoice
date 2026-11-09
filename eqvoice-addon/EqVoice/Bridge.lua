-- EqVoice / Bridge.lua
-- Gets BF1 frames out of the game, to the desktop client. Two sinks:
--
--  optical  a 32x10 block of solid colour cells in a screen corner. The client
--           reads the pixels. Needs no mods; WoW must be visible on screen.
--  file     a one-line text file written through a mod's file API when one
--           exists (Nampower >= 3.2 WriteCustomFile, or SuperWoW/Nampower
--           ExportFile). Works with WoW minimised.
--
-- The 1.12 sandbox has no sockets and no files of its own; SavedVariables are
-- only written at logout, so they cannot be a live channel.

local OV = EqVoice
local Bridge = {}
OV.Bridge = Bridge

Bridge.FILE_NAME = "eqvoice"          -- ".txt" is appended by the mod
Bridge.optical   = true
Bridge.file      = true
Bridge.corner    = "TOPLEFT"
Bridge.cellSize  = 6                    -- UI units per cell
Bridge.shown     = {}                   -- cell index -> value currently painted
Bridge.tex       = {}
Bridge.frame     = nil
Bridge.stats     = { frames = 0, cellWrites = 0, fileWrites = 0, fileErrors = 0 }
Bridge.lastFileError = nil

-- Which file API does this client have? Probed every time, never assumed.
function Bridge.FileApi()
	if type(WriteCustomFile) == "function" then return "WriteCustomFile" end
	if type(ExportFile) == "function" then return "ExportFile" end
	return nil
end

local CORNERS = { TOPLEFT = 1, TOPRIGHT = 1, BOTTOMLEFT = 1, BOTTOMRIGHT = 1 }

function Bridge.SetCorner(corner)
	corner = string.upper(corner or "")
	if not CORNERS[corner] then return false end
	Bridge.corner = corner
	if Bridge.frame then
		Bridge.frame:ClearAllPoints()
		Bridge.frame:SetPoint(corner, UIParent, corner, 0, 0)
	end
	return true
end

function Bridge.SetCellSize(units)
	units = OV.clamp(math.floor(tonumber(units) or 6), 3, 16)
	Bridge.cellSize = units
	if Bridge.frame then Bridge.Destroy() end
	return units
end

function Bridge.Destroy()
	if Bridge.frame then Bridge.frame:Hide() end
	Bridge.frame, Bridge.tex, Bridge.shown = nil, {}, {}
end

local function build()
	if Bridge.frame then return Bridge.frame end
	if not CreateFrame or not UIParent then return nil end
	local size = Bridge.cellSize
	local f = CreateFrame("Frame", nil, UIParent)
	f:SetFrameStrata("TOOLTIP")
	f:SetWidth(OV.Frame.COLS * size)
	f:SetHeight(OV.Frame.ROWS * size)
	f:SetPoint(Bridge.corner, UIParent, Bridge.corner, 0, 0)
	-- A black backing so rounding gaps between cells never show the game world.
	local bg = f:CreateTexture(nil, "BACKGROUND")
	bg:SetTexture("Interface\\Buttons\\WHITE8X8")
	bg:SetVertexColor(0, 0, 0, 1)
	bg:SetAllPoints(f)
	for i = 1, OV.Frame.NCELLS do
		local col = OV.mod(i - 1, OV.Frame.COLS)
		local row = math.floor((i - 1) / OV.Frame.COLS)
		local t = f:CreateTexture(nil, "OVERLAY")
		t:SetTexture("Interface\\Buttons\\WHITE8X8")
		t:SetWidth(size)
		t:SetHeight(size)
		t:SetPoint("TOPLEFT", f, "TOPLEFT", col * size, -row * size)
		t:SetVertexColor(0, 0, 0, 1)
		Bridge.tex[i] = t
		Bridge.shown[i] = 0
	end
	Bridge.frame = f
	return f
end

local function render(cells)
	local f = build()
	if not f then return false end
	f:Show()
	for i = 1, OV.Frame.NCELLS do
		local v = cells[i]
		if Bridge.shown[i] ~= v then
			local r, g, b = OV.Frame.CellRGB(v)
			Bridge.tex[i]:SetVertexColor(r, g, b, 1)
			Bridge.shown[i] = v
			Bridge.stats.cellWrites = Bridge.stats.cellWrites + 1
		end
	end
	return true
end

-- One file, through whichever API this client has. `name` has no extension.
local function writeNamed(api, name, content)
	if api == "WriteCustomFile" then
		return pcall(WriteCustomFile, name .. ".txt", content, "w")
	end
	return pcall(ExportFile, name, content)
end

Bridge.WriteNamed = writeNamed          -- the panel's command file (Control.lua) goes out the same way

-- The file is the frame line, then (when there is any) the addon's trace lines:
--   OVB2 <hex>
--   OVT <seq> <wall> <text>      (the newest few; the desktop client logs each seq once)
-- It is written twice: eqvoice.txt for the usual one-game-per-folder setup, and eqvoice-<Character>.txt so
-- two game instances sharing one folder can each be read by their own desktop client.
local function writeFile(bytes)
	local api = Bridge.FileApi()
	if not api then return false, "no file api" end
	local content = "OVB2 " .. OV.tohex(bytes)
	local trace = OV.TraceText and OV.TraceText() or ""
	if trace ~= "" then content = content .. "\n" .. trace end
	local ok, err = writeNamed(api, Bridge.FILE_NAME, content)
	if not ok then
		Bridge.stats.fileErrors = Bridge.stats.fileErrors + 1
		Bridge.lastFileError = tostring(err)
		return false, err
	end
	Bridge.stats.fileWrites = Bridge.stats.fileWrites + 1
	local who = OV.FileSafeName and OV.FileSafeName()
	if who then
		local okC, errC = writeNamed(api, Bridge.FILE_NAME .. "-" .. who, content)
		if okC then
			Bridge.stats.charWrites = (Bridge.stats.charWrites or 0) + 1
		else
			Bridge.stats.fileErrors = Bridge.stats.fileErrors + 1
			Bridge.lastFileError = tostring(errC)
		end
	end
	return true
end

-- full: a BF2 frame from Frame.Encode, written to the file. compact: the same state cut to fit the
-- optical grid (234 bytes), painted as cells. Either may be the same string.
function Bridge.Publish(full, compact)
	local bytes = full
	local cells = OV.Frame.ToCells(compact or full)
	if not cells then return false end
	Bridge.stats.frames = Bridge.stats.frames + 1
	local okO, okF = false, false
	if Bridge.optical then
		okO = render(cells)
	elseif Bridge.frame then
		Bridge.frame:Hide()
	end
	if Bridge.file and Bridge.FileApi() then
		okF = writeFile(bytes)
	end
	return okO or okF
end

-- Which sinks are live right now (for the status flags and /ev status).
function Bridge.Active()
	return Bridge.optical and true or false,
	       (Bridge.file and Bridge.FileApi()) and true or false
end
