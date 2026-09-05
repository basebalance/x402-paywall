"""Hermetic regression test for the x402 paywall gate.
Replays the exact failures Felipe found:
 1. cookie-less clients (the agent norm) MUST hit the paywall after free tier
 2. deliberate x-session-id clients get counted, not a fresh bucket per request
 3. the gate MUST compile (catches indentation/splice bugs)
Run: python3 tests/ci_test.py  (exit 0 = pass)"""
import http.server, random, socket, subprocess, sys, threading, time, urllib.error, urllib.request, os

PAYTO = "0xe51e284b6Fbd870F43A2B112d40e48b34F8a7963"
GATE = os.path.join(os.path.dirname(__file__), "..", "x402-gate-serve.py")

class Upstream(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(b'{"ok":true}')
    def log_message(self, *a): pass

def free_port():
    # truly free: bind, note port, release, and verify nothing else grabs it
    while True:
        s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
        try:
            s = socket.create_connection(("127.0.0.1", p), 0.2); s.close(); continue  # taken -> retry
        except OSError:
            return p

def http_code(port, headers=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r: return r.status
    except urllib.error.HTTPError as e: return e.code

def wait_tcp(port, proc, timeout=15):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            raise SystemExit(f"gate died rc={proc.returncode}: {proc.stderr.read().decode()}")
        try:
            s = socket.create_connection(("127.0.0.1", port), 0.3); s.close()
            time.sleep(0.5)  # let handler thread spin up
            return
        except OSError:
            time.sleep(0.3)
    raise SystemExit(f"gate never listened on {port}")

def run_case(up, gp):
    gate = subprocess.Popen([sys.executable, GATE, "--upstream", f"http://127.0.0.1:{up}",
                             "--port", str(gp), "--bind", "127.0.0.1", "--payto", PAYTO,
                             "--free-tier", "5", "--free-window", "60"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        wait_tcp(gp, gate)
        banner = gate.stderr.readline().decode()  # our listening banner
        assert str(gp) in banner, f"wrong listener: {banner!r}"
        a = [http_code(gp) for _ in range(12)]
        assert a == [200]*5 + [402]*7, f"cookie-less wrong: {a}"
        b = [http_code(gp, {"x-session-id": "ci-agent"}) for _ in range(8)]
        assert b == [200]*5 + [402]*3, f"session wrong: {b}"
        c = [http_code(gp, {"x-session-id": "ci-other"}) for _ in range(3)]
        assert c == [200]*3, f"isolation wrong: {c}"
        return (a, b, c)
    finally:
        gate.terminate()
        try: gate.wait(timeout=5)
        except subprocess.TimeoutExpired: gate.kill()

UP, GP = free_port(), free_port()
up = http.server.HTTPServer(("127.0.0.1", UP), Upstream)
threading.Thread(target=up.serve_forever, daemon=True).start()
try:
    a, b, c = run_case(UP, GP)
    print("CI-PASS")
    print(f"  cookie-less  : {' '.join(map(str,a))}")
    print(f"  session agent: {' '.join(map(str,b))}")
    print(f"  other session: {' '.join(map(str,c))}")
finally:
    up.shutdown()
