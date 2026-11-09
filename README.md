# EqVoice

### [⬇ Download EqVoice (one click, Windows)](https://github.com/Equadis/EqVoice/releases/latest/download/EqVoice-Setup.exe)

Proximity voice chat for **OctoWoW**: talk to the players near you, quieter the further away they are. Test build.

## Install

1. Click the download above and double-click **EqVoice-Setup.exe**.
2. Windows may say "unknown publisher" (it isn't code-signed yet): **More info → Run anyway**.
3. Pick your WoW folder if it asks. Done: it installs the addon and a small tray app that starts with Windows.

## Use

- Start WoW as normal. EqVoice sits in your system tray (by the clock) and connects by itself.
- First time in game, a short setup picks your microphone, speakers and push-to-talk key.
- Open the settings any time: click the **mic icon beside your name** or type **`/eqvoice`**.
- Players near you who have EqVoice show a small speaker icon beside their name.
- Uninstall: Windows **Settings → Apps → EqVoice**.

## Something wrong? Send logs

1. In game: `/eqvoice` → **Save logs to send** (or tray icon → *Save logs to send*).
2. A zip appears on your Desktop with Explorer open on it. It holds logs only, no audio.
3. Join the [OctoWoW Discord](https://discord.gg/QtdNpKnU72), then in
   [this channel](https://discord.com/channels/1547014756463026197/1567260437102202880) DM or message **Equadis** with:
   - the zip (drag it into the chat)
   - your character name and realm
   - what you did, what you expected, and what happened

## For developers

Architecture, protocol and test guides are in [`docs/`](docs/) (start with [`docs/developers.md`](docs/developers.md)).
