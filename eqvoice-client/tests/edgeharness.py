"""Run a small page of JavaScript in a real (headless) Edge or Chrome and get its answer back as JSON.

The page is served, and its result posted back, by a tiny local web server, so nothing needs a debugging protocol or an extra package.
It is served over http (not opened from disk) because a page from disk has no origin: browsers refuse an audio worklet from it.
Scripts are embedded in the page, which is what ``page()`` does.
"""
import http.server
import json
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from eqvoice_client import browser

UI = Path(__file__).resolve().parents[1] / "eqvoice_client" / "ui"


def available() -> bool:
    return browser.find_browser() is not None


def page(script: str, scripts=("voicemodel.js",)) -> str:
    """An HTML page that runs ``script`` after the named UI files. ``script`` calls ``done(value)`` with something JSON-able."""
    embedded = "\n".join("<script>\n%s\n</script>" % (UI / s).read_text(encoding="utf-8") for s in scripts)
    return ("<!doctype html><meta charset='utf-8'><title>harness</title>\n" + embedded + "\n<script>\n"
            "const done = (v) => fetch('http://127.0.0.1:' + location.hash.slice(1) + '/r', {method: 'POST', mode: 'no-cors', body: JSON.stringify(v)});\n"
            "window.addEventListener('error', (e) => done({error: String(e.message)}));\n"
            "window.addEventListener('unhandledrejection', (e) => done({error: String(e.reason)}));\n"
            + script + "\n</script>")


def run(html: str, timeout: float = 90.0, args=()):
    got = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            got["r"] = json.loads(self.rfile.read(n) or b"null")
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tmp = Path(tempfile.mkdtemp(prefix="eqvoice-edge-"))
    proc = subprocess.Popen([str(browser.find_browser()), "--headless=new", "--disable-gpu", "--no-first-run", "--autoplay-policy=no-user-gesture-required",
                             "--user-data-dir=%s" % (tmp / "profile"), *args, "http://127.0.0.1:%d/#%d" % (srv.server_address[1], srv.server_address[1])],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        end = time.time() + timeout
        while "r" not in got and time.time() < end:
            time.sleep(0.2)
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)         # the browser profile: tens of megabytes, and a test run makes hundreds
    if "r" not in got:
        raise TimeoutError("the page never answered")
    if isinstance(got["r"], dict) and "error" in got["r"] and len(got["r"]) == 1:
        raise RuntimeError("the page failed: %s" % got["r"]["error"])
    return got["r"]
