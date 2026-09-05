#!/usr/bin/env python3
"""Smoke test for x402-gate-serve.py: proves the paywall actually fires.

Run from anywhere:
    python3 smoke_test.py /path/to/x402-gate-serve.py

Exit 0 if the gate serves free-tier requests to per-IP buckets and returns
402 past the limit. Exit 1 otherwise. Safe to run in CI — all listeners are
ephemeral (127.0.0.1, ports 18998/18999) and the gate is killed at the end.

Note for shell users: never `pkill -f x402-gate-serve.py` in the same command
that creates the process — the pattern matches the outer shell's own command
line and kills it. Use the PID returned here, or the bracket trick: pkill -f
"[x]402-gate-serve.py".
"""
import http.server
import socketserver
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

GATE = sys.argv[1] if len(sys.argv) > 1 else "x402-gate-serve.py"
BACKEND_PORT, GATE_PORT = 18998, 18999
FREE_TIER = 2  # requests per IP before 402


class Backend(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"jsonrpc":"2.0","id":1,"result":"ok"}')

    def log_message(self, *a):
        pass


def req(ip, headers_extra=None):
    h = {"Content-Type": "application/json", "CF-Connecting-IP": ip}
    if headers_extra:
        h.update(headers_extra)
    req_ = urllib.request.Request(
        f"http://127.0.0.1:{GATE_PORT}/rpc", data=b"{}", headers=h
    )
    try:
        r = urllib.request.urlopen(req_, timeout=5)
        return ("free", r.status, r.headers.get("x-quota-remaining"))
    except urllib.error.HTTPError as e:
        return ("paywall", e.code, None)
    except Exception as e:  # noqa: BLE001
        return ("error", repr(e), None)


def main():
    backend = socketserver.TCPServer(("127.0.0.1", BACKEND_PORT), Backend)
    threading.Thread(target=backend.serve_forever, daemon=True).start()

    gate = subprocess.Popen(
        [
            sys.executable, GATE,
            "--upstream", f"http://127.0.0.1:{BACKEND_PORT}/",
            "--payto", "0x0000000000000000000000000000000000000000",
            "--free-tier", str(FREE_TIER),
            "--port", str(GATE_PORT),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(2)
    if gate.poll() is not None:
        print(f"FAIL: gate exited early (rc={gate.returncode})")
        backend.shutdown()
        sys.exit(1)

    ok = True
    for ip in ("1.1.1.1", "2.2.2.2"):
        for n in range(1, FREE_TIER + 1):
            kind, status, remaining = req(ip)
            expected = "free"
            line = f"{ip} hit{n}: {kind} status={status}"
            if remaining is not None:
                line += f" remaining={remaining}"
            print(line)
            if kind != expected:
                ok = False
        kind, status, _ = req(ip)  # hit past the limit -> must 402
        print(f"{ip} hit{FREE_TIER+1}: {kind} status={status}")
        if kind != "paywall":
            ok = False

    gate.terminate()
    try:
        gate.wait(timeout=3)
    except subprocess.TimeoutExpired:
        gate.kill()
    backend.shutdown()
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()