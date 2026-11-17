// SPDX-License-Identifier: GPL-3.0-or-later
// EqVoice.dll, independent of the unmodified ClassicAPI.dll.
// Engine addresses and calling conventions based on ClassicAPI v1.21.1:
// src/Offsets.h, src/Game.cpp, src/target/SoftTargetIcon.cpp (GPL-3.0-or-later).
// Only the 32-bit 1.12.1.5875 client is supported. No game data is written;
// the one registration hook adds a read-only Lua projection function.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <winver.h>
#include <MinHook.h>
#include <cstdint>
#include <cmath>
#include <cstring>
#include <vector>
static_assert(sizeof(void*) == 4, "Build Win32, not x64");
namespace {
template<typename T> T fn(uintptr_t a) { return reinterpret_cast<T>(a); }
template<typename T> T read(uintptr_t a) { return *reinterpret_cast<const T*>(a); }
using LuaFn = int(__fastcall*)(void*);
using Register = void(__fastcall*)(const char*, LuaFn);
Register original = nullptr;
constexpr uintptr_t kRegister = 0x00704120;
constexpr uintptr_t kResolve = 0x00515940;
constexpr uintptr_t kProject = 0x0060F7F0;
constexpr uintptr_t kWorld = 0x00B4B2BC;
constexpr uintptr_t kScale = 0x00832A44;
constexpr uintptr_t kScriptScale = 0x007FFD68;
constexpr uintptr_t kAspect = 0x0041AD70;
constexpr uintptr_t kDivide = 0x0041AE40;
constexpr uintptr_t kIsString = 0x006F3510;
constexpr uintptr_t kToString = 0x006F3690;
constexpr uintptr_t kPushNumber = 0x006F3810;
constexpr uintptr_t kPushNil = 0x006F37F0;
bool compatible() {
    HMODULE exe = GetModuleHandleW(nullptr);
    if (reinterpret_cast<uintptr_t>(exe) != 0x00400000) return false;
    wchar_t path[MAX_PATH]{};
    if (!GetModuleFileNameW(exe, path, MAX_PATH)) return false;
    DWORD ignored = 0, size = GetFileVersionInfoSizeW(path, &ignored);
    if (!size) return false;
    std::vector<unsigned char> data(size);
    if (!GetFileVersionInfoW(path, 0, size, data.data())) return false;
    VS_FIXEDFILEINFO* info = nullptr; UINT len = 0;
    if (!VerQueryValueW(data.data(), L"\\", reinterpret_cast<void**>(&info), &len) ||
        len < sizeof(VS_FIXEDFILEINFO) || info->dwSignature != 0xFEEF04BD) return false;
    if (info->dwFileVersionMS != MAKELONG(12, 1) ||
        info->dwFileVersionLS != MAKELONG(5875, 1)) return false;
    // Refuse addresses that do not map to committed executable/readable image pages.
    for (uintptr_t a : {kRegister,kResolve,kProject,kWorld,kScale,kScriptScale,kAspect,kDivide,kIsString,kToString,kPushNumber,kPushNil}) {
        MEMORY_BASIC_INFORMATION m{};
        if (!VirtualQuery(reinterpret_cast<void*>(a), &m, sizeof(m)) ||
            m.AllocationBase != exe || m.State != MEM_COMMIT ||
            (m.Protect & (PAGE_GUARD | PAGE_NOACCESS))) return false;
    }
    return true;
}
double coordinate(float a) {
    if (read<float>(kScale) == 0) return 0;
    float aspect = fn<float(__cdecl*)()>(kAspect)();
    return fn<double(__stdcall*)(float)>(kDivide)(aspect * read<float>(kScriptScale) * a);
}
int __fastcall point(void* L) {
    auto nil = fn<void(__fastcall*)(void*)>(kPushNil);
    if (!fn<bool(__fastcall*)(void*,int)>(kIsString)(L,1)) { nil(L); return 1; }
    const char* token = fn<const char*(__fastcall*)(void*,int)>(kToString)(L,1);
    void* unit = token ? fn<void*(__fastcall*)(const char*)>(kResolve)(token) : nullptr;
    void* world = read<void*>(kWorld);
    float xy[2]{};
    if (!unit || !world || read<float>(kScale) == 0 ||
        !fn<bool(__fastcall*)(void*,void*,float*)>(kProject)(unit,world,xy)) { nil(L); return 1; }
    double x = coordinate(xy[0]), y = coordinate(xy[1]);
    if (!std::isfinite(x) || !std::isfinite(y)) { nil(L); return 1; }
    auto number = fn<void(__fastcall*)(void*,double)>(kPushNumber);
    number(L,x); number(L,y);
    return 2;
}
void __fastcall registerHook(const char* name, LuaFn callback) {
    original(name,callback);
    // ClassicAPI registers this function in the game Lua state on login and
    // /reload. Register directly through the original trampoline in that state.
    if (name && std::strcmp(name,"UnitTokenFromName") == 0)
        original("EqVoice_GetOverheadPoint",&point);
}
}
extern "C" DWORD Load() {
    static bool loaded = false;
    if (loaded) return 0;
    if (!GetModuleHandleW(L"ClassicAPI.dll") || !compatible()) return 1;
    if (MH_Initialize() != MH_OK) return 1;
    if (MH_CreateHook(reinterpret_cast<void*>(kRegister),reinterpret_cast<void*>(&registerHook),
                      reinterpret_cast<void**>(&original)) != MH_OK) { MH_Uninitialize(); return 1; }
    if (MH_EnableHook(reinterpret_cast<void*>(kRegister)) != MH_OK) { MH_Uninitialize(); return 1; }
    loaded = true;
    return 0;
}
BOOL WINAPI DllMain(HINSTANCE, DWORD, LPVOID) { return TRUE; }
