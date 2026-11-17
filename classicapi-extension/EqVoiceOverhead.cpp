// SPDX-License-Identifier: GPL-3.0-or-later
// EqVoice extension for ClassicAPI v1.21.1. Build as part of ClassicAPI.
// Projects the engine's overhead name anchor, including the 2/3-yard lift.
// It does NOT read or require a nameplate frame. This is not the glyph bounds.
#include "Game.h"
#include "Offsets.h"
#include <cmath>

namespace EqVoiceOverhead {
namespace {
using Project_t = bool(__fastcall *)(void *, void *, float *);
int __fastcall GetPoint(void *L) {
    if (!Game::Lua::IsString(L, 1)) { Game::Lua::PushNil(L); return 1; }
    const char *token = Game::Lua::ToString(L, 1);
    void *unit = token ? Game::ResolveUnitToken(token) : nullptr;
    void *world = Game::Read<void *>(Offsets::VAR_GAME_STATE_PTR);
    float xy[2] = {};
    if (!unit || !world || !reinterpret_cast<Project_t>(
            Offsets::FUN_UNIT_GET_NAMEPLATE_SCREEN_POS)(unit, world, xy)) {
        Game::Lua::PushNil(L); return 1;
    }
    const double x = Game::InternalToPixel(xy[0]);
    const double y = Game::InternalToPixel(xy[1]);
    if (!std::isfinite(x) || !std::isfinite(y)) { Game::Lua::PushNil(L); return 1; }
    Game::Lua::PushNumber(L, x);
    Game::Lua::PushNumber(L, y);
    return 2;
}
const Game::Doc::Field args[] = { Game::Doc::Req("unit", "UnitToken") };
const Game::Doc::Field rets[] = {
    Game::Doc::Opt("x", "number", nullptr, "Projected horizontal offset from WorldFrame bottom left; nil when off-screen."),
    Game::Doc::Opt("y", "number", nullptr, "Projected vertical offset.")
};
const Game::Doc::Function doc{"Projects the engine overhead anchor without a nameplate frame.", args, rets, "UnitGlobals"};
void Register() {
    Game::Lua::RegisterGlobalFunction("EqVoice_GetOverheadPoint", &GetPoint, &doc);
}
const Game::ModuleAutoRegister reg{&Register};
}
}
