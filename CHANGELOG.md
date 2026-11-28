# Changelog

## Unreleased: Studio v0.123 / GenVM v0.4 compatibility

Contract behaviour and the ABI (`artifacts/errata.abi.json`, byte-identical) are unchanged; the source and its hash are not.

- **Tooling pins**: `genlayer-py==0.19.0`, `genlayer-test==0.30.0`, `genvm-linter==0.11.0` (newest on PyPI). The runner is `v0.6.0-rc16` (`genvm-manager`) in `scripts/preflight.py`, `run.sh`, `.github/workflows/ci.yml` and `tests/direct/support.py`.
- **Contract**: header now depends on `py-genlayer:kzybg95x1gn21dw72m7hyg0maaap04n4nx55j9yt6fkdqxtz6bz0`. The v0.4 SDK moved or renamed what the contract imported: `import genlayer as gl`, `gl.contract.Contract`, `gl.storage.allow`, `gl.storage.TreeMap`, and `gl.vm.run_nondet` in place of `run_nondet_unsafe`. No `salt_nonce`, no `__receive__`, no decorator on `__init__`.
- **Release gate**: `genvm-linter` 0.11.0 cannot load this runner, so `preflight.py` runs `genvm-lint lint` plus SDK validation and ABI generation through the runner (`scripts/abi_schema.py`).
- **Live runner**: every `write_contract` and `deploy_contract` carries `fees`; each test account (stranger included) gets a 1 GEN fee buffer; withdrawals are verified net of the fee the caller pays instead of requiring an exact gain, which timed out after 240 s.
- **Tests**: 404 Direct Mode tests. The simulated chain now charges a fee on every transaction; new tests cover fee attachment, the simulation fallback, funding, a fee larger than any payout, and a payout that never arrives. Mocked model answers reach the SDK as text (see `tests/direct/conftest.py`).

## 0.2.0

Errata 0.1.0 merged with the strengths of the earlier Erratum prototype, keeping Errata's design (two verbatim passages, deterministic checks in code, exact validator agreement) as the base.

From Erratum:

- **Several findings per account** (up to three per bounty, one bond each). Findings are addressed by the index `commit_finding` returns: `withdraw_finding`, `reveal_finding`, `review_finding` and `get_finding` now take an index instead of a finder address. The same sealed commitment cannot be filed twice. `get_findings` replaces `get_finders` and returns every finding.
- **The errata sheet**: `get_errata()` lists the accepted defects (projected while open, settled after finalization). `get_standing()` now also works after finalization.
- **`live_e2e.py --probe-nonpayable`** and a live-fixture check in `preflight.py` (LF endings, every planned quote still matches).

Fixed or added while merging:

- `scripts/errata_tools.py seal` now refuses a mirror URL, address or bounty id the contract would reject or parse differently. Before, such a mirror was sealed into a commitment that could never be revealed, which forfeited the bond.
- Every non-payable write method now reverts when GEN is attached (`_refuse_value`). Direct Mode does not enforce this on its own, so the contract does; the live probe checks the network side.
- `preflight.py` tolerates a ", N warnings" pytest summary.
- The live scenario exercises the per-account cap and the forfeit path on the real chain (bounty B capacity 4, two extra sealed findings from one account).

Deliberately not taken from Erratum: severity tiers and model confidence inside the consensus path (see docs/DESIGN.md), and its single-quote report format (a defect here is a pair of passages).

ABI changes: see `artifacts/errata.abi.json`. Anything that called the 0.1.0 methods by finder address must switch to indexes.

## 0.1.0

First version: two-passage sealed findings, exact-agreement validators, mirror failover, pull-payment settlement.
