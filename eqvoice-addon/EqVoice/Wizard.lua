-- EqVoice / Wizard.lua
-- The first-time setup: a small WoW-styled box that appears by itself the first time EqVoice is running with this account, and takes someone who
-- knows nothing about EqVoice to "it works" in under a minute:
--
--   1 Welcome        what EqVoice is
--   2 Microphone     the microphone Windows chose, a list to pick another, and a live level bar to see that it works
--   3 Speakers       the same, and a test sound
--   4 Push to talk   open microphone or push to talk, and the key (press a new one to bind it)
--   5 Voice options  your microphone's volume, everyone else's volume, mute and deafen, and the one-minute microphone setup
--   6 Check          Microphone detected, Output detected, EqVoice connected: then Finish
--
-- Closing it in any way counts as done: it never comes back by itself (EqVoiceDB.ui.setupDone). /eqvc welcome opens it again, and everything in it
-- is also in the panel (/eqvc, /eqvoice, the microphone icon beside your name). It draws with the panel's own pieces (Panel.UI) and is only a
-- view of the status the app writes: the choices go out as the same commands the panel sends.
--
-- Lua 5.0 for the 1.12 client. Everything is inside a pcall: a drawing mistake must never get in the way of the voice.

local OV = EqVoice
local Wizard = {}
OV.Wizard = Wizard

Wizard.WIDTH     = 440
Wizard.HEIGHT    = 360
Wizard.PAGES     = 7
Wizard.START_AFTER_S = 8        -- seconds the app must have been running (and the player in the world) before the box appears
Wizard.page      = 1
Wizard.frame     = nil
Wizard.shownOnce = false
Wizard.aliveSince = nil
Wizard.error     = nil
Wizard.checkAt   = nil          -- when the last page was reached: a check that is still red some seconds later says so

local TITLES = { "Welcome to EqVoice", "Your microphone", "Your speakers", "How you hear", "Push to talk", "Calibrate", "All set?" }

local function UI() return OV.Panel and OV.Panel.UI end

local function fail(err)
	if Wizard.error ~= tostring(err) then
		Wizard.error = tostring(err)
		OV.Trace("setup box error: " .. tostring(err))
	end
end

local function safe(fn)
	return function()
		local ok, err = pcall(fn)
		if not ok then fail(err) end
	end
end

-- ------------------------------------------------------------------ what each page of the check says (pure)

-- The three lines of the last page: { text, state } with state "yes", "no" or "wait".
function Wizard.Checks(m, secondsOnPage)
	local waited = (secondsOnPage or 0) >= 10
	local function line(text, ok)
		if ok then return { text = text, state = "yes" } end
		return { text = text, state = waited and "no" or "wait" }
	end
	local outs = m.devOut and table.getn(m.devOut) or 0
	return {
		line("Microphone detected", m.alive and (m.mic == "ok")),
		line("Output detected", m.alive and outs > 0),
		line("EqVoice connected", m.alive and (OV.Status.fields.hub == "1")),
	}
end

-- ------------------------------------------------------------------ the box

