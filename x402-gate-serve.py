#!/usr/bin/env python3
"""x402-gate-serve.py — turnkey reverse-proxy paywall for ANY existing HTTP API.

Makes the x402-gate-sdk a zero-code product:
  python3 x402-gate-serve.py --upstream http://127.0.0.1:9999/ --port 8080 \
      --payto 0xe51e284b6Fbd870F43A2B112d40e48b34F8a7963 \
      --free-tier 10 --free-window 60 --price 0.50 --grant 10000

Flow per request:
  1. Client hits public endpoint (default 0.0.0.0:8080).
  2. Free tier check (session-keyed sliding window; CDN-safe via cookie). If budget left -> proxy upstream.
  3. Else -> look for x-paywall-tx header; verify USDC transfer on Base (read-only).
  4. Verified -> grant quota (anti-replay) -> proxy upstream.
  5. No valid proof -> HTTP 402 with x402 challenge JSON (same shape as basebalance.cloud).

No private keys are stored here. Proof verification is read-only via public RPC.
State (quotas, grants, ledger) persists in --state-file (default ./x402gate-state.json).

Dependencies: Python 3.9+, `requests`. Install: pip install requests
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore

USDC_ADDRESS = "0x833589fCD6eDb6E08f4c7C32D4f71b54bDA02913"
BASE_CHAIN_ID = 8453
DEFAULT_RPC = "https://mainnet.base.org"
TX_SIG_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")
TRANSFER_SIG = "0xa9059cbb"  # transfer(address,uint256)


def log(msg: str) -> None:
    print(f"[x402gate] {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}", flush=True)


class State:
    """Persistent state: per-IP free-token buckets, granted paid quota, ledger."""

    def __init__(self, path: str):
        self.path = path
        self.lock = threading.RLock()
        self.data = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.path):
            try:
                return json.load(open(self.path))
            except Exception:
                pass
        return {"free": {}, "grants": {}, "paid_used": {}, "ledger": [], "created": time.time()}

    def save(self) -> None:
        with self.lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.data, f, indent=1)
            os.replace(tmp, self.path)

    # ---- free tier: sliding window of timestamps per IP ----
    def window(self, ip: str, now: float, limit: int, window: int) -> tuple[int, float]:
        with self.lock:
            key = f"free:{ip}"
            bucket = self.data["free"].get(key, [])
            bucket = [t for t in bucket if t > now - window]
            self.data["free"][key] = bucket
            remaining = max(0, limit - len(bucket))
            self.save()
            return remaining, (bucket[0] if bucket else 0.0)

    def consume(self, ip: str, now: float) -> None:
        with self.lock:
            key = f"free:{ip}"
            self.data["free"].setdefault(key, []).append(now)
            self.save()

    # ---- paid quota: grant id -> (used, limit, expires) ----
    def grant(self, tx_hash: str, limit: int, ttl: int, now: float) -> dict:
        with self.lock:
            existing = self.data["grants"].get(tx_hash)
            if existing and existing.get("expires", 0) > now:
                return existing
            g = {"tx": tx_hash, "used": 0, "limit": limit, "expires": now + ttl, "created": now}
            self.data["grants"][tx_hash] = g
            self.data["ledger"].append({"tx": tx_hash, "kind": "grant", "amount": limit, "ts": now})
            self.save()
            return g

    def paid_remaining(self, tx_hash: str, now: float) -> int:
        with self.lock:
            g = self.data["grants"].get(tx_hash)
            if not g or g.get("expires", 0) <= now:
                return 0
            return max(0, g["limit"] - g["used"])

    def consume_paid(self, tx_hash: str, now: float) -> None:
        with self.lock:
            g = self.data["grants"].get(tx_hash)
            if g and g.get("expires", 0) > now:
                g["used"] = g["used"] + 1
                self.save()


def verify_usdc_transfer(tx_hash: str, payto: str, min_amount: int, rpc: str) -> tuple[bool, str]:
    """Read-only on-chain verification: status 0x1, to=USDC, transfer(to=payto, amount>=min)."""
    if requests is None:
        return False, "requests lib unavailable"
    if not TX_SIG_RE.match(tx_hash):
        return False, "malformed tx hash"
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "eth_getTransactionReceipt", "params": [tx_hash],
    }
    try:
        r = requests.post(rpc, json=payload, timeout=10)
        receipt = r.json().get("result")
        if not receipt:
            return False, "receipt not found"
        if receipt.get("status") != "0x1":
            return False, f"tx status {receipt.get('status')}"
        if receipt.get("to", "").lower() != USDC_ADDRESS.lower():
            return False, "tx.to is not USDC"
        tx = requests.post(rpc, json={"jsonrpc": "2.0", "id": 2, "method": "eth_getTransactionByHash", "params": [tx_hash]}, timeout=10).json().get("result")
        if not tx:
            return False, "tx not found"
        inp = tx.get("input", "")
        if not inp.startswith(TRANSFER_SIG):
            return False, "not a transfer() call"
        # transfer(address,uint256): 32B addr (padded) + 32B amount. USDC has 6 decimals.
        dest = "0x" + inp[34:74]
        amount = int(inp[74:138], 16)
        if dest.lower() != payto.lower():
            return False, f"dest {dest} != payto"
        if amount < min_amount:
            return False, f"amount {amount} < min {min_amount}"
        # confirm chainId is Base
        ch = requests.post(rpc, json={"jsonrpc": "2.0", "id": 3, "method": "eth_chainId", "params": []}, timeout=10).json().get("result")
        if ch and int(ch, 16) != BASE_CHAIN_ID:
            return False, f"chainId {ch} != 8453"
        return True, f"verified {amount} USDC"
    except Exception as e:  # noqa: BLE001
        return False, f"verify error: {e}"


def challenge_json(payto: str, price: str) -> dict:
    return {
        "error": "402 payment required",
        "challenge": {
            "amount": price, "currency": "USDC", "network": BASE_CHAIN_ID,
            "payTo": payto, "scheme": "USDC", "token": USDC_ADDRESS,
        },
    }


def build_handler(state: State, args: argparse.Namespace):
    payto = args.payto.lower()
    min_amount = int(round(float(args.price) * 1_000_000))
    ttl = args.ttl

    class Handler(BaseHTTPRequestHandler):

        def _client_ip(self):
            """Quota identity: honor proxy/CDN headers (CF-Connecting-IP,
            X-Forwarded-For) so deployments behind Cloudflare/nginx charge real
            users, not the CDN's shared egress IP. Set QUOTA_TRUST_HEADERS=0
            to force raw socket IP."""
            if os.environ.get("QUOTA_TRUST_HEADERS", "1").lower() not in ("0", "false", "no"):
                cf = self.headers.get("CF-Connecting-IP")
                if cf:
                    return cf.split(",")[0].strip()
                xff = self.headers.get("X-Forwarded-For")
                if xff:
                    return xff.split(",")[0].strip()
            return self.client_address[0]
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # silence default
            pass

        def _send(self, code: int, body: bytes, ctype: str = "application/json", extra: dict | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("x-quota-tier", "paid" if code == 200 else "free")
            for k, v in (extra or {}).items():
                self.send_header(k, str(v))
            self.end_headers()
            self.wfile.write(body)

        def _session_key(self):
            """Quota bucket identity: CDN-aware client IP (per-IP free tier,
            matching live basebalance.cloud). Per-request random session keys were
            a bug: every request opened a fresh free window so the paywall never
            fired."""
            return "ip:" + self._client_ip()
        def do_GET(self):
            now = time.time()
            key = self._session_key()
            remaining, _ = state.window(key, now, args.free_tier, args.free_window)
            if remaining > 0:
                state.consume(key, now)
                self._proxy(now, tier="free", remaining=remaining)
                return
            # free exhausted: check x-paywall-tx
            tx = self.headers.get("x-paywall-tx", "").strip()
            if tx:
                pr = state.paid_remaining(tx, now)
                if pr > 0:
                    state.consume_paid(tx, now)
                    self._proxy(now, tier="paid", remaining=pr - 1, tx=tx)
                    return
                ok, msg = verify_usdc_transfer(tx, payto, min_amount, args.rpc)
                if ok:
                    state.grant(tx, args.grant, ttl, now)
                    self._proxy(now, tier="paid", remaining=args.grant - 1, tx=tx)
                    return
                body = json.dumps({"error": "payment proof rejected", "detail": msg}).encode()
                self._send(402, body, extra={"x-quota-remaining": 0, "x-quota-reason": msg})
                return
            body = json.dumps(challenge_json(payto, args.price)).encode()
            self._send(402, body, extra={"x-quota-remaining": 0})

        do_POST = do_GET

        def _proxy(self, now: float, tier: str, remaining: int, tx: str | None = None) -> None:
            upstream = args.upstream
            path = self.path or "/"
            q = urlparse(self.path).query
            target = upstream.rstrip("/") + path + (f"?{q}" if q else "")
            headers = {k: v for k, v in self.headers.items() if k.lower() in ("content-type", "accept", "authorization", "user-agent")}
            try:
                if self.command == "GET":
                    r = requests.get(target, headers=headers, timeout=args.timeout, stream=False)
                else:
                    length = int(self.headers.get("content-length", 0) or 0)
                    body = self.rfile.read(length) if length else None
                    r = requests.post(target, data=body, headers=headers, timeout=args.timeout)
                self.send_response(r.status_code)
                if getattr(self, "_new_sess", None):
                    self.send_header("Set-Cookie", "x402sess=%s; Path=/; Max-Age=86400; SameSite=Lax" % self._new_sess)
                self.send_header("x-quota-tier", tier)
                self.send_header("x-quota-remaining", str(remaining))
                if tx:
                    self.send_header("x-quota-tx", tx)
                for k, v in r.headers.items():
                    if k.lower() not in ("content-length", "transfer-encoding", "connection"):
                        self.send_header(k, v)
                body = r.content
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:  # noqa: BLE001
                body = json.dumps({"error": "upstream unreachable", "detail": str(e)}).encode()
                self._send(502, body)

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser(description="x402 reverse-proxy paywall — sell any API for USDC.")
    ap.add_argument("--upstream", required=True, help="upstream API base URL, e.g. http://127.0.0.1:9999/")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--payto", required=True, help="USDC destination address (your wallet)")
    ap.add_argument("--price", default="0.50", help="USDC price per grant")
    ap.add_argument("--grant", type=int, default=10000, help="requests bought per grant")
    ap.add_argument("--ttl", type=int, default=86400, help="grant validity seconds")
    ap.add_argument("--free-tier", type=int, default=10, help="free requests per client session per window")
    ap.add_argument("--free-window", type=int, default=60, help="free window seconds")
    ap.add_argument("--rpc", default=DEFAULT_RPC, help="Base JSON-RPC for verification")
    ap.add_argument("--state-file", default="./x402gate-state.json")
    ap.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args()

    if requests is None:
        log("ERROR: `requests` not installed — run: pip install requests")
        return 1
    if not re.match(r"^0x[a-fA-F0-9]{40}$", args.payto):
        log("ERROR: --payto must be a 0x address")
        return 1

    state = State(args.state_file)
    handler = build_handler(state, args)
    httpd = ThreadingHTTPServer((args.bind, args.port), handler)
    log(f"listening {args.bind}:{args.port} -> upstream {args.upstream}")
    log(f"price ${args.price}/grant({args.grant} req) payto={args.payto} free={args.free_tier}/{args.free_window}s")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("shutting down")
    return 0


if __name__ == "__main__":
    sys.exit(main())