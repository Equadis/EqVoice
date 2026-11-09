"""Run a page from eqvoice-signalling-cf in headless Chromium and collect the JSON it posts back."""
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CF = ROOT / "eqvoice-signalling-cf"


def find_browser():
    candidates = []
    if sys.platform == "win32":
        for r in filter(None, [os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")]):
            candidates += [Path(r) / "Microsoft/Edge/Application/msedge.exe", Path(r) / "Google/Chrome/Application/chrome.exe"]
    for n in ("microsoft-edge", "google-chrome", "chromium", "chromium-browser", "chrome", "msedge"):
        p = shutil.which(n)
        if p:
            candidates.append(Path(p))
    return next((c for c in candidates if c.exists()), None)


def available() -> bool:
    return find_browser() is not None and os.environ.get("EQVOICE_E2E") != "0"


async def run_page(page: str, timeout: float = 90.0, extra_files: dict = None) -> dict:
    """Serve eqvoice-signalling-cf (src/ and tests/) plus ``extra_files`` ({url path: file}) and open ``page``."""
    from aiohttp import web

    result = asyncio.get_event_loop().create_future()
    extra_files = extra_files or {}

    async def index(request):
        return web.FileResponse(CF / "tests" / page)

    async def static(request):
        rel = request.match_info["path"]
        p = (CF / rel).resolve()
        if CF.resolve() not in p.parents or not p.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(p, headers={"Content-Type": "text/javascript"} if p.suffix == ".js" else {})

    def extra(path):
        async def handler(request):
            return web.FileResponse(path)
        return handler

    async def post_result(request):
        if not result.done():
            result.set_result(await request.json())
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_get("/", index)
    for url_path, file in extra_files.items():
        app.router.add_get(url_path, extra(file))
    app.router.add_post("/result", post_result)
    app.router.add_get("/{path:(src|tests)/.*}", static)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    profile = tempfile.mkdtemp(prefix="ov-js-")
    proc = subprocess.Popen([str(find_browser()), "--headless=new", "--disable-gpu", "--no-first-run",
                             "--no-default-browser-check", "--user-data-dir=" + profile, "http://127.0.0.1:%d/" % port],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        return await asyncio.wait_for(result, timeout)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        await runner.cleanup()
        shutil.rmtree(profile, ignore_errors=True)
