"""Will the signalling hub fit in Cloudflare's free plan? A transparent estimate, not a promise.

    python tools/cost_estimate.py                       the default table (assumptions printed at the bottom)
    python tools/cost_estimate.py --hours 4 --near 0.5  heavier use
    python tools/cost_estimate.py --dau 750             one row, in detail

What is billed (Cloudflare Durable Objects, checked against developers.cloudflare.com/durable-objects/platform/pricing on
2026-10-17; prices change, check again before relying on this):
  * requests   a WebSocket connection counts as one; INCOMING messages count 1 per 20 (outgoing are free)
  * duration   wall-clock time the object is active, at 128 MB: GB-s = seconds x 0.128
  * Free plan  100,000 requests/day and 13,000 GB-s/day (rows read/written are far below their limits for this hub: it keeps its
               state in memory and a ~200-byte attachment per connection)
  * Paid plan  $5/month; 1 M requests and 400,000 GB-s a month included, then $0.15 per M requests and $12.50 per M GB-s
What a player's client sends (eqvoice_client/signalling.py): a ping every 10 s; while someone is in range, an unchanged
proximity report every 4 s plus one per 2 yards moved; and ~14 messages (offer/answer/ICE) per voice connection.

The object is assumed to stay awake whenever anybody is connected (their pings arrive every 10 s, the object's own alarm every
5-15 s), which is the pessimistic reading of hibernation. The measured truth comes from the dashboard after a real deployment.
"""
from __future__ import annotations

import argparse
import math

GB = 0.128                       # a Durable Object is billed as 128 MB
FREE_REQUESTS, FREE_GBS = 100_000, 13_000
PAID_REQUESTS, PAID_GBS = 1_000_000, 400_000      # per month, included in the $5
PAID_BASE, PER_M_REQUESTS, PER_M_GBS = 5.0, 0.15, 12.50


def estimate(dau: float, hours: float = 2.5, near: float = 0.3, calls: float = 6.0, msgs_per_call: float = 14.0, ping_s: float = 10.0,
             keepalive_s: float = 4.0, move_msgs: float = 500.0, sessions: float = 2.0) -> dict:
    """Per-day figures for ``dau`` daily active players, each online ``hours`` hours, with someone in range ``near`` of that time."""
    connections = dau * sessions
    pings = dau * hours * 3600.0 / ping_s
    prox = dau * hours * near * (3600.0 / keepalive_s + move_msgs)
    signal = dau * hours * near * calls * msgs_per_call
    other = connections * 2.0                                  # register + unregister
    incoming = pings + prox + signal + other
    requests = connections + incoming / 20.0
    concurrency = dau * hours / 24.0
    awake = 1.0 - math.exp(-concurrency)                       # share of the day with at least one player connected
    gbs = 86400.0 * awake * GB
    month_req, month_gbs = requests * 30.0, gbs * 30.0
    paid = PAID_BASE + max(0.0, month_req - PAID_REQUESTS) / 1e6 * PER_M_REQUESTS + max(0.0, month_gbs - PAID_GBS) / 1e6 * PER_M_GBS
    return {"dau": dau, "concurrent": concurrency, "incoming_msgs": incoming, "requests": requests, "gbs": gbs,
            "free_requests_pct": 100.0 * requests / FREE_REQUESTS, "free_gbs_pct": 100.0 * gbs / FREE_GBS,
            "fits_free": requests <= FREE_REQUESTS and gbs <= FREE_GBS, "paid_usd_month": paid,
            "split": {"pings": pings / 20.0, "proximity": prox / 20.0, "signalling": signal / 20.0, "connections": connections + other / 20.0}}


def max_free_dau(**kw) -> int:
    lo, hi = 0, 1_000_000
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if estimate(mid, **kw)["fits_free"]:
            lo = mid
        else:
            hi = mid - 1
    return lo


def table(rows: list, **kw) -> str:
    out = ["| daily players | avg online at once | requests/day | free requests used | GB-s/day | free duration used | fits free plan | paid plan ($/month) |",
           "|---|---|---|---|---|---|---|---|"]
    for n in rows:
        e = estimate(n, **kw)
        out.append("| %s | %.1f | %s | %.0f%% | %s | %.0f%% | %s | %.2f |" % (
            format(n, ","), e["concurrent"], format(round(e["requests"]), ","), e["free_requests_pct"], format(round(e["gbs"]), ","),
            e["free_gbs_pct"], "yes" if e["fits_free"] else "NO", e["paid_usd_month"]))
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dau", type=float, default=None, help="one row, with the breakdown")
    ap.add_argument("--hours", type=float, default=2.5, help="hours a player is online per day")
    ap.add_argument("--near", type=float, default=0.3, help="share of that time with another EqVoice player in range")
    ap.add_argument("--calls", type=float, default=6.0, help="new voice connections per hour spent near others")
    ap.add_argument("--ping-s", type=float, default=10.0)
    ap.add_argument("--keepalive-s", type=float, default=4.0)
    ap.add_argument("--move-msgs", type=float, default=500.0, help="movement-triggered proximity messages per hour spent near others")
    a = ap.parse_args(argv)
    kw = dict(hours=a.hours, near=a.near, calls=a.calls, ping_s=a.ping_s, keepalive_s=a.keepalive_s, move_msgs=a.move_msgs)
    if a.dau is not None:
        e = estimate(a.dau, **kw)
        print("%g daily players: %.0f requests/day (%.0f%% of the free 100,000), %.0f GB-s/day (%.0f%% of 13,000), paid plan about $%.2f/month" % (
            a.dau, e["requests"], e["free_requests_pct"], e["gbs"], e["free_gbs_pct"], e["paid_usd_month"]))
        print("requests by cause: " + ", ".join("%s %.0f" % kv for kv in e["split"].items()))
        return 0
    print(table([10, 50, 200, 500, 1000, 2000, 5000, 20000], **kw))
    print()
    print("Free plan holds about %s daily players with these assumptions (requests are the first limit to run out)." % format(max_free_dau(**kw), ","))
    print("Assumptions: %.1f h online per player per day, someone in range %.0f%% of that time, %.0f new voice connections per hour in range, "
          "ping every %.0f s, proximity keepalive every %.0f s, %.0f movement-triggered reports per hour in range, 2 sessions a day." % (
              a.hours, a.near * 100, a.calls, a.ping_s, a.keepalive_s, a.move_msgs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
