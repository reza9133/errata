"""Release gate: source rules, lint, ABI schema, and every Direct Mode test."""

from __future__ import annotations

import ast
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
CONTRACT = CONTRACTS / "errata.py"
SCHEMA = ROOT / "artifacts" / "errata.abi.json"
GENVM_VERSION = "v0.6.0-rc16"
WRITE_SCHEMA = "--write-schema" in sys.argv


SOURCES = sorted(CONTRACTS.glob("*.py"))
if SOURCES != [CONTRACT]:
    raise SystemExit(f"release gate requires only contracts/errata.py; found {[p.name for p in SOURCES]}")

raw = CONTRACT.read_bytes()
source = raw.decode("utf-8")
lines = source.splitlines()
version_match = re.search(r'^VERSION\s*=\s*"(\d+\.\d+\.\d+)"\s*$', source, re.MULTILINE)
header = re.fullmatch(r"# v(\d+\.\d+\.\d+)", lines[0]) if lines else None
if not version_match or not header or header.group(1) != version_match.group(1):
    raise SystemExit("source header and VERSION must match")
if len(lines) < 2 or not re.fullmatch(r'# \{ "Depends": "py-genlayer:[a-z0-9]+" \}', lines[1]):
    raise SystemExit("second line must pin the py-genlayer dependency")
for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
    if "==" not in line or "rc" in line.lower() or "dev" in line.lower():
        raise SystemExit(f"release dependency must be pinned to a stable version: {line}")

ast.parse(source, filename=str(CONTRACT))
subprocess.run([sys.executable, "-m", "compileall", "-q", "contracts", "tests", "scripts"],
               cwd=ROOT, check=True)

# The live scenario pins the fixture's hash on chain: it must keep LF line endings, and every quote the
# scenario plans to seal must still match it (or, for the decoys, still be unusable).
FIXTURE = ROOT / "fixtures" / "live" / "handbook.md"
fixture_bytes = FIXTURE.read_bytes()
if b"\r" in fixture_bytes:
    raise SystemExit("fixtures/live/handbook.md must use LF line endings: its hash is pinned on chain")
sys.path.insert(0, str(ROOT))
from scripts import live_e2e  # noqa: E402

live_e2e.load_fixtures("https://fixtures.invalid/live/", verify=False)

lint = shutil.which("genvm-lint") or shutil.which("genvm-lint.exe")
if not lint:
    candidate = Path(sys.executable).with_name("genvm-lint.exe" if os.name == "nt" else "genvm-lint")
    if candidate.exists():
        lint = str(candidate)
if not lint:
    raise SystemExit("genvm-lint is required; install requirements.txt")

env = os.environ.copy()
env["GENVM_VERSION"] = GENVM_VERSION
# genvm-linter 0.11.0 (newest on PyPI) cannot load the v0.4-era runner that genlayer-test 0.30.0 pins, so
# `genvm-lint check` and `genvm-lint schema` fail with E101 for a contract that is fine. The static rules
# (`genvm-lint lint`) still work; SDK validation and the ABI come from the runner's own SDK instead
# (scripts/abi_schema.py). Switch back to `check` and `schema` once a genvm-linter supports this runner.
subprocess.run([lint, "lint", str(CONTRACT), "--json"], cwd=ROOT, check=True, env=env)

with tempfile.TemporaryDirectory() as temp_dir:
    generated = Path(temp_dir) / "errata.abi.json"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "abi_schema.py"), str(CONTRACT),
                    "--output", str(generated), "--runner", GENVM_VERSION],
                   cwd=ROOT, check=True, env=env)
    if WRITE_SCHEMA:
        SCHEMA.parent.mkdir(exist_ok=True)
        SCHEMA.write_bytes(generated.read_bytes())
        print(f"wrote {SCHEMA.relative_to(ROOT)}")
    elif SCHEMA.exists() and generated.read_bytes() != SCHEMA.read_bytes():
        raise SystemExit("generated ABI differs from tracked artifacts/errata.abi.json "
                         "(run: python scripts/preflight.py --write-schema)")
    schema_state = "matches tracked file" if SCHEMA.exists() else "generated OK (no tracked copy yet)"

test_env = os.environ.copy()
test_env["GENVM_VERSION"] = GENVM_VERSION
test = subprocess.run(
    [sys.executable, "-m", "pytest", "tests/direct", "-q"],
    cwd=ROOT, capture_output=True, text=True, env=test_env,
)
print(test.stdout, end="")
print(test.stderr, end="", file=sys.stderr)
if test.returncode:
    raise SystemExit(test.returncode)
summary = test.stdout + "\n" + test.stderr
# pytest may append ", N warnings" to the summary line; tolerate that, but never a skip or a failure.
match = re.search(
    r"(?:^|\s)(\d+) passed(?:,\s*(\d+) skipped)?(?:,\s*(\d+) failed)?(?:,\s*\d+ warnings?)?\s+in\s",
    summary,
)
if not match:
    raise SystemExit("pytest summary count could not be verified")
passed, skipped, failed = (int(value or 0) for value in match.groups())
if not passed or skipped or failed:
    raise SystemExit(f"release gate requires passing tests and zero skips: {passed=}, {skipped=}, {failed=}")

print(f"contract_version={version_match.group(1)}")
print(f"contract_sha256={hashlib.sha256(raw).hexdigest()}")
print(f"contract_source_bytes={len(raw)}")
print(f"live_fixture_sha256={hashlib.sha256(fixture_bytes).hexdigest()}")
print(f"direct_mode_tests={passed}")
print(f"tests_skipped={skipped}")
print(f"tests_failed={failed}")
print("deployable_contract_sources=1")
print("lint=PASS")
print("sdk_validation=PASS")
print(f"schema={schema_state}")
print("preflight=PASS")
