---
name: x402-consume-basebalance
description: Client-side x402 dance to consume the basebalance.cloud RPC gateway — free tier first, $0.50 USDC per 10k requests when you need more. For agents that need Base chain JSON-RPC.
---

# Consume basebalance.cloud (x402 RPC gateway)

Base chain JSON-RPC access from `https://basebalance.cloud` — free tier first, paid only when you exceed it.

## Base URL
`https://basebalance.cloud`

## Endpoint
`POST /rpc` with a standard JSON-RPC body (eth_* methods: eth_call, eth_getBalance, eth_blockNumber, eth_chainId, eth_getTransactionByHash, eth_getTransactionReceipt, eth_getCode). Batches supported: POST a JSON-RPC array, one quota unit per element (free tier max 20 elements, paid max 200).

## Step 1 — Try free tier
```python
import json, urllib.request
body = json.dumps({"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}).encode()
req = urllib.request.Request("https://basebalance.cloud/rpc", data=body,
    headers={"Content-Type":"application/json",
             "User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"})
try:
    r = urllib.request.urlopen(req, timeout=10)
    print(r.status, r.read().decode())  # free tier: 10 req/min/IP
except urllib.error.HTTPError as e:
    print(e.code, e.read().decode())    # 402 => payment required (see Step 2)
```
Free tier: 10 requests/min per IP. Response headers tell you your state: `x-quota-tier: free|paid`, `x-quota-used`, `x-quota-limit`, `x-quota-remaining`, `x-quota-expiry`.

## Step 2 — On 402, read the challenge
```json
{"error":"402 payment required","challenge":{
  "amount": "0.50", "currency": "USDC", "network": 8453,
  "payTo": "0xe51e284b6Fbd870F43A2B112d40e48b34F8a7963",
  "scheme": "USDC", "token": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"}}
```
$0.50 USDC buys 10,000 requests (or 24h, whichever first).

## Step 3 — Pay
Send a USDC transfer on Base (chain 8453) to `payTo` for `amount` (500000 = 0.50 USDC in 6-decimal units). If you have an x402 tool (e.g. `x402_fetch`), pass the URL and it signs and retries on 402 automatically.

## Step 4 — Retry with proof
Retry the same RPC call with header `x-paywall-tx: <your tx hash>`. The gateway verifies the tx on-chain (read-only) and promotes you to paid quota until quota exhausted or 24h expiry.

## Why pay instead of using a public RPC?
- Concurrent 5-RPC failover: survives public-RPC rate limits and downtime (your calls stay green when mainnet.base.org is red).
- Short-TTL cache: repeated eth_call/eth_getBalance on hot addresses are served without upstream round-trips.
- Transparent: every payment verified on-chain, no accounts/API keys. Public append-only ledger https://basebalance.cloud/ledger.json, live health https://basebalance.cloud/public-health.json, counters https://basebalance.cloud/stats.json.
- Honest pricing: the free tier deliberately stays open; you only pay when you need more than 10 req/min.

## OpenAI-compatible surface
`POST /v1/chat/completions` (Base URL https://basebalance.cloud/v1, api_key "unused") — same x402 gating, intent interpreter routes to live Base RPC.
