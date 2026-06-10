#!/usr/bin/env bash
# First-time setup for standalone funding-arb project.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example — edit before live trading."
fi

mkdir -p data output runs/.state
touch data/.gitkeep runs/.state/.gitkeep

if ! python3 -c "import json" 2>/dev/null; then
  echo "Python 3 required."
  exit 1
fi

echo ""
echo "Setup complete."
echo "  Scan:      python3 scripts/cli/scan_funding_arbitrage.py --venues bitget,bybit,okx"
echo "  Scan unified: python3 scripts/cli/scan_unified_funding.py --verbose"
echo "  Paper run: python3 scripts/execution/run_cash_and_carry.py --config templates/config.cash_and_carry.btc.json --verbose"
echo "  Orch:      python3 scripts/cli/orchestrate_funding.py --venues bitget,bybit"
echo "  Tests:     python3 -m pytest scripts/tests/test_funding_arbitrage.py scripts/tests/test_reverse_margin.py -q"
