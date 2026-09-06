## Install as a library

Any agent or script can install the client directly from GitHub:

```bash
pip install git+https://github.com/basebalance/x402-paywall
python -c "import x402_client; print(x402_client.__file__)"
```

The module is zero-dependency (stdlib `urllib`/`hashlib` only) and works on
Python 3.8+. For the interactive CLI and free-tier testing see `x402-client.py`.

# x402-paywall — charge USDC for any JSON-RPC/HTTP API in one file

Turn any upstream API into an x402 paywall: agents get N free requests/min/IP,
then receive a standard 402 challenge and pay USDC on Base to continue.

## Quick start
```sh
python3 x402-gate-serve.py --upstream https://mainnet.base.org \
  --payto 0xYOUR_WALLET --free-tier 5 --free-window 60
```
Or self-host with Docker: `docker compose up` (env: UPSTREAM, PAYTO).

## How paying works (agents)
1. POST / with a JSON-RPC body → free tier until quota exhausted.
2. Next request → `402` with an x402 challenge (amount, token, payTo).
3. Agent sends USDC on Base → retries with `x-paywall-tx: <txhash>`.
4. Read-only on-chain verification → paid quota grant (no accounts/keys).

Session key: `x-session-id` header first, else CDN-aware IP
(CF-Connecting-IP / X-Forwarded-For). Deliberate headers are counted;
cookie-less agent clients still hit the paywall (never a free-for-all).

## Verify a published release (run before you ship, and after you clone)
```sh
./verify-publish.sh                 # fresh clone + compile + behavior test
python3 tests/ci_test.py            # offline: cookie-less 5x200+7x402 repro
```
Both tests replay the exact failures found in review (indentation splice,
cookie-key paywall invisibility). Regression CI runs the same file.

## Files
- x402-gate-serve.py — the gate (stdlib only)
- consume.py — client-side x402 payment dance
- tests/ci_test.py — hermetic behavior regression
- verify-publish.sh — published-artifact proof
- Dockerfile / docker-compose.yml — one-command self-host
