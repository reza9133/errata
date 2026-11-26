# Errata

**Errata is a standalone GenLayer Intelligent Contract for sealed-finding errata bounties on hash-pinned documents.** A publisher escrows a budget and pins a document by SHA-256. Readers post a bond and a sealed commitment to a claimed *internal* defect (one account may seal up to three): two verbatim passages of the document and a declared defect kind (arithmetic, contradiction, or definition conflict). After the sealing window closes they reveal; the contract recomputes every commitment. GenLayer validators then independently fetch the pinned document, check **in code** that both quotes occur exactly once and do not overlap, and show **only the two quoted passages** to the model. Deterministic code, never model prose, decides who is paid: the earliest upheld finding per defect, until the budget runs out.

Think of Knuth's reward checks for errors in his books, with a trustless judge, no way to copy a competitor's finding, and no way for the publisher to quietly change the document or the rules afterwards.

It is a reusable primitive, not a proofreading service. It only judges whether a document is consistent with **itself**; it does not check the document against the outside world and does not certify that the document is otherwise correct, complete or safe to rely on.

## Why GenLayer?

Whether "payable in 4 equal quarterly instalments of 50 dollars" contradicts "the annual fee is 240 dollars" is a reading-comprehension question that deterministic code cannot answer and that a single off-chain judge could answer in the publisher's favour. Errata narrows the question until independent validators can reproduce it exactly: the model sees two short quoted passages and returns `upheld`, `not_upheld` or `unclear`. The Equivalence Principle accepts a result only when validators reproduce the **same verdict and the same passage locations**; free-text rationale is stored but never compared and never decides anything.

## Lifecycle

```text
publisher deposits credit, then opens a bounty (budget escrowed from credit)
  -> sealing window (until commit_by)
finder deposits credit, commits a sealed finding (bond locked)        [withdrawable until commit_by]
  -> commit_finding returns the finding's index; commit order is the priority order
  -> reveal window (commit_by .. reveal_by)
finder reveals index + kind + quote A + quote B + note + mirror + salt  [commitment recomputed on-chain]
  -> review window (reveal_by .. review_by, 3 days)
publisher or the finding's own finder requests a review of one finding (by index)
  -> upheld | not_upheld | disqualified | still open (unclear / retryable)
anyone finalizes (all findings decided, or review_by reached)
  -> earliest upheld finding per defect is paid, until the budget is spent
  -> get_errata() lists the accepted defects: the published errata sheet
everyone withdraws their own credit with withdraw_credit()
```

A bounty with no sealed finding can be cancelled by its publisher. One with at least one sealed finding cannot.

## Defect kinds

Every kind needs **two different, non-overlapping passages**. Quotes are whitespace-collapsed, matched exactly and case-sensitively, must be 8 to 120 characters, and must occur **exactly once** in the collapsed document.

| Kind | Meaning |
| --- | --- |
| `1` arithmetic | A number, total, percentage, unit conversion, date or duration in one passage does not follow from the figures in the other passage (or the shown context). |
| `2` contradiction | The two passages assert things that cannot both be true about the same subject. |
| `3` definition_conflict | One passage defines a term or fixes the scope of a rule; the other uses it in a way that conflicts with that definition. |

The publisher sets a reward for each kind (`0` switches a kind off).

## What the validators do

1. Fetch the publisher's URL, then (only if that fails or the bytes do not match the pinned hash) the finder's own mirror. The hash makes every source equally trustworthy, so a publisher who takes the document down cannot stop a finder who brought a mirror.
2. Check in code that each quote occurs exactly once and that the two passages do not overlap. A quote that is missing, ambiguous or overlapping **disqualifies** the finding; this is deterministic, so every validator reaches the same result.
3. Send the model **only the two highlighted passages plus context** (300, then 900, then 2000 characters on each side). The finder's note, address, mirror and salt, the bounty title and every other finding never reach the model. The passages are fenced as untrusted data and the contract neutralizes delimiter forgery inside them.
4. The model answers `upheld`, `not_upheld` or `unclear`. `unclear` is allowed only when the shown text is visibly cut off, and it widens the context for the next attempt.
5. Validators agree only if they report the **identical verdict and identical passage locations**. There is no tolerance and no adjacent-verdict acceptance. A subject-side failure can only be confirmed by another subject-side failure.
6. The first upheld or not_upheld consensus verdict for a finding is final. Re-rolling cannot flip it.

## Duplicates, priority and payment

