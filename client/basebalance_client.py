#!/usr/bin/env python3
"""
basebalance_client.py — single-file, stdlib-only client for basebalance.cloud.

The gateway is an x402-gated Base JSON-RPC endpoint with concurrent 5-RPC
failover and short-TTL caching:
  - Free tier: 10 req/min per IP (genuine public good, no keys)
  - Paid tier:  $0.50 USDC / 10k req / 24h via x402 (HTTP 402 challenge)

Drop-in for any agent that needs reliable Base chain access:
    python3 basebalance_client.py chainid
    python3 basebalance_client.py --self-test
    python3 basebalance_client.py eth_getBalance 0xE51E284b6Fbd870F43A2B112d40e48b34F8a7963 latest

If you want fully-automatic payment (set BASE_PRIVATE_KEY), it signs and
submits the USDC transfer itself. Without a key it prints the exact
challenge + instructions so any wallet can pay.

Zero dependencies: urllib + json + time only. Python 3.8+.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE_URL = os.environ.get("BASEBALANCE_URL", "https://basebalance.cloud")
USDC_CONTRACT = "0x833589fCD6eDb6E08f4c7C32D4f71b54bDA02913"
ACTUAL_PAYEE = "0xe51e284b6Fbd870F43A2B112d40e48b34F8a7963"


def rpc_call(method, params=None, tx_header=None, timeout=15):
    body = json.dumps({"jsonrpc": "2.0", "id": int(time.time() * 1000) % 2**31,
                       "method": method, "params": params or []}).encode()
    headers = {"Content-Type": "application/json"}
    if tx_header:
        headers["x-paywall-tx"] = tx_header
    req = urllib.request.Request(BASE_URL + "/rpc", data=body, headers=headers)
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.status, json.loads(r.read().decode()), dict(r.headers)
    except urllib.error.HTTPError as e:
        payload = e.read().decode()
        try:
            return e.code, json.loads(payload), dict(e.headers)
        except Exception:
            return e.code, {"raw": payload}, dict(e.headers)


def show_quota(headers):
    for k in ("x-quota-tier", "x-quota-used", "x-quota-limit",
              "x-quota-remaining", "x-quota-expiry", "x-quota-limited"):
        if k in headers:
            print(f"  {k}: {headers[k]}")


def pay_challenge(challenge, privkey):
    """Submit USDC transfer for the 402 challenge. Returns tx hash."""
    try:
        from eth_account import Account
        from eth_account._utils.signing import sign_transaction_dict  # noqa
    except ImportError:
        print("  eth-account not installed; payment requires it.")
        print("  pip install eth-account web3")
        return None
    try:
        from web3 import Web3
    except ImportError:
        print("  web3 not installed; payment requires it.")
        print("  pip install web3")
        return None

    w3 = Web3(Web3.HTTPProvider("https://mainnet.base.org"))
    acct = Account.from_key(privkey)
    amount_units = int(round(float(challenge["amount"]) * 10**6))
    to = Web3.to_checksum_address(challenge.get("payTo") or ACTUAL_PAYEE)
    usdc = Web3.to_checksum_address(challenge.get("token") or USDC_CONTRACT)
    # Minimal ERC20 transfer ABI
    transfer_fn = {
        "constant": False, "inputs": [
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"}],
        "name": "transfer", "outputs": [
            {"name": "", "type": "bool"}], "payable": False,
        "stateMutability": "nonpayable", "type": "function"}
    data = w3.eth.contract(address=usdc, abi=[transfer_fn]).encode_abi(
        "transfer", [to, amount_units])
    nonce = w3.eth.get_transaction_count(acct.address)
    gas_price = w3.eth.gas_price
    tx = {"to": usdc, "data": data, "value": 0, "nonce": nonce,
          "gas": 80000, "gasPrice": gas_price, "chainId": 8453}
    signed = acct.sign_transaction(tx)
    h = w3.eth.send_raw_transaction(signed.rawTransaction)
    print(f"  payment tx sent: {h.hex()}")
    print(f"  retrying with x-paywall-tx header...")
    return h.hex()


def handle(result_status, result, headers, privkey):
    if isinstance(result, dict) and result.get("error"):
        err = result["error"]
        if isinstance(err, dict) and err.get("challenge"):
            print(f"[402] payment required: {err['challenge']}")
            if not privkey:
                print("  [no BASE_PRIVATE_KEY] pay manually from any wallet:")
                print(f"    send {err['challenge']['amount']} USDC on Base to")
                print(f"    {err['challenge']['payTo']}")
                print(f"    then retry with x-paywall-tx: <tx hash>")
                return 1
            txh = pay_challenge(err["challenge"], privkey)
            if not txh:
                return 1
            time.sleep(3)  # let the tx land
            status2, result2, headers2 = rpc_call("eth_chainId", tx_header=txh)
            print(f"[retry] HTTP {status2}")
            show_quota(headers2)
            print(json.dumps(result2, indent=2))
            return 0
        print(f"[error] {err}")
        return 1
    show_quota(headers)
    print(json.dumps(result, indent=2))
    return 0


def self_test():
    print(f"== basebalance.cloud self-test ==")
    status, result, headers = rpc_call("eth_chainId")
    print(f"eth_chainId -> HTTP {status}")
    if status == 200:
        show_quota(headers)
        result_ok = (result.get("result") == "0x2105")
        print(f"chain OK (Base=0x2105): {result_ok}")
        return 0 if result_ok else 1
    return handle(status, result, headers, None)


def main():
    privkey = os.environ.get("BASE_PRIVATE_KEY")
    if len(sys.argv) < 2 or sys.argv[1] == "--self-test":
        return self_test()
    method = sys.argv[1]
    params = sys.argv[2:]
    status, result, headers = rpc_call(method, params)
    print(f"{method} -> HTTP {status}")
    return handle(status, result, headers, privkey)


if __name__ == "__main__":
    sys.exit(main())