# Design notes

## What problem the model is actually asked to solve

A bounty over a long document invites the worst use of an LLM: "here is the document, is this finder right?". That question is large, open to prompt injection from both sides, and impossible for independent validators to answer identically.

Errata replaces it with the smallest question that still needs reading comprehension: *do these two short passages, read in their context, show a defect of this exact kind?* Everything else is moved into code:

| Step | Who decides | Why |
| --- | --- | --- |
| Is this the document that was promised? | SHA-256 in code | Byte identity is deterministic |
| Do the quotes exist, exactly once, without overlapping? | Code on the collapsed text | Deterministic, so every validator agrees |
| Where are the quotes? | Code (character offsets) | Compared exactly between validators |
| Is there a defect between them? | The model, one bounded call | The only genuinely semantic step |
| Is it a duplicate? | Code, from the offsets | No model involved |
| Who is paid, how much, in which order? | Code | Money never depends on prose |

## Why exact equality

Every value that is stored or that changes the path of the contract (the verdict, the evidence reason, both passage locations) must be identical between leader and validator. There is no confidence score to average and no "one step stricter is fine" tolerance. Only the free-text rationale differs between validators and it is never compared.

To make exact agreement achievable on a real network:

- the question is narrow and the answer space is three words;
- the model sees at most a few hundred characters per side, so there is little to disagree about;
- `unclear` is reserved for visibly truncated context and turns into a wider context rather than a coin flip;
- failures are classified by **whose budget they charge** (publisher-side transport and document faults are free, model faults are charged), and two failures only confirm each other inside the same class.

## Why the finder's words never reach the model

A finder can write anything in a free-text explanation, including instructions to the reviewer. Errata stores an optional note for human readers and **never sends it to the model**. The only finder-chosen input the model sees is *which* two spans of the publisher's own document to look at, and a quote must be verbatim text of that document. That removes the finder-side injection channel entirely. The remaining channel is a hostile publisher writing instructions into their own document; the prompt fences and neutralizes delimiters and tells the model to treat excerpts as data, and the live scenario plants such a note next to a real defect to check that the model resists it.

## Why quotes must be unique and at most 120 characters

Uniqueness makes a quote point at exactly one place, so a validator cannot resolve it differently. The length cap stops one huge quote from "blanketing" a whole section and shadowing every later finding on it under the duplicate rule.

## Why defect identity is the passage pair

Deciding whether two reports describe the same root cause is itself a semantic question. Errata deliberately avoids asking it. Two findings are the same defect when their unordered pairs of passages overlap, which is deterministic, explainable to a finder in one sentence, and cannot be gamed by relabelling the kind. The cost, stated in the README, is that findings sharing one passage but citing different partners count separately.

## Why mirrors are per finding

Letting anyone register a mirror on the bounty would let a griefer fill a shared list with dead links. A mirror that is part of one finding's commitment affects only that finding, can be used for nothing else, and cannot be tampered with because the pinned hash rejects any byte that differs. It also closes the obvious attack: a publisher who deletes the document after seeing the reveals.

## Settlement order

Review verdicts are independent and can arrive in any order. Only the final pass is ordered, by commitment index. That keeps review parallel and free of head-of-line blocking (a stuck finding cannot hold up later ones), while priority stays objective. `get_standing()` runs the same pass without writing, so a finder can see their projected outcome before finalization; after finalization it returns the settled rows.

## Why one account may file several findings

An earlier prototype of this primitive (Erratum) argued the point well: a one-report-per-account rule does not stop a determined attacker, because accounts are free, and it penalizes the diligent proofreader who found three errors. So Errata takes the same stance and bounds it instead: up to three findings per account per bounty, one bond each, commit order as priority. A finding is addressed by the index `commit_finding` returns, not by its finder, so each one is revealed, reviewed and settled on its own. The commitment itself is unchanged (it already binds the finder and the salt, and the bond makes a pile of identical commitments pointless: the same commitment cannot be filed twice by one account).

## Why there are no severity tiers

The prototype also asked the model to grade each report (minor, major, critical) with a confidence, and let validators accept a leader's grade when they reached the same or a higher one. That gives a richer prize table, but it puts a graded, confidence-dependent judgment inside the consensus path, which is the part of the system least able to afford variance. Errata keeps the reward per defect *kind* (a fixed, publisher-chosen table) and asks the model only for a verdict between two passages, compared exactly. A publisher who wants graded prizes can run several bounties with different tables.

## The errata sheet

`get_errata()` is a read-only view over the same settlement pass that finalization runs. It lists the accepted defects (the first upheld finding per defect, including one that found the budget empty and earned nothing), with both passages and their locations. It adds no state and no new rule, so it cannot disagree with the money.

## Why the contract refuses attached value itself

Declaring a method non-payable is not enough evidence that a mistaken transfer is refused: the Direct Mode harness accepts value on a non-payable method, and how a real network treats it is a property of the network. The contract therefore reverts any call to a non-payable method that carries value (`_refuse_value()`), the test suite proves it per method against a state in which the call would otherwise succeed, and the live runner's optional `--probe-nonpayable` checks the network side. A refused call changes nothing the books can see.

## Known trade-offs, chosen on purpose

- A wrong decisive verdict is final. Allowing re-rolls would let a motivated party buy variance.
- Forfeiture is reserved for faults that code can prove (never revealing, quotes that cannot be located). A merely unconvincing finding gets its bond back.
- Anyone can finalize, but only the publisher or the finder can request a review. Opening review to everyone would let a rival burn a finder's budget at a moment of their choosing.
