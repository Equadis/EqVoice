# EqVoice.dll (experimental v0.7.4)
The native extension is an independent Win32 DLL, owned by EqVoice. **Do not replace ClassicAPI.dll with a patched build.** Stock ClassicAPI v1.21.1 or newer remains necessary. The projection function is registered during ClassicAPI's normal UnitTokenFromName Lua registration, including after /reload. The extension uses the existing client name-position projection routine (name point plus lift), not a visible nameplate or exact rendered glyph bounds. It refuses clients with a different executable version or memory layout. Only the VanillaFixes-compatible loader's Load entry point is supported; validate the actual OctoWoW loader and client before distributing.

Build with MSVC Win32 and CMake. tools/build-v074.ps1 builds the extension and verifies/downloads the official pinned ClassicAPI release, then packages both DLLs into EqVoice-Setup.exe. It does not publish by default. The installer asks for consent, preserves existing ClassicAPI, and writes the EqVoice module into dlls.txt. Restart WoW.

In-game checks:
    /run DEFAULT_CHAT_FRAME:AddMessage(tostring(CLASSIC_API_VERSION).." "..type(EqVoice_GetOverheadPoint))
    /run local x,y=EqVoice_GetOverheadPoint("target"); DEFAULT_CHAT_FRAME:AddMessage(tostring(x).." "..tostring(y))

Test on the actual client with two voice users, plates hidden, camera rotation, /reload, off-screen movement and whisper changes. Check that official ClassicAPI updates and other addons are unaffected. The extension and old suite still require Windows and in-game verification. The project sources include the relevant GPL-derived projection implementation and should accompany distribution (including applicable upstream licence/MinHook notices).
