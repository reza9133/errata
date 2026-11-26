# Deployment and live verification (Studionet)

Errata targets GenLayer **Studionet** and GenVM `v0.2.16`. The offline gate and the simulated-chain scenario run anywhere; the live run needs internet access, a public GitHub repository for the fixture, and about 75 minutes.

## 1. Prepare a Codespace

1. Create a **public** GitHub repository, push this directory to it, and open it in a Codespace (the dev container installs Python 3.12 and `requirements.txt`). The fixture must be public because validators fetch it from the internet.
2. Run the offline gate and write the ABI:

   ```bash
   python scripts/preflight.py --write-schema
   ```

   It checks source rules, runs `genvm-lint`, writes `artifacts/errata.abi.json`, and runs every Direct Mode test. Fix any failure before deploying; do not skip it.
3. Commit and push `fixtures/live/` unchanged. The runner pins the commit and refuses to start if the public bytes differ from the local file. `.gitattributes` stops git from rewriting line endings, which would change the hash.

## 2. Deploy

Deploy `contracts/errata.py` to Studionet with the GenLayer CLI or Studio (select Studionet), with no constructor arguments. Record the address.

```bash
bash run.sh deploy                         # optional: deploys through the runner, then runs everything
python scripts/live_e2e.py --deploy        # the same, without the offline gate
```

## 3. Run the live end-to-end test

The runner uses throwaway accounts (one publisher, nine finders and a stranger; the finder `india` seals three findings) and prints what each needs. It funds them through `sim_fundAccount` when the endpoint offers it.

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

`tests/direct/test_live_runner.py` runs this exact scenario (about 120 checks, plus the non-payable probe) against the real contract code with a simulated chain, the web and an idealised model. If it fails after you edit the contract, the live run would fail too.

## Known unverified assumptions

- `sim_fundAccount` may not exist on your Studionet endpoint; fund the printed addresses manually if the runner says so.
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
