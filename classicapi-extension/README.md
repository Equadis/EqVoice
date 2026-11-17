# Experimental ClassicAPI extension for EqVoice 0.7.4

Base: upstream [ClassicAPI v1.21.1](https://github.com/brues-code/ClassicAPI/tree/v1.21.1), GPL-3.0-or-later. The extension uses the existing engine projection used by ClassicAPI's SoftTargetIcon.cpp; it does not read a nameplate frame. The point is the head-name position plus the engine's two-thirds-yard lift, **not measured glyph bounds**. Camera/scale/visibility, mount, occlusion and name-only cases need testing on the real 1.12.1 client. This source is experimental and not a verified DLL.

On Windows with MSVC Win32 and CMake, make a new clone of the upstream tag and copy EqVoiceOverhead.cpp into its src/unit folder. Build using upstream instructions:

    cmake -B build -A Win32 -DCLASSICAPI_TAG=v1.21.1
    cmake --build build --config Release

The output is build/Release/ClassicAPI.dll. Keep upstream source and licence available when distributing a modified binary; do not present the binary as an official upstream build. Back up existing ClassicAPI.dll before a manual test replacement; close WoW first. The launcher may overwrite a modified build. Verify the function exists in game before packaging:

    /run DEFAULT_CHAT_FRAME:AddMessage(tostring(CLASSIC_API_VERSION).." "..type(EqVoice_GetOverheadPoint))

Set EQVOICE_CLASSICAPI_DLL to the absolute path of that compiled DLL when building EqVoice. The optional installer prompt never replaces an existing root or Mods DLL. If the original DLL is already installed, the experimental extension must be installed manually after backup. The upstream launcher version alone cannot satisfy the new function requirement.

Missing ClassicAPI: the warning UI exposes a selectable link (or copies it if clipboard API is available); a vanilla Lua addon without ClassicAPI cannot launch a browser directly.

Development only: run existing Lua and Python regression suites, update the old nameplate-oriented fixtures to projected-coordinate fixtures, and verify name-only anchoring before merging/publishing. No production release is implied.