function Wizard.Build()
	if Wizard.frame then return Wizard.frame end
	local ui = UI()
	if not ui or not CreateFrame or not UIParent then return nil end
	local newText, newButton = ui.newText, ui.newButton
	local f = CreateFrame("Frame", "EqVoiceWizard", UIParent)
	f:SetWidth(Wizard.WIDTH)
	f:SetHeight(Wizard.HEIGHT)
	f:SetFrameStrata("DIALOG")
	f:SetFrameLevel(30)
	f:EnableMouse(true)
	f:SetMovable(true)
	f:SetClampedToScreen(true)
	f:RegisterForDrag("LeftButton")
	f:SetScript("OnDragStart", function() this:StartMoving() end)
	f:SetScript("OnDragStop", safe(function() this:StopMovingOrSizing(); ui.savePos(Wizard.frame, "wizardPos") end))
	ui.paint(f, ui.THEME.bg, ui.THEME.edge)
	ui.restorePos(f, "wizardPos", "CENTER", 0, 60)
	f:Hide()

	f.title = newText(f, "GameFontNormalLarge", 20, -18, 330, 18)
	f.step = newText(f, "GameFontDisableSmall", 20, -40, 330, 12)
	ui.closeButton(f, "EqVoiceWizardClose", function() Wizard.Finish(false) end)

	-- page 1: welcome
	local p1 = {}
	p1[1] = newText(f, "GameFontHighlight", 24, -70, 392, 60)
	p1[1]:SetText("EqVoice adds proximity voice chat to OctoWoW.\n\nPlayers close to you hear you, and you hear them. The further apart you are, the quieter it gets.")
	p1[2] = newText(f, "GameFontNormalSmall", 24, -170, 392, 40)
	p1[2]:SetText("This takes less than a minute: your microphone, your speakers and the key you press to talk.")
	f.pages = { p1 }

	-- page 2: microphone
	local p2 = {}
	p2[1] = newText(f, "GameFontHighlightSmall", 24, -66, 392, 12)
	p2[1]:SetText("Pick the microphone you talk into.")
	p2.list = OV.Panel.NewDeviceList(f, "in", 24, -86, 392, function(kind, label, index) OV.Panel.SetDevice(kind, label, index) end)
	p2.meter = OV.Panel.NewMeter(f, 26, -282, 388)
	p2[2] = newText(f, "GameFontHighlightSmall", 24, -294, 392, 26)
	p2[2]:SetText("Say something: the bar should move.")
	p2.problem = newText(f, "GameFontNormalSmall", 24, -318, 392, 26)
	p2.problem:SetTextColor(1.0, 0.3, 0.3)
	f.pages[2] = p2

	-- page 3: speakers
	local p3 = {}
	p3[1] = newText(f, "GameFontHighlightSmall", 24, -66, 392, 12)
	p3[1]:SetText("Pick where you hear other players.")
	p3.list = OV.Panel.NewDeviceList(f, "out", 24, -86, 392, function(kind, label, index) OV.Panel.SetDevice(kind, label, index) end)
	p3[2] = newText(f, "GameFontDisableSmall", 24, -318, 392, 26)
	p3[2]:SetText("Anything the game plays comes out of the same place. To hear yourself, use Test Microphone in the settings.")
	f.pages[3] = p3

	-- page 4: how you hear: mono, speakers or headphones
	local p4s = {}
	p4s[1] = newText(f, "GameFontHighlightSmall", 24, -66, 392, 40)
	p4s[1]:SetText("Voices can come from the side the player is on: someone to your right is heard in your right ear.")
	p4s.mono = newButton(f, "EqVoiceWizardMono", "Mono", 392, 24, -116, function() OV.Panel.SetSpatial("mono") end, 30)
	p4s.speakers = newButton(f, "EqVoiceWizardSpeakers", "Speakers", 392, 24, -154, function() OV.Panel.SetSpatial("speakers") end, 30)
	p4s.headphones = newButton(f, "EqVoiceWizardHeadphones", "Headphones", 392, 24, -192, function() OV.Panel.SetSpatial("headphones") end, 30)
	p4s[2] = newText(f, "GameFontDisableSmall", 24, -232, 392, 60)
	p4s[2]:SetText("Mono: no sides, every voice in the middle.\nSpeakers: a gentle left and right, made for speakers in front of you.\nHeadphones: the full effect, including in front of and behind you.")
	f.pages[4] = p4s

	-- page 5: push to talk
	local p4 = {}
	p4[1] = newText(f, "GameFontHighlightSmall", 24, -66, 392, 40)
	p4[1]:SetText("Open microphone sends your voice whenever you are not muted. Push to talk sends it only while you hold a key.")
	p4.open = newButton(f, "EqVoiceWizardOpen", "Open microphone", 180, 24, -116, function() OV.Panel.SetMode("open") end)
	p4.ptt = newButton(f, "EqVoiceWizardPtt", "Push to talk", 180, 214, -116, function() OV.Panel.SetMode("ptt") end)
	p4.keytext = newText(f, "GameFontNormal", 24, -166, 392, 14)
	p4.key = newButton(f, "EqVoiceWizardKey", "Change the key", 180, 24, -196, function()
		if OV.Panel.capturing then OV.Panel.StopCapture("key not changed") else OV.Panel.CaptureKey() end
	end)
	p4[2] = newText(f, "GameFontDisableSmall", 24, -232, 392, 40)
	f.pages[5] = p4

	-- page 6: calibrate (and the two volumes)
	local p5 = {}
	p5.sIn, p5.inText = ui.sliderRow(f, "WizIn", "Boost", -80, 0, 200, 1, 50,
		function(v) return string.format("%d%%", v) end, function(v) OV.Panel.SetInput(v) end)
	p5.sOut, p5.outText = ui.sliderRow(f, "WizOut", "Other players", -112, 0, 100, 1, 50,
		function(v) return string.format("%d%%", v) end, function(v) OV.Panel.SetVolume(v) end)
	p5.setup = newButton(f, "EqVoiceWizardMicSetup", "Calibrate", 392, 24, -150, function() OV.Panel.StartSetup() end, 30)
	p5[1] = newText(f, "GameFontDisableSmall", 24, -192, 392, 60)
	p5[1]:SetText("Calibrate asks you to stay silent, talk and then shout (about 17 seconds) and sets the sensitivity so that keyboard clicks and room noise stay off the line. You can do it again later from the settings.")
	f.pages[6] = p5

	-- page 7: the check
	local p6 = {}
	p6.rows = {}
	for i = 1, 3 do
		local row = {}
		row.tex = f:CreateTexture(nil, "OVERLAY")
		row.tex:SetWidth(18)
		row.tex:SetHeight(18)
		row.tex:SetPoint("TOPLEFT", f, "TOPLEFT", 28, -76 - (i - 1) * 28)
		row.text = newText(f, "GameFontHighlight", 54, -80 - (i - 1) * 28, 340, 14)
		p6.rows[i] = row
	end
	p6.summary = newText(f, "GameFontNormal", 24, -176, 392, 60)
	p6.hint = newText(f, "GameFontDisableSmall", 24, -240, 392, 60)
	p6.hint:SetText("Later, change anything by clicking the microphone beside your name, or type /eqvoice or /eqvc. If something goes wrong, the Export Logs button there makes a zip you can drag into a chat.")
	f.pages[7] = p6

	f.bBack = newButton(f, "EqVoiceWizardBack", "Back", 90, 24, -322, function() Wizard.Go(Wizard.page - 1) end)
	f.bNext = newButton(f, "EqVoiceWizardNext", "Next", 110, 306, -322, function()
		if Wizard.page >= Wizard.PAGES then Wizard.Finish(true) else Wizard.Go(Wizard.page + 1) end
	end)
	f.bSkip = newButton(f, "EqVoiceWizardSkip", "Skip", 70, 124, -322, function() Wizard.Finish(false) end)

	if UISpecialFrames then table.insert(UISpecialFrames, "EqVoiceWizard") end
	f:SetScript("OnHide", safe(function()
		if Wizard.open then Wizard.Finish(false) end                       -- Escape counts as closing it
	end))
	Wizard.frame = f
	return f