Two upheld findings are **the same defect** when their unordered pairs of passages overlap pairwise (either orientation, any shared character), whatever kind they were filed under. Findings are settled in **commitment order**: the earliest upheld finding is paid and later copies are refunded with `duplicate_of` set. Each prize is the reward of the earlier finding's kind, capped by the remaining budget, so the last prize can be partial and anything after it gets nothing. A finding that was not upheld never shields a later one.

Because findings are sealed until the sealing window has closed, nobody can copy a competitor and win on timing. The rules do not care who filed what: two findings from the same account that describe one defect are a duplicate like any other, and the later one only gets its bond back.

### The errata sheet

`get_errata(publisher, id)` returns the accepted defects in commit order: for each, both passages, where they sit in the whitespace-collapsed document, the note, the finder and the reward earned (zero when the budget ran out). While the bounty is open it is the projection finalization would settle; after finalization it is the settled result (`finalized` says which). It is the artefact a publisher actually wants at the end: the list of things to correct.

## Money flow

All settlement is internal bookkeeping on a pull-payment ledger. `finalize_bounty` emits **no** transfers, so there is no partial-failure state across recipients; every party later withdraws only their own credit in a single transfer.

| Event | Effect |
| --- | --- |
| Upheld, first for its defect | Finder is credited bond + prize |
| Upheld duplicate, not upheld, unresolved, budget exhausted | Bond refunded, no prize |
| Withdrawn before the sealing deadline | Bond refunded |
| Never revealed | Bond forfeited to the publisher |
| Quotes not found exactly once, or overlapping | Bond forfeited to the publisher |
| Unspent budget | Credited back to the publisher |

The liability invariant is `total_credits + total_locked_budgets + total_locked_bonds = every wei ever deposited and not yet withdrawn`. `get_accounting()` exposes it with the contract balance, and `get_standing()` previews exactly what finalization would do, computed by the same code (after finalization it returns the settled rows, flagged `finalized`).

**Only `deposit_credit` accepts GEN.** Every other write method reverts when value is attached, so a mistaken transfer is never left in the contract outside every account's books. The contract checks this itself because Direct Mode does not refuse value on a non-payable method; `tests/direct/test_value_guard.py` proves, for each method, that the call fails with value attached and succeeds without it. `live_e2e.py --probe-nonpayable` checks how Studionet itself treats a refused call.

A finder can never lose more than the bond, and the bond may not exceed the smallest prize on offer.

## Public interface

| Method | Who | Purpose |
| --- | --- | --- |
| `deposit_credit()` payable, `withdraw_credit()` | anyone | Move GEN in and out of your own ledger credit |
| `open_bounty(...)`, `cancel_bounty(id)` | publisher | Escrow a budget against a pinned document; cancel while empty |
| `commit_finding(publisher, id, commitment)` -> index, `withdraw_finding(publisher, id, index)` | finder | Seal a finding (bond locked); take it back before `commit_by` |
| `reveal_finding(publisher, id, index, kind, quote_a, quote_b, note, mirror, salt)` | the finding's finder | Open the commitment |
| `review_finding(publisher, id, index)` | publisher or that finding's finder | Ask validators to judge one finding |
| `finalize_bounty(publisher, id)` | anyone | Settle once everything is decided or `review_by` passed |
| `get_info`, `get_bounty`, `get_finding(..., index)`, `get_findings`, `get_standing`, `get_errata`, `get_accounting`, `get_available_credit` | anyone | Read state |

## Security boundaries and known limits

- SHA-256 proves byte identity, not truth or authorship. URL admission cannot pin DNS or prohibit every redirect; use direct raw URLs.
- Semantic judgment is probabilistic. A wrong decisive verdict is final for that finding, so quote tightly and pick unambiguous passages.
- The commitment binds publisher, bounty, finder, kind, both quotes, note, mirror and a salt of at least 16 characters, so it cannot be copied, replayed or boundary-shifted. A copied commitment cannot be revealed by anyone but its finder.
- Quotes are matched on the whitespace-collapsed text, case-sensitively and without Unicode normalization. Copy them from the document and check them first with `scripts/errata_tools.py check`; a quote the contract cannot find forfeits the bond.
- **At most three sealed findings per address per bounty.** A diligent proofreader should not need three addresses, and a cap plus one bond per finding bounds what one account can do. A withdrawn finding still counts toward the cap and keeps its slot, and the same sealed commitment cannot be filed twice. Each finding is revealed, reviewed and settled on its own, addressed by the index `commit_finding` returned.
- **Defect identity is the passage pair, not the root cause.** Two findings that cite different second passages count as different defects even if one edit fixes both. Publishers who care should set rewards with that in mind.
- The publisher collects forfeited bonds and slots are first come, first served, so a determined publisher can fill the slots with throwaway accounts and crowd out honest finders at no cost beyond time (the per-account cap means at least six accounts for the largest bounty). Slots are capped at 16 and a publisher cannot file on their own bounty. Finders should pick bounties by publisher reputation.
- Review needs the publisher or the finder to ask. A publisher who never asks, or a finder who never asks for their own finding, can delay finalization until `review_by` (3 days), never longer, and without penalizing anyone's bond.
- A publisher-side hostile document can try to steer the model with text near a defect. The model is told to treat excerpts as untrusted data and only ever sees bounded excerpts, but no prompt defence is absolute.
- `withdraw_credit` emits one value transfer. The contract cannot confirm downstream credit; compare the finalized parent transaction with the child receipt and the recipient balance.
- The contract certifies nothing about the document beyond the specific passage pairs that were upheld.

