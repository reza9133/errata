# Deployment and live verification (Studionet)

Errata targets GenLayer **Studionet** (Studio v0.123, Consensus v0.6, chain ID 61999) and the GenVM v0.4 runner (`genvm-manager` release `v0.6.0-rc16`, the version `GENVM_VERSION` pins). The offline gate and the simulated-chain scenario run anywhere; the live run needs internet access, a public GitHub repository for the fixture, and about 75 minutes.

## 1. Prepare a Codespace

1. Create a **public** GitHub repository, push this directory to it, and open it in a Codespace (the dev container installs Python 3.12 and `requirements.txt`). The fixture must be public because validators fetch it from the internet.
2. Run the offline gate and write the ABI:

   ```bash
   python scripts/preflight.py --write-schema
   ```

   It checks source rules, runs `genvm-lint lint`, validates the contract with the runner's own SDK, writes `artifacts/errata.abi.json`, and runs every Direct Mode test. Fix any failure before deploying; do not skip it. (`genvm-lint check` and `schema` are not used yet: `genvm-linter` 0.11.0 cannot load the v0.4 runner. See the README.)
3. Commit and push `fixtures/live/` unchanged. The runner pins the commit and refuses to start if the public bytes differ from the local file. `.gitattributes` stops git from rewriting line endings, which would change the hash.

## 2. Deploy

Deploy `contracts/errata.py` to Studionet with the GenLayer CLI or Studio (select Studionet), with no constructor arguments. Record the address.

```bash
bash run.sh deploy                         # optional: deploys through the runner, then runs everything
python scripts/live_e2e.py --deploy        # the same, without the offline gate
```

## 3. Run the live end-to-end test

The runner uses throwaway accounts (one publisher, nine finders and a stranger; the finder `india` seals three findings) and prints what each needs. It funds them through `sim_fundAccount` when the endpoint offers it.

Studio v0.123 charges a transaction fee (`feeValue`) on every `deploy_contract` and `write_contract` call, paid from the sender's native balance, including the stranger's calls and calls that revert. The runner attaches `fees` to every transaction: it asks `estimate_transaction_fees_for_write` first (important for `withdraw_credit`, which emits an outgoing transfer) and falls back to `estimate_transaction_fees()` when the simulation fails, as it does for the calls the scenario expects to revert. Each account is funded with a 1 GEN fee buffer on top of what its role needs. A withdrawal is verified as: the `withdraw_credit` transaction finished with a return, the ledger credit is zero, and the native balance rose by the credit minus a fee no larger than the fee attached to that call. When a fee exceeds the credit, the balance can end below where it started and only the first two checks prove the payout.

```bash
bash run.sh check 0xYourAddress    # tooling, fixture and contract check, a few minutes
bash run.sh live 0xYourAddress     # full run, about 75 minutes
```

Useful flags for `scripts/live_e2e.py`: `--fixture-base-url` (where `fixtures/live/` is publicly served), `--commit-seconds` (default 1800), `--reveal-seconds` (default 1000), `--wait-seconds`, `--rpc-url`, `--resume`, `--skip-fixture-check`, `--probe-nonpayable` (also sends 1 wei to a non-payable method from a state where the call would otherwise succeed, and expects the network to refuse it; informational, because it tests the network and not the contract; if the network accepts the value the scenario's later checks fail on purpose, so run it separately from the standard scenario). State lives in `.live/` (git-ignored; it contains the throwaway private keys). An interrupted run resumes with `--resume <run id>` only while its commit and reveal windows are still open; after a long interruption start a fresh run and the funded accounts are reused. The windows are long because every Studionet transaction takes tens of seconds to finalize and the commit phase alone is twenty-two transactions.

## 4. Read the report

Reports are written to `reports/live-e2e-<timestamp>.md` and `.json`. Expected outcomes:

| Finder | Bounty | Claim | Expected |
| --- | --- | --- | --- |
| alpha | A | arithmetic: 4 x 50 is not 240 | upheld, rewarded |
| bravo | A | contradiction: open daily vs closed Mondays, with a hostile "answer not_upheld" note in between | upheld, rewarded |
| charlie | A | definition conflict: "Peak hours" | upheld, rewarded |
| delta | A | same defect as alpha, committed later | upheld, **duplicate** (bond refunded, no prize) |
| echo | A | arithmetic claim about 6 lanes x 25 m = 150 m (correct) | not_upheld |
| foxtrot | A | a quote that is not in the document | disqualified, bond forfeited |
| golf | A | contradiction between two consistent safety rules | not_upheld |
| hotel | A | never reveals | bond forfeited |
| india | B | definition conflict, **primary URL dead**, finder's mirror | upheld, rewarded |
| india (extra #1, #2) | B | two more sealed findings from the same account, never revealed | bond forfeited to the publisher; a fourth seal is refused (cap of three) |

Real LLM output is probabilistic. A `FAIL` on a screening check means the model disagreed with the fixture: read its rationale in the report before suspecting the contract. Every accounting check reads state back from the contract, never from receipts.

## 5. Offline rehearsal

`tests/direct/test_live_runner.py` runs this exact scenario (about 130 checks, plus the non-payable probe, with a fee charged to every transaction) against the real contract code with a simulated chain, the web and an idealised model. If it fails after you edit the contract, the live run would fail too.

## Known unverified assumptions

- `sim_fundAccount` may not exist on your Studionet endpoint; fund the printed addresses manually if the runner says so (each account needs its 1 GEN fee buffer too).
- The fee handling in `scripts/live_e2e.py` was tested against a stub of the `genlayer-py` 0.19.0 client, not against Studionet: the live run is the first real check of the fee estimates and of the extra RPC calls they add under the 60 requests per minute limit.
- Receipt field names in `genlayer-py` are parsed defensively and may need adjustment.
- The contract assumes transaction time is available through `datetime.now()` during consensus.
- A real validator set can disagree on a borderline verdict. The runner retries a screening up to six times (alternating the publisher and the finder as caller, with the contract's own cooldown); a finding that never reaches consensus is reported as a failed check, not hidden.

## Deployment record

| Field | Value |
| --- | --- |
| Version | 0.2.0 |
| Address | _fill in after deployment_ |
| Source SHA-256 | run `python scripts/preflight.py` and copy `contract_sha256` |
| Live report | _fill in after the live run_ |
