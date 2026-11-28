"""Validate a contract and extract its ABI schema with the pinned GenVM runner's own SDK.

Why this exists: genvm-linter 0.11.0 (the newest release on PyPI) still expects the GenVM v0.2/v0.3
runner layout (``.tar`` runner archives and ``genlayer.py.get_schema``). The v0.4-era runner that
``genlayer-test==0.30.0`` pins (``GENVM_VERSION=v0.6.0-rc16``) ships ``.zip`` runners and moved the
schema generator to ``genlayer._internal.get_schema``, so ``genvm-lint check`` and ``genvm-lint schema``
fail with E101 "Failed to load SDK" even though the contract is fine. ``genvm-lint lint`` (the static
rules) still works and stays in the release gate.

This script loads the contract exactly as Direct Mode does, with the same runner, and calls the SDK's
``Contract.__get_schema__()``. That is the very function the GenVM host uses to introspect a deployed
contract, so it rejects invalid storage types, forbidden special methods and bad signatures. The output
is written in the same format ``genvm-lint schema`` used (``json.dumps(..., indent=2)``).

When a genvm-linter release supports this runner, ``preflight.py`` can go back to ``genvm-lint check``
and ``genvm-lint schema`` and this file can be deleted.

Usage: python scripts/abi_schema.py contracts/errata.py [--output artifacts/errata.abi.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

DEFAULT_RUNNER = "v0.6.0-rc16"


def extract_schema(contract: Path, runner: str) -> dict:
    from gltest.direct.loader import load_contract_class
    from gltest.direct.vm import VMContext

    vm = VMContext()
    with vm.activate():
        contract_class = load_contract_class(contract.resolve(), vm, runner)
        return json.loads(contract_class.__get_schema__())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("contract", type=Path)
    parser.add_argument("--output", type=Path, help="write the schema here instead of stdout")
    parser.add_argument("--runner", default=os.environ.get("GENVM_VERSION", DEFAULT_RUNNER))
    args = parser.parse_args()

    try:
        schema = extract_schema(args.contract, args.runner)
    except Exception as exc:  # noqa: BLE001 - report any SDK rejection as a failed gate
        print(f"SDK validation failed for {args.contract}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    methods = schema.get("methods", {})
    if not methods:
        print(f"SDK validation failed for {args.contract}: schema has no public methods", file=sys.stderr)
        return 1
    text = json.dumps(schema, indent=2)
    if args.output:
        args.output.write_text(text, encoding="utf-8", newline="\n")
    else:
        print(text)
    print(f"sdk_validation=PASS runner={args.runner} methods={len(methods)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