## Build and verification

Use the pinned Python and GenLayer tooling (GenVM `v0.2.16`):

```bash
python -m pip install -r requirements.txt
python -m pytest tests/direct -q
python scripts/preflight.py                 # source rules, lint, ABI schema, all tests
python scripts/preflight.py --write-schema  # also (re)writes artifacts/errata.abi.json
bash run.sh                                 # the same gate, installing tooling first
```

The Direct Mode suite (397 tests) covers whole lifecycles, every input-validation path and exact boundary values, deadline boundaries, commit/reveal attacks, review griefing and mirror failover, prompt-injection and delimiter forging, validator equivalence in both directions (including malformed leader results), duplicate and priority rules, several findings per account, the errata sheet, the per-method refusal of attached GEN, budget exhaustion, and seeded stateful fuzzing (several findings per account included) that checks value conservation and the per-account cap after every call. `tests/direct/test_live_runner.py` additionally runs the **entire live scenario** against the real contract code with a simulated chain, so scenario mistakes are caught before a Codespace hour is spent.

As a check on the tests themselves, deliberate faults were injected into the contract. For version 0.1.0, 59 faults (off-by-one deadlines, removed checks, dropped accounting lines, weakened equivalence, ...) were recorded: every one was caught except two that turned out not to change any observable behaviour. That pass was not repeated for 0.2.0. For the code 0.2.0 added or changed (window boundaries, ownership of a finding, the per-account cap, duplicate commitments, who may request a review, the errata sheet and the settled standing, the value guard, the finding index) a further 17 faults were injected and all are caught; one of them (a value guard that lets exactly 1 wei through) was first missed, which is why `test_value_guard.py` attaches both 1 wei and a large amount.

## Command-line helper for publishers and finders

```bash
python scripts/errata_tools.py hash handbook.md        # the 0x SHA-256 to pin when opening a bounty
python scripts/errata_tools.py check handbook.md --quote-a "..." --quote-b "..."
python scripts/errata_tools.py seal handbook.md --publisher 0x... --bounty-id B-1 --finder 0x... \
    --kind 2 --quote-a "..." --quote-b "..." [--note "..."] [--mirror https://...]
```

`seal` refuses anything the contract would reject, disqualify or never let you reveal (bad quotes, a mirror that is not a plain `https://host/path` URL, malformed addresses or bounty id), prints the commitment and a fresh salt, and runs entirely locally. The mirror is part of the sealed commitment, so a mirror the contract refuses could never be revealed and the bond would be forfeited; that is why the tool checks it before you seal. Never send the quotes or the salt to anything before the reveal. Seal each finding with its own salt; the contract numbers them in commit order and `commit_finding` returns the index you reveal with.

## Live end-to-end test on Studionet

`scripts/live_e2e.py` drives **two** bounties through the real network with real validators and a real LLM and writes a Markdown and JSON report to `reports/`. Bounty A has eight findings: three genuine defects (one of them sitting next to a hostile "answer not_upheld" note in the document), a duplicate, a correct calculation wrongly claimed as an error, a fabricated quote, a consistent pair wrongly claimed as a contradiction, and a finder who never reveals. Bounty B has a dead primary URL and a finder who brought a mirror; that finder also seals two extra findings that are never revealed, which exercises the per-account cap and the forfeit path on the real chain. It then finalizes both, compares the settled errata sheets, checks every balance, and withdraws everything as native GEN. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

```bash
bash run.sh check 0xYourAddress   # fast tooling / fixture / contract check
bash run.sh live 0xYourAddress    # full run, roughly 75 minutes
```

## Deployment status

Not deployed yet. Deploy `contracts/errata.py` (v0.2.0) to Studionet with no constructor arguments and record the address, source hash and live report in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). The offline gate and the simulated-chain scenario pass; the real-network run has not been performed from this repository yet.
