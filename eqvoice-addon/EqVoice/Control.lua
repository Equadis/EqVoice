-- EqVoice / Control.lua
-- The addon -> desktop direction of the in-game panel: what the player clicked. A small text file, written through a
-- mod's file API (which is not atomic: the desktop checks the CRC and reads again if it caught the file half written).
--
--   OVC1 <seq> <unix-ts> <crc> <character>
--   <id> <command> <argument>            the newest few commands, oldest first
--
--   commands   mode open|ptt    mute 0|1    deafen 0|1    volume 0..100    test 1
--   id         strictly increasing, and it starts from the wall clock (seconds x 100), so a desktop that starts later
--              ignores commands from before it started, and no command is applied twice
--
-- Every write carries the last MAX commands, so two clicks inside one desktop poll (0.25 s) are both seen. The file is
-- written twice, eqvoice-ctl-<Character>.txt and eqvoice-ctl.txt, like the bridge frame: the desktop reads the one that
-- matches the roster file's name.
--
-- Numbers go through %.0f: a wall-clock x 100 does not fit the 32-bit %d of the game's Lua 5.0.

local OV = EqVoice
local Control = {}
OV.Control = Control

Control.NAME  = "eqvoice-ctl"
Control.MAX   = 8
Control.queue = {}
Control.nq    = 0
Control.lastId   = 0
Control.seq      = 0
Control.lastText = nil
Control.lastError = nil
Control.stats = { sent = 0, errors = 0 }

function Control.NextId()
	local id = (time and time() or 0) * 100
	if id <= Control.lastId then id = Control.lastId + 1 end
	Control.lastId = id
	return id
end

-- The file text for the current queue (pure apart from the sequence number and the clock).
function Control.Text()
	local lines = {}
	for i = 1, Control.nq do
		local q = Control.queue[i]
		lines[i] = string.format("%.0f %s %s", q.id, q.cmd, q.arg)
	end
	local body = table.concat(lines, "\n") .. "\n"
	Control.seq = OV.mod(Control.seq + 1, 4294967296)
	local head = string.format("OVC1 %.0f %.0f %04X %s", Control.seq, (time and time() or 0), OV.Frame.CRC16(body), tostring(OV.character or "?"))
	return head .. "\n" .. body
end

-- Queue one command and write the file. Returns true when it was written.
function Control.Send(cmd, arg)
	arg = tostring(arg)
	local id = Control.NextId()
	local nq = {}
	local first = Control.nq - Control.MAX + 2
	if first < 1 then first = 1 end
	local n = 0
	for i = first, Control.nq do
		n = n + 1
		nq[n] = Control.queue[i]
	end
	n = n + 1
	nq[n] = { id = id, cmd = cmd, arg = arg }
	Control.queue, Control.nq = nq, n
	local api = OV.Bridge.FileApi()
	if not api then
		Control.lastError = "no file api"
		Control.stats.errors = Control.stats.errors + 1
		return false
	end
	local text = Control.Text()
	Control.lastText = text
	local ok, err = OV.Bridge.WriteNamed(api, Control.NAME, text)
	if ok then
		local who = OV.FileSafeName and OV.FileSafeName()
		if who then
			local ok2, err2 = OV.Bridge.WriteNamed(api, Control.NAME .. "-" .. who, text)
			if not ok2 then ok, err = false, err2 end
		end
	end
	if not ok then
		Control.lastError = tostring(err)
		Control.stats.errors = Control.stats.errors + 1
		return false
	end
	Control.lastError = nil
	Control.stats.sent = Control.stats.sent + 1
	OV.Trace("panel: sent " .. cmd .. " " .. arg, true)
	return true
end