end

local function showPage(f, which)
	for i = 1, table.getn(f.pages) do
		local p = f.pages[i]
		for k, w in pairs(p) do
			if type(w) == "table" and w.Show and w.Hide then
				if i == which then w:Show() else w:Hide() end
			end
		end
		if p.list then if i ~= which then p.list.Hide() end end
		if p.meter then if i == which then p.meter.Show() else p.meter.Hide() end end
		if p.rows then
			for j = 1, table.getn(p.rows) do
				if i == which then p.rows[j].tex:Show(); p.rows[j].text:Show() else p.rows[j].tex:Hide(); p.rows[j].text:Hide() end
			end
		end
	end
end

function Wizard.Go(n)
	local ok, err = pcall(Wizard.Build)
	if not ok or not Wizard.frame then fail(err or "no frame"); return end
	if n < 1 then n = 1 end
	if n > Wizard.PAGES then n = Wizard.PAGES end
	Wizard.page = n
	Wizard.checkAt = (n == Wizard.PAGES) and OV.Now() or nil
	local f = Wizard.frame
	f.title:SetText(TITLES[n])
	f.step:SetText(string.format("Step %d of %d", n, Wizard.PAGES))
	showPage(f, n)
	if n == 1 then f.bBack:Hide() else f.bBack:Show() end
	f.bNext:SetText(n >= Wizard.PAGES and "Finish" or "Next")
	Wizard.Refresh(OV.Panel.Model(OV.Now()))
end

-- Show the box (at a page; the first by default). It is the same box every time.
function Wizard.Show(n)
	local ok, err = pcall(Wizard.Build)
	if not ok or not Wizard.frame then fail(err or "no frame"); return end
	Wizard.open = true
	Wizard.frame:Show()
	Wizard.Go(n or 1)
end

