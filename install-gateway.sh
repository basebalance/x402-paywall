#!/usr/bin/env bash
# install-gateway.sh — one-liner x402 RPC gateway deploy (zero dependencies).
#
#   curl -sL https://basebalance.cloud/x402client/install-gateway.sh | bash -s -- \
#       --upstream https://basebalance.cloud/rpc --payto 0xYOUR_ADDRESS
#
# Defaults after the first free-tier hit: paid requests go to the operator's
# payTo address (USDC on Base). Default upstream is the public basebalance.cloud
# gateway so the gate works the moment it starts.
#
# The installed gate is a plain python3 process supervised by a cron line.
# Logs: /var/log/x402-gate.log. Verify after install:
#   curl -s http://127.0.0.1:8003/rpc -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}'
set -euo pipefail

UPSTREAM="https://basebalance.cloud/rpc"
PAYTO=""
FREE_TIER=10
PORT=8003

while [[ $# -gt 0 ]]; do
  case "$1" in
    --upstream) UPSTREAM="$2"; shift 2;;
    --payto)    PAYTO="$2"; shift 2;;
    --free-tier) FREE_TIER="$2"; shift 2;;
    --port)     PORT="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

if [[ -z "$PAYTO" ]]; then
  echo "ERROR: --payto 0xADDRESS is required (your USDC receive address on Base)." >&2
  exit 2
fi
command -v python3 >/dev/null || { echo "python3 required" >&2; exit 1; }

TARGET_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/x402-gate"
mkdir -p "$TARGET_DIR"
GATE="$TARGET_DIR/x402-gate-serve.py"

# Prefer system copy if we were run from a path that has one next to us.
if [[ -x "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")/x402-gate-serve.py" ]]; then
  cp "$(dirname "$(readlink -f "$0")")/x402-gate-serve.py" "$GATE"
else
  curl -fsSL -o "$GATE" https://basebalance.cloud/x402client/x402-gate-serve.py \
    || curl -fsSL -o "$GATE" https://raw.githubusercontent.com/basebalance/x402-paywall/main/x402-gate-serve.py
fi

nohup python3 "$GATE" \
  --upstream "$UPSTREAM" \
  --payto "$PAYTO" \
  --free-tier "$FREE_TIER" \
  --port "$PORT" >> /var/log/x402-gate.log 2>&1 &
echo $! > /tmp/x402-gate.pid

# Cron supervision (5-min liveness): respawn if missing.
CRON=$(crontab -l 2>/dev/null || true)
if ! grep -q "$GATE" <<<"$CRON"; then
  ( echo "$CRON"; echo "*/5 * * * *  pgrep -f '[x]402-gate-serve.py.*--port $PORT' >/dev/null || nohup python3 $GATE --upstream $UPSTREAM --payto $PAYTO --free-tier $FREE_TIER --port $PORT >> /var/log/x402-gate.log 2>&1 &" ) | crontab -
fi

sleep 1
echo "x402 gateway live on port $PORT (pid $(cat /tmp/x402-gate.pid))"
echo "Verify:  curl -s http://127.0.0.1:$PORT/rpc -H 'Content-Type: application/json' -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"eth_blockNumber\",\"params\":[]}'"
echo "Logs: tail -f /var/log/x402-gate.log"