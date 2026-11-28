#!/usr/bin/env bash
# One-command runner for Errata.
#
#   bash run.sh                      offline gate only: lint, ABI schema, every Direct Mode test
#   bash run.sh live 0xYourAddress   offline gate, fixture check, then the full live end-to-end run
#   bash run.sh deploy               offline gate, deploy contracts/errata.py to Studionet, live run
#   bash run.sh check 0xYourAddress  offline gate and a fast tooling/fixture/contract reachability check
#
# Run it from the repository root (a GitHub Codespace works out of the box). The live
# fixtures must be pushed to a PUBLIC GitHub repository first; see docs/DEPLOYMENT.md.
set -euo pipefail
cd "$(dirname "$0")"

export GENVM_VERSION="${GENVM_VERSION:-v0.6.0-rc16}"
mode="${1:-offline}"
address="${2:-${ERRATA_ADDRESS:-}}"

echo "==> Installing pinned tooling"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

echo "==> Offline release gate (source rules, lint, ABI schema, all Direct Mode tests)"
python scripts/preflight.py

case "$mode" in
  offline)
    echo "Offline gate passed. Next: bash run.sh check <address>  or  bash run.sh live <address>"
    ;;
  check)
    [ -n "$address" ] || { echo "usage: bash run.sh check 0xYourAddress" >&2; exit 2; }
    python scripts/live_e2e.py --address "$address" --check-only
    ;;
  live)
    [ -n "$address" ] || { echo "usage: bash run.sh live 0xYourAddress" >&2; exit 2; }
    python scripts/live_e2e.py --address "$address"
    ;;
  deploy)
    python scripts/live_e2e.py --deploy
    ;;
  *)
    echo "unknown mode '$mode' (offline | check | live | deploy)" >&2
    exit 2
    ;;
esac