-- Close it. `finished` is the Finish button; any other way of closing it is the same as far as coming back is concerned.
function Wizard.Finish(finished)
	Wizard.open = false
	if OV.db and OV.db.ui then OV.db.ui.setupDone = true end
	if Wizard.frame and Wizard.frame:IsShown() then Wizard.frame:Hide() end
	if OV.Panel.capturing then OV.Panel.StopCapture(nil) end
	Wizard.checkAt = nil
end

function Wizard.ResetPosition()
	if OV.db and OV.db.ui then OV.db.ui.wizardPos = nil end
	if Wizard.frame then UI().restorePos(Wizard.frame, "wizardPos", "CENTER", 0, 60) end
end

-- The microphone's meter is needed while the box is on the microphone page or the speaker page does not need it; options page shows no meter.
function Wizard.NeedsMeter()
	return Wizard.open and Wizard.frame and Wizard.frame:IsShown() and Wizard.page == 2
end

-- Redraw the page that is showing from the model (called from Panel.Refresh).
function Wizard.Refresh(m)
	local f = Wizard.frame
	if not (f and Wizard.open and f:IsShown()) then return end
	local ui, n, p = UI(), Wizard.page, f.pages[Wizard.page]
	if n == 2 then
		p.list.Refresh(m)
		p.meter.Set(m.lvl)
		if m.micBad then p.problem:SetText("Your microphone cannot be used. In Windows, check Settings > Privacy > Microphone, and that one is plugged in.")
		else p.problem:SetText("") end
	elseif n == 3 then
		p.list.Refresh(m)
	elseif n == 4 then
		ui.setSelected(p.mono, m.sp == "mono")
		ui.setSelected(p.speakers, m.sp == "speakers")
		ui.setSelected(p.headphones, m.sp == "headphones")
	elseif n == 5 then
		ui.setSelected(p.open, m.mode == "open")
		ui.setSelected(p.ptt, m.mode == "ptt")
		if m.capturing then
			p.keytext:SetText("Press the key you want to use...")
			p.key:SetText("Press a key (Esc cancels)")
		else
			p.keytext:SetText("Push-to-talk key: " .. m.key)
			p.key:SetText("Change the key")
		end
		p[2]:SetText(m.mode == "ptt" and "Hold this key to talk." or "With an open microphone this key mutes and unmutes you.")
	elseif n == 6 then
		if not p.sIn.Busy() then p.sIn.Set(m.input); p.inText:SetText(string.format("%d%%", m.input)) end
		if not p.sOut.Busy() then p.sOut.Set(m.vol); p.outText:SetText(string.format("%d%%", m.vol)) end
	elseif n == 7 then
		local checks = Wizard.Checks(m, Wizard.checkAt and (OV.Now() - Wizard.checkAt) or 0)
		local allYes = true
		for i = 1, 3 do
			local c = checks[i]
			p.rows[i].text:SetText(c.text)
			if c.state == "yes" then
				p.rows[i].tex:SetTexture("Interface\\Buttons\\UI-CheckBox-Check")
				p.rows[i].tex:SetVertexColor(1, 1, 1)
			elseif c.state == "no" then
				p.rows[i].tex:SetTexture("Interface\\Buttons\\UI-GroupLoot-Pass-Up")
				p.rows[i].tex:SetVertexColor(1, 1, 1)
			else
				p.rows[i].tex:SetTexture("Interface\\Buttons\\UI-CheckBox-Check")
				p.rows[i].tex:SetVertexColor(0.5, 0.5, 0.5)
			end
			if c.state ~= "yes" then allYes = false end
		end
		if allYes then p.summary:SetText("Setup complete.")
		else p.summary:SetText("Not everything is ready yet. EqVoice keeps trying by itself; you can finish now and look at /eqvoice later.") end
	end
end

-- Called every tick: the box opens by itself, once, when EqVoice is running and the player has been in the world a moment.
function Wizard.Tick(now)
	local alive = OV.Status.Alive(now)
	if alive then Wizard.aliveSince = Wizard.aliveSince or now else Wizard.aliveSince = nil end
	if Wizard.open then
		Wizard.Refresh(OV.Panel.Model(now))
		return
	end
	if Wizard.shownOnce or not OV.db or not OV.db.ui or OV.db.ui.setupDone == true then return end
	if not (OV.started and alive and Wizard.aliveSince and now - Wizard.aliveSince >= Wizard.START_AFTER_S) then return end
	Wizard.shownOnce = true
	Wizard.Show(1)
end
