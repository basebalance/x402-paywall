#!/usr/bin/env python3
"""x402-client.py — reference x402 client: free tier first, pay only on 402.

Consumes any x402-gated API (e.g. https://basebalance.cloud/rpc).
- Tries the free tier with a browser-like User-Agent.
- On HTTP 402, parses the challenge, pays USDC on Base (chain 8453),
  retries with `x-paywall-tx` header.
- Dependency-light: stdlib + eth_account. If eth_account is missing it
  prints the payment instructions so any wallet can complete the dance.

Usage:
    python3 x402-client.py "<json-rpc body>" [private_key]
Example:
    python3 x402-client.py '{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}'
"""
import json
import sys
import time
import urllib.request
import urllib.error

URL = "https://basebalance.cloud/rpc"
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bDA02913"  # Base USDC
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"


def _call(body: dict, txhash: str = "") -> tuple[int, dict, dict]:
    headers = {"Content-Type": "application/json", "User-Agent": UA}
    if txhash:
        headers["x-paywall-tx"] = txhash
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.headers, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.headers, json.loads(e.read().decode())
        except Exception:
            return e.code, e.headers, {}


def _parse_challenge(resp: dict) -> dict:
    ch = resp.get("challenge") or resp.get("error", {}).get("challenge", {})
    if not ch:
        raise SystemExit("402 but no x402 challenge in body: " + json.dumps(resp)[:300])
    return ch


def _pay(privkey: str, amount: str, pay_to: str, rpc: str = "https://mainnet.base.org") -> str:
    """Send USDC transfer (approve-free) and return tx hash once mined."""
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct  # noqa
    except ImportError:
        raise SystemExit(
            "eth_account not installed. Pay manually: send %s USDC on Base (8453) to %s, "
            "then retry with header 'x-paywall-tx: <txhash>'." % (amount, pay_to))
    w3 = None
    try:
        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider(rpc))
    except ImportError:
        pass  # fall back to raw signed tx via eth_account only
    acct = Account.from_key(privkey)
    amt = int(float(amount) * 1_000_000)  # USDC 6 decimals
    # transfer(address,uint256) = 0xa9059cbb + padded to + padded value
    data = "0xa9059cbb" + pay_to[2:].rjust(64, "0") + format(amt, "064x")
    if w3 is not None:
        to = w3.to_checksum_address(USDC)
        nonce = w3.eth.get_transaction_count(acct.address)
        gas = w3.eth.estimate_gas({"from": acct.address, "to": to, "data": data})
        base = w3.eth.get_block("latest")["baseFeePerGas"]
        tx = {"to": to, "data": data, "nonce": nonce, "gas": gas * 2,
              "maxFeePerGas": int(base * 2.2), "maxPriorityFeePerGas": 100_000,
              "chainId": 8453, "type": 2}
    else:  # legacy via eth_account signed tx (no gas estimation)
        tx = {"to": USDC, "data": data, "nonce": 0, "gas": 100_000,
              "gasPrice": 1_000_000, "chainId": 8453}
    signed = acct.sign_transaction(tx)
    if w3 is None:
        raise SystemExit("web3 not installed; signed ready (hex below) — broadcast manually.\n" +
                         signed.raw_transaction.hex()[:80] + "...")
    h = w3.eth.send_raw_transaction(signed.raw_transaction)
    print("payment tx:", h.hex())
    for _ in range(30):  # ~30s wait
        rec = w3.eth.get_transaction_receipt(h)
        if rec is not None:
            if rec["status"] != 1:
                raise SystemExit("payment tx reverted: " + h.hex())
            return h.hex()
        time.sleep(1)
    raise SystemExit("payment tx not mined in 30s: " + h.hex())


def main() -> None:
    body = json.loads(sys.argv[1]) if len(sys.argv) > 1 else \
        {"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []}
    key = sys.argv[2] if len(sys.argv) > 2 else ""
    status, headers, resp = _call(body)
    qh = {k: v for k, v in headers.items() if k.startswith("x-quota")}
    print("free-tier ->", status, "| quota:", qh or "n/a")
    if status == 200:
        print("OK:", json.dumps(resp)[:200])
        return
    if status != 402:
        raise SystemExit("unexpected status %s: %s" % (status, json.dumps(resp)[:300]))
    ch = _parse_challenge(resp)
    print("challenge:", json.dumps(ch))
    if not key:
        raise SystemExit("402 payment required. Re-run with a private key to auto-pay, "
                         "or pay %s USDC to %s and retry with x-paywall-tx." %
                         (ch.get("amount"), ch.get("payTo")))
    txh = _pay(key, ch["amount"], ch["payTo"])
    status, headers, resp = _call(body, txh)
    qh = {k: v for k, v in headers.items() if k.startswith("x-quota")}
    print("after-payment ->", status, "| quota:", qh or "n/a")
    print("OK:", json.dumps(resp)[:200])


if __name__ == "__main__":
    main()