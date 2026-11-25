from __future__ import annotations

import argparse
import os
import signal
import sys
import threading

from . import __version__
from .collector import Collector, Sampler, Thresholds
from .server import MonitorServer, is_loopback


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="server-monitor", description="Live CPU, memory, disk and network dashboard for one machine.")
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("--host", default="127.0.0.1", help="address to bind (default: 127.0.0.1, this machine only)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between samples (default: 2)")
    ap.add_argument("--history", type=int, default=150, help="samples kept for the charts (default: 150, about 5 minutes)")
    ap.add_argument("--warn", type=float, default=75.0, help="percent at which a metric turns amber")
    ap.add_argument("--crit", type=float, default=90.0, help="percent at which a metric turns red")
    ap.add_argument("--token", default=os.environ.get("MONITOR_TOKEN"), help="require this token (or set MONITOR_TOKEN); needed to bind a non-local address")
    ap.add_argument("--allow-host", action="append", default=[], help="extra Host header value to accept when no token is set")
    ap.add_argument("--no-processes", action="store_true", help="don't collect or show process names")
    args = ap.parse_args(argv)

    if args.interval < 0.5:
        ap.error("--interval must be at least 0.5 seconds")
    if not 0 < args.warn < args.crit <= 100:
        ap.error("thresholds must satisfy 0 < --warn < --crit <= 100")
    if not is_loopback(args.host) and not args.token:
        ap.error("binding a non-local address exposes process and system details; set --token (or MONITOR_TOKEN) first")

    collector = Collector(Thresholds(args.warn, args.crit), include_processes=not args.no_processes)
    sampler = Sampler(collector, args.interval, args.history)
    sampler.start()
    server = MonitorServer((args.host, args.port), sampler, args.token, set(args.allow_host))
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    shown = "localhost" if args.host in ("127.0.0.1", "::1") else args.host
    print(f"Dashboard on http://{shown}:{server.server_address[1]}/" + ("  (token required)" if args.token else ""), flush=True)
    stop.wait()
    server.shutdown()
    sampler.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
