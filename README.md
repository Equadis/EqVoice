# EqVoice

### [⬇ Download EqVoice for Windows](https://github.com/Equadis/EqVoice/releases/latest/download/EqVoice-Setup.exe)

**Proximity voice chat for OctoWoW.** Talk to the players standing near you, and hear them from the direction they are in. The further apart you are, the quieter it gets. No server to join, no channel to pick, no extra window to keep open.

## What it is

- **Natural distance.** Full volume up close, fading smoothly with distance. A raised voice carries further, a quiet one less far, and a whisper key halves your range.
- **Directional sound.** Headphones put each voice to the left, right, front or back of you. Speakers and mono are there too.
- **Clean voice.** Noise suppression, a keyboard-click filter, and a one-minute Calibrate that sets the sensitivity to your room.
- **Open mic or push to talk.** One-key mute, deafen, and a microphone test.
- **Control of who you hear.** Per-player volume, mute and block. Block is a mute and a deafen at once: you do not hear them and they do not hear you.
- **See who is talking.** A small icon above each EqVoice player and beside their name: speaking, speaking quietly, yelling, muted or deafened.
- **Private.** Voice goes straight between players (a relay only steps in when a direct link cannot be made). Nothing is recorded.

## Easy install

1. Download **EqVoice-Setup.exe** (button above) and double-click it.
2. Windows may say "unknown publisher" (it is not code-signed): **More info → Run anyway**.
3. Answer the few questions it asks. Start WoW. A short in-game setup picks your microphone, speakers and talk key.

Needs Windows 10 or 11 with Microsoft Edge (already there). Close WoW first if setup says a file is in use. To remove it: **Windows Settings → Apps → EqVoice**.

## Clean interface

One small panel, in the game's own style. Open it by clicking the icon beside your name or typing `/eqvoice`. Everything is a button or a slider: no hidden menus, no walls of text.

## Consent every step of the way

- **Setup asks before it installs anything**, including the game add-on and the small native extension (ClassicAPI and `EqVoice.dll`). Say no and nothing changes.
- **Starting with Windows is a question, and it is off unless you say yes.** If EqVoice is not running when you play, the game tells you and you start it yourself.
- **You choose your microphone, speakers and key** in the first-run setup. EqVoice only talks to players who also have it, and only the ones near you.
- **You decide who you hear.** Mute or block anyone at any time. Turn the icons over heads off with one button.
- **Nothing leaves your PC unless you send it.** Logs are saved as a zip only when you press **Export Logs**, and hold no audio.

## Something wrong? Send logs

1. In game: `/eqvoice` → **Export Logs** (or tray icon → *Export logs*).
2. A zip appears on your Desktop with Explorer open on it. It holds logs only, no audio.
3. Join the [OctoWoW Discord](https://discord.gg/QtdNpKnU72), then in
   [this channel](https://discord.com/channels/1547014756463026197/1567260437102202880) DM or message **Equadis** with:
   - the zip (drag it into the chat)
   - your character name and realm
   - what you did, what you expected, and what happened

## For developers

Architecture, protocol and test guides are in [`docs/`](docs/) (start with [`docs/developers.md`](docs/developers.md)). Third-party notices: [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
